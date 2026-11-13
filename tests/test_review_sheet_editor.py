"""Opaque project-state transport; project meaning stays outside the core."""
import base64
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / 'bin' / 'review-sheet'
TRANSPORT = ROOT / 'bin' / 'review_sheet_editor.js'
HARNESS = ROOT / 'tests' / 'review_sheet_editor_harness.cjs'


def bounded_loader_contract(tool):
  loader = importlib.machinery.SourceFileLoader('bounded_editor_loader',
                                               str(tool))
  spec = importlib.util.spec_from_loader(loader.name, loader)
  module = importlib.util.module_from_spec(spec)
  loader.exec_module(module)
  with tempfile.TemporaryDirectory() as temporary:
    folder = Path(temporary)
    manifest = json.dumps({'role': 'editor', 'kind': 'bounded',
      'version': '1.0.0', 'api': 1,
      'scripts': ['first.js', 'late.js']}).encode()
    (folder / 'editor.json').write_bytes(manifest)
    for body, total in [(b'x' * 600, 4096), (b'x' * 100, len(manifest) + 99)]:
      reads = []
      class Stream(io.BytesIO):
        def read(self, size=-1):
          assert 0 < size <= 257, \
            'editor loader must bound each materialized read'
          reads.append(size)
          return super().read(size)
      class File:
        def open(self, mode):
          assert mode == 'rb'
          return Stream(body)
        def read_bytes(self):
          raise AssertionError(
            'editor loader must bound each materialized read')
      def lookup(_folder, name):
        if name == 'editor.json':
          return folder / name
        assert name == 'first.js', \
          'editor loader must stop before later scripts'
        return File()
      with mock.patch.object(module, 'safe_file', side_effect=lookup):
        try:
          module.editor_bundle(folder, {'component_file': 256,
                                        'component': total})
        except module.ReviewError:
          pass
        else:
          raise AssertionError('oversized editor bundle accepted')
      assert len(reads) == 1, 'editor loader must stop before later scripts'


class EditorCliTests(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.config = self.root / 'config'
    self.bundle = self.root / 'editor'
    self.bundle.mkdir()
    (self.bundle / 'editor.json').write_text(json.dumps({
      'kind': 'fixture-editor', 'version': '1.0.0', 'api': 1,
      'role': 'editor', 'scripts': ['editor.js']}), encoding='utf8')
    (self.bundle / 'editor.js').write_text("""
ReviewSheet.registerEditor({kind:'fixture-editor',version:'1.0.0',api:1,
  create(config) {
    let raw=config.initial_state;
    return {snapshot:()=>raw,prepare(next) {
      const old=raw;
      return {snapshot:()=>next,commit(){raw=next;},rollback(){raw=old;}};
    }};
  }});
""", encoding='utf8')
    self.raw = ' {"kept":[false,0,"","\\u0061"],"hidden":{"id":"x"}}\n'
    self.desc = self.root / 'description.json'
    self.page = self.root / 'review.html'
    self.resolved = self.root / 'review.resolved.json'
    self.packet = self.root / 'results.json'
    self.description = {'review': 'editor-fixture', 'editor': {
      'kind': 'fixture-editor', 'path': 'editor',
      'source_identity': 'fixture:source-1',
      'parameters': {'revision': 1}, 'initial_state': self.raw},
      'items': [{'id': 'visible', 'media': []}]}
    self.write_description()

  def write_description(self):
    self.desc.write_text(json.dumps(self.description), encoding='utf8')

  def cli(self, *args, code=0):
    proc = subprocess.run([sys.executable, str(TOOL), *map(str, args)],
      cwd=self.root, env={**os.environ, 'XDG_CONFIG_HOME': str(self.config)},
      capture_output=True, text=True)
    self.assertEqual(proc.returncode, code, proc.stderr + proc.stdout)
    return proc

  def trust_and_build(self):
    trusted = json.loads(self.cli('editor', 'trust', self.bundle,
                                  '--yes').stdout)
    self.cli('build', self.desc, '--output', self.page)
    return trusted, json.loads(self.resolved.read_text('utf8'))

  def write_packet(self, frozen, raw=None):
    packet = {'schema_version': 2, 'review': 'editor-fixture',
      'resolved_sha256': hashlib.sha256(self.resolved.read_bytes()).hexdigest(),
      'answers': {}, 'project_state': {'schema_version': 1,
        'editor_sha256': frozen['editor']['sha256'],
        'data': self.raw if raw is None else raw}}
    self.packet.write_text(json.dumps(packet), encoding='utf8')
    return packet

  def payload(self):
    emitted = self.page.read_text('utf8').split(
      'window.REVIEW_SHEET_DATA=', 1)[1]
    value, end = json.JSONDecoder().raw_decode(emitted)
    self.assertTrue(emitted[end:].startswith(';\n'))
    return value

  def set_api(self, api):
    manifest_path = self.bundle / 'editor.json'
    manifest = json.loads(manifest_path.read_text('utf8'))
    manifest['api'] = api
    manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    script = self.bundle / 'editor.js'
    script.write_text(re.sub(r'api:\d', f'api:{api}',
                             script.read_text('utf8')), encoding='utf8')

  @staticmethod
  def browser_editor(editor):
    """The binding a page may carry: bundle identity, no local record."""
    return {**editor, 'bundle': {key: editor['bundle'][key] for key in
                                 ('kind', 'version', 'api', 'sha256')}}

  def test_page_and_factory_config_carry_no_local_filesystem_path(self):
    node = shutil.which('node')
    self.assertIsNotNone(node, 'Node is required for the cli-binding case')
    local = sorted({str(self.bundle), str(self.bundle.resolve()),
                    str(self.root), str(self.root.resolve()),
                    tempfile.gettempdir(),
                    os.path.realpath(tempfile.gettempdir())})
    for api in (1, 2):
      with self.subTest(api=api):
        self.set_api(api)
        trusted, frozen = self.trust_and_build()
        # The resolving side keeps the full local bundle record.
        bundle = frozen['editor']['bundle']
        self.assertEqual(bundle['path'], str(self.bundle.resolve()))
        self.assertEqual(bundle['files'], trusted['files'])
        self.assertIn('manifest', bundle)
        markup = self.page.read_text('utf8')
        for path in local:
          self.assertNotIn(path, markup,
                           'a generated page must not embed a local path')
        emitted = self.payload()['editor']
        self.assertEqual(set(emitted['bundle']),
                         {'kind', 'version', 'api', 'sha256'})
        self.assertEqual(emitted['bundle']['sha256'], trusted['sha256'])
        self.assertEqual(emitted['sha256'], frozen['editor']['sha256'],
                         'the editor digest still binds the full record')
        # The shipped transport hands the owner factory this emitted binding.
        proc = subprocess.run(
          [node, str(HARNESS), str(TRANSPORT), 'cli-binding',
           json.dumps(emitted), json.dumps(local)],
          capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)
        result = self.write_packet(frozen)
        imported = json.loads(self.cli(
          'import', self.packet, '--resolved', self.resolved).stdout)
        self.assertEqual(imported['project_state'], result['project_state'])

  def test_editor_scripts_refuse_nul_and_closing_script_markup(self):
    script = self.bundle / 'editor.js'
    original = script.read_bytes()
    for extra in (b'\n// </script>\n', b"\nconst s = '</ScRiPt';\n",
                  b"\nconst s = '\0';\n"):
      with self.subTest(extra=extra):
        script.write_bytes(original + extra)
        refused = self.cli('editor', 'trust', self.bundle, '--yes', code=2)
        self.assertIn('editor script contains NUL or closing script markup',
                      refused.stderr)
        self.assertEqual(json.loads(self.cli('editor', 'list').stdout), [])
    script.write_bytes(original + b"\nconst s = '<script>';\n")
    self.trust_and_build()
    self.assertEqual(self.page.read_text('utf8').count('</script>'), 1)

  def trust_file(self):
    return self.config / 'review-sheet' / 'trusted-editors.json'

  def listed(self):
    return json.loads(self.cli('editor', 'list').stdout)

  def test_trusting_again_keeps_one_row_and_list_reports_current_files(self):
    trusted, frozen = self.trust_and_build()
    first = trusted['sha256']
    self.write_packet(frozen)
    stored = self.trust_file().read_bytes()
    self.cli('editor', 'trust', self.bundle, '--yes')
    self.assertEqual(self.trust_file().read_bytes(), stored,
                     'trusting an unchanged bundle again must change nothing')
    self.assertEqual(self.trust_file().stat().st_mode & 0o777, 0o600)
    rows = self.listed()
    self.assertEqual([(row['sha256'], row['current']) for row in rows],
                     [(first, True)])
    self.assertEqual(rows[0]['path'], str(self.bundle.resolve()))
    # Changed files are a new identity. The earlier row stays until it is
    # untrusted, and the listing says it no longer matches the files.
    with (self.bundle / 'editor.js').open('a', encoding='utf8') as stream:
      stream.write('\n// reviewed fixture change\n')
    self.assertEqual([(row['sha256'], row['current']) for row in
                      self.listed()], [(first, False)])
    second = json.loads(self.cli('editor', 'trust', self.bundle,
                                 '--yes').stdout)['sha256']
    self.assertNotEqual(second, first)
    self.cli('editor', 'trust', self.bundle, '--yes')
    self.assertEqual([(row['sha256'], row['current']) for row in
                      self.listed()], [(first, False), (second, True)])
    saved = json.loads(self.trust_file().read_text('utf8'))['editors']
    self.assertTrue(all('current' not in row for row in saved),
                    'the listing must not write its derived field')
    # Results bound to the earlier bundle import while its row remains.
    self.cli('import', self.packet, '--resolved', self.resolved)
    self.cli('editor', 'untrust', first)
    self.assertEqual([(row['sha256'], row['current']) for row in
                      self.listed()], [(second, True)])
    revoked = self.cli('import', self.packet, '--resolved', self.resolved,
                       code=2)
    self.assertIn('editor trust revoked or changed', revoked.stderr)
    shutil.rmtree(self.bundle)
    self.assertEqual([(row['sha256'], row['current']) for row in
                      self.listed()], [(second, False)])

  def test_malformed_trust_file_is_refused_without_a_traceback(self):
    _, frozen = self.trust_and_build()
    self.write_packet(frozen)
    path = self.trust_file()
    good = json.loads(path.read_text('utf8'))
    row = good['editors'][0]
    def rows(*values):
      return json.dumps({'schema_version': 1, 'editors': list(values)})
    shapes = ['[]', '"text"', 'null', '7', '{"schema_version": 1}',
              '{"schema_version": 2, "editors": []}',
              '{"schema_version": 1, "editors": {}}',
              '{"schema_version": 1, "editors": "rows"}',
              rows('row'), rows(None), rows([]), rows(7), rows(row, 'row'),
              rows({**row, 'path': 5}), rows({**row, 'sha256': None}),
              rows({**row, 'kind': ['fixture-editor']}),
              rows({**row, 'version': 1}), rows({**row, 'role': True}),
              rows({key: value for key, value in row.items()
                    if key != 'role'}),
              '{broken', b'\xff\xfe{}']
    commands = [('editor', 'list'),
                ('editor', 'trust', self.bundle, '--yes'),
                ('editor', 'untrust', row['sha256']),
                ('build', self.desc, '--output', self.root / 'again.html'),
                ('import', self.packet, '--resolved', self.resolved)]
    for shape in shapes:
      before = shape if isinstance(shape, bytes) else shape.encode()
      path.write_bytes(before)
      for command in commands:
        with self.subTest(shape=shape, command=command[:2]):
          refused = self.cli(*command, code=2)
          self.assertIn('trusted-editors.json', refused.stderr)
          self.assertNotIn('Traceback', refused.stderr)
          self.assertEqual(len(refused.stderr.strip().splitlines()), 1)
          self.assertEqual(path.read_bytes(), before)
    self.assertFalse((self.root / 'again.html').exists())
    path.write_text(json.dumps(good), encoding='utf8')
    self.assertEqual([entry['current'] for entry in self.listed()], [True])
    self.cli('import', self.packet, '--resolved', self.resolved)

  def test_browser_editor_projection_keeps_frozen_binding_and_domain(self):
    payload = self.payload
    browser_editor = self.browser_editor

    marker = 'one-authoritative-editor-card-α🦉'
    self.description['editor']['parameters']['ui'] = {
      'cards': {'visible': marker + 'x'*8192}, 'full': [False, 0, '']}
    self.description['items'].append({'id': 'unannotated', 'media': []})
    for api in (1, 2):
      with self.subTest(api=api):
        manifest_path = self.bundle/'editor.json'
        manifest = json.loads(manifest_path.read_text('utf8'))
        manifest['api'] = api
        manifest_path.write_text(json.dumps(manifest), encoding='utf8')
        script = self.bundle/'editor.js'
        script.write_text(script.read_text('utf8').replace(
          'api:1', 'api:2') if api == 2 else script.read_text('utf8'),
          encoding='utf8')
        self.write_description()
        input_bytes = self.desc.read_bytes()
        _, frozen = self.trust_and_build()
        resolved_bytes = self.resolved.read_bytes()
        emitted = payload()
        self.assertNotIn('editor', emitted['description'],
          'browser payload must not duplicate the full raw editor subtree')
        self.assertEqual(emitted['description'], {key: value for key, value
          in frozen['description'].items() if key != 'editor'})
        self.assertEqual(emitted['editor'], browser_editor(frozen['editor']))
        self.assertEqual(frozen['description']['editor'],
                         self.description['editor'])
        self.assertEqual(emitted['editor']['parameters'],
                         self.description['editor']['parameters'])
        self.assertEqual(emitted['editor']['initial_state'], self.raw)
        self.assertEqual(emitted['editor']['visible_item_ids'],
                         ['visible', 'unannotated'])
        self.assertIn('hidden', emitted['editor']['initial_state'])
        self.assertNotIn('hidden', emitted['editor']['visible_item_ids'])
        self.assertEqual(emitted['resolved_sha256'],
                         hashlib.sha256(resolved_bytes).hexdigest())
        self.assertEqual(self.page.read_text('utf8').count(marker), 1)
        self.assertEqual(self.desc.read_bytes(), input_bytes)
        # Re-emitting a page does not project/mutate the persisted description.
        self.cli('build', self.desc, '--output', self.page)
        self.assertEqual(self.resolved.read_bytes(), resolved_bytes)
        self.assertEqual(payload()['editor'],
                         browser_editor(frozen['editor']))

    del self.description['editor']
    self.write_description()
    self.cli('build', self.desc, '--output', self.page)
    ordinary = payload()
    frozen = json.loads(self.resolved.read_text('utf8'))
    self.assertNotIn('editor', ordinary)
    self.assertEqual(ordinary['description'], frozen['description'])

  def test_trusted_editor_freezes_v2_binding_without_answer_scopes(self):
    trusted, frozen = self.trust_and_build()
    self.assertEqual(frozen['schema_version'], 2)
    self.assertEqual(frozen['scopes'], {})
    self.assertEqual(frozen['editor']['bundle']['sha256'], trusted['sha256'])
    self.assertEqual(frozen['editor']['initial_state'], self.raw)
    self.assertEqual(frozen['editor']['source_identity'], 'fixture:source-1')
    self.assertIn('hidden', frozen['editor']['initial_state'])
    self.assertNotIn('hidden', frozen['editor']['visible_item_ids'])
    self.assertIn('ReviewSheet.registerEditor', self.page.read_text('utf8'))

    original = json.loads(json.dumps(self.description))
    for field in ('source', 'parameters', 'baseline', 'visible', 'bundle'):
      with self.subTest(binding_field=field):
        self.description = json.loads(json.dumps(original))
        editor = self.description['editor']
        if field == 'source':
          editor['source_identity'] = 'fixture:source-2'
        elif field == 'parameters':
          editor['parameters']['revision'] = 2
        elif field == 'baseline':
          # Even semantically equal JSON with other raw bytes is new evidence.
          editor['initial_state'] += ' '
        elif field == 'visible':
          self.description['items'].append({'id': 'another', 'media': []})
        else:
          with (self.bundle / 'editor.js').open('a', encoding='utf8') as stream:
            stream.write('\n// distinct reviewed fixture bundle\n')
          self.cli('editor', 'trust', self.bundle, '--yes')
        self.write_description()
        self.cli('build', self.desc, '--output', self.page)
        changed = json.loads(self.resolved.read_text('utf8'))
        self.assertNotEqual(changed['editor']['sha256'],
                            frozen['editor']['sha256'])

  def test_api2_view_bundle_is_explicitly_bound_and_future_api_refused(self):
    _, state_only = self.trust_and_build()
    manifest_path = self.bundle / 'editor.json'
    manifest = json.loads(manifest_path.read_text('utf8'))
    manifest['api'] = 2
    manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    script = self.bundle / 'editor.js'
    script.write_text(script.read_text('utf8').replace('api:1', 'api:2'),
                      encoding='utf8')
    trusted, view = self.trust_and_build()
    self.assertEqual(view['editor']['api'], 2)
    self.assertEqual(view['editor']['bundle']['api'], 2)
    self.assertEqual(view['editor']['bundle']['sha256'], trusted['sha256'])
    self.assertNotEqual(view['editor']['sha256'],
                        state_only['editor']['sha256'])
    self.write_packet(view)
    result = json.loads(self.cli('import', self.packet,
                                '--resolved', self.resolved).stdout)
    self.assertEqual(result['project_state']['data'], self.raw)
    manifest['api'] = 3
    manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    before = manifest_path.read_bytes()
    self.cli('editor', 'trust', self.bundle, '--yes', code=2)
    self.assertEqual(manifest_path.read_bytes(), before)

  def test_trusted_script_newlines_bind_the_actual_HTML_parser_text(self):
    script = self.bundle/'editor.js'
    original = script.read_bytes().replace(b'\r\n',b'\n').replace(b'\r',b'\n')
    manifest_path = self.bundle/'editor.json'
    manifest = json.loads(manifest_path.read_text('utf8'))
    for api in (2,1):
      for newline in (b'\r\n',b'\r'):
        with self.subTest(api=api,newline=newline):
          manifest['api'] = api
          manifest_path.write_text(json.dumps(manifest),encoding='utf8')
          raw = original.replace(b'api:1',f'api:{api}'.encode()).replace(
            b'\n',newline)
          script.write_bytes(raw)
          trusted, frozen = self.trust_and_build()
          self.assertEqual(script.read_bytes(),raw)
          self.assertEqual(trusted['files']['editor.js'],
                           hashlib.sha256(raw).hexdigest())
          self.assertEqual(frozen['editor']['bundle']['sha256'],
                           trusted['sha256'])
          markup = self.page.read_bytes().decode('utf8')
          scripts = re.findall(r'<script>(.*?)</script>',markup,re.S)
          self.assertEqual(len(scripts),1)
          emitted = scripts[0].replace('\r\n','\n').replace('\r','\n')
          expected = base64.b64encode(
            hashlib.sha256(emitted.encode()).digest()).decode()
          self.assertTrue("script-src 'sha256-"+expected+"'" in markup,
            'script-src must bind HTML-normalized trusted script text')
          self.assertNotIn('\r',scripts[0])
          self.assertNotIn("'unsafe-inline'",markup)

  def test_api2_styles_are_trusted_bounded_and_use_the_host_CSP_hash(self):
    manifest_path = self.bundle/'editor.json'
    manifest = json.loads(manifest_path.read_text('utf8'))
    manifest.update(api=2, styles=['first.css','second.css'])
    manifest_path.write_text(json.dumps(manifest), encoding='utf8')
    script = self.bundle/'editor.js'
    script.write_text(script.read_text('utf8').replace('api:1','api:2'), 'utf8')
    first, second = self.bundle/'first.css', self.bundle/'second.css'
    first.write_bytes(b'.fixture-owner {color:rgb(1,2,3)}\r\n'
                      b'/* CRLF */\r/* lone CR */')
    second.write_text('.fixture-owner {background:rgb(4,5,6)}', 'utf8')
    trusted, frozen = self.trust_and_build()
    markup = self.page.read_text('utf8')
    styles = re.findall(r'<style>(.*?)</style>', markup, re.S)
    combined = (ROOT/'bin/review_sheet_page.css').read_text('utf8') + '\n' + \
      first.read_text('utf8') + '\n' + second.read_text('utf8')
    self.assertEqual(styles, [combined])
    hash_ = base64.b64encode(
      hashlib.sha256(combined.encode()).digest()).decode()
    self.assertIn("style-src 'sha256-"+hash_+"'", markup)
    self.assertNotIn("'unsafe-inline'", markup)
    for file in (first, second):
      self.assertEqual(trusted['files'][file.name],
                       hashlib.sha256(file.read_bytes()).hexdigest())
    self.write_packet(frozen)
    original = self.packet.read_bytes()
    first.write_text(first.read_text('utf8')+'/* changed */', 'utf8')
    self.cli('build', self.desc, '--output', self.page, code=2)
    _, changed = self.trust_and_build()
    self.assertNotEqual(changed['editor']['sha256'],frozen['editor']['sha256'])
    self.cli('import',self.packet,'--resolved',self.resolved,code=2)
    self.assertEqual(self.packet.read_bytes(),original)
    for styles in ('first.css',['first.css','first.css'],['../foreign.css'],
                   ['editor.js']):
      with self.subTest(styles=styles):
        manifest['styles']=styles
        manifest_path.write_text(json.dumps(manifest),'utf8')
        self.cli('editor','trust',self.bundle,'--yes',code=2)
    manifest['styles']=['first.css'];manifest['api']=1
    manifest_path.write_text(json.dumps(manifest),'utf8')
    self.cli('editor','trust',self.bundle,'--yes',code=2)
    manifest['api']=2
    manifest_path.write_text(json.dumps(manifest),'utf8')
    for raw in (b'/* </StYlE> */',b'\xff',b'.fixture-owner{}\0'):
      first.write_bytes(raw)
      self.cli('editor','trust',self.bundle,'--yes',code=2)
    first.write_bytes(b'x'*1025)
    self.cli('editor','trust',self.bundle,'--yes')
    limited = self.cli('build',self.desc,'--output',self.page,
      '--max-component-file',1024,code=2)
    self.assertIn('editor bundle exceeds component limits',limited.stderr)
    first.write_bytes(b'x'*800)
    self.cli('editor','trust',self.bundle,'--yes')
    limited = self.cli('build',self.desc,'--output',self.page,
      '--max-component',1024,code=2)
    self.assertIn('editor bundle exceeds component limits',limited.stderr)
    first.unlink();first.symlink_to(second)
    self.cli('editor','trust',self.bundle,'--yes',code=2)

  def test_compact_transport_limit_does_not_budget_diagnostic_annotations(self):
    for api in (1, 2):
      manifest_path = self.bundle/'editor.json'
      manifest = json.loads(manifest_path.read_text('utf8'))
      manifest['api'] = api
      manifest_path.write_text(json.dumps(manifest), encoding='utf8')
      script = self.bundle/'editor.js'
      script.write_text(script.read_text('utf8').replace('api:1', f'api:{api}'),
                        encoding='utf8')
      _, frozen = self.trust_and_build()
      for prefix in ('', 'α🦉 \\u0061 "\r\n'):
        packet = self.write_packet(frozen, prefix)
        compact = lambda: json.dumps(packet, ensure_ascii=False,
          separators=(',', ':')).encode('utf8')
        packet['project_state']['data'] += 'x'*(1024-len(compact()))
        raw = compact()
        self.assertEqual(len(raw), 1024)
        self.packet.write_bytes(raw)
        result = json.loads(self.cli('import', self.packet, '--resolved',
          self.resolved, '--max-results', 1024).stdout)
        self.assertEqual(result['project_state']['data'],
          packet['project_state']['data'])
        self.assertEqual(result['project_validation'], 'not-run')
        self.assertEqual(self.packet.read_bytes(), raw)
        packet['project_state']['data'] += 'x'
        overflow = compact()
        self.assertEqual(len(overflow), 1025)
        self.packet.write_bytes(overflow)
        self.cli('import', self.packet, '--resolved', self.resolved,
                 '--max-results', 1024, code=2)
        self.assertEqual(self.packet.read_bytes(), overflow)

  def test_transport_keeps_opaque_utf8_and_refuses_completion_or_merge(self):
    _, frozen = self.trust_and_build()
    raw = 'Not domain JSON: α🦉 \\u0061\r\n'
    self.write_packet(frozen, raw)
    before = self.packet.read_bytes()
    result = json.loads(self.cli('import', self.packet,
                                '--resolved', self.resolved).stdout)
    self.assertEqual(result['project_state']['data'], raw)
    self.assertEqual(result['project_validation'], 'not-run')
    self.assertEqual(result['answers'], {})
    self.cli('import', self.packet, '--resolved', self.resolved,
             '--require-complete', code=2)
    self.cli('import', self.packet, self.packet, '--resolved', self.resolved,
             code=2)
    self.assertEqual(self.packet.read_bytes(), before)

  def test_changed_binding_and_malformed_envelope_preserve_source_bytes(self):
    _, frozen = self.trust_and_build()
    original = self.write_packet(frozen)
    for changes in ({'schema_version': 9}, {'editor_sha256': '0' * 64},
                    {'data': []}, {'unsupported': True}):
      with self.subTest(changes=changes):
        packet = {**original, 'project_state': {
          **original['project_state'], **changes}}
        self.packet.write_text(json.dumps(packet), encoding='utf8')
        before = self.packet.read_bytes()
        self.cli('import', self.packet, '--resolved', self.resolved, code=2)
        self.assertEqual(self.packet.read_bytes(), before)
    self.packet.write_text(json.dumps({**original, 'schema_version': 1}),
                           encoding='utf8')
    self.cli('import', self.packet, '--resolved', self.resolved, code=2)
    self.write_packet(frozen)
    self.cli('import', self.packet, '--resolved', self.resolved,
             '--max-results', len(self.packet.read_bytes()) - 1, code=2)

  def test_editor_refuses_mixed_decisions_and_changed_or_revoked_bundle(self):
    bounded_loader_contract(TOOL)
    trusted, _ = self.trust_and_build()
    self.description['decisions'] = {'verdict': {'kind': 'boolean'}}
    self.write_description()
    self.cli('build', self.desc, '--output', self.root / 'mixed.html', code=2)
    self.assertFalse((self.root / 'mixed.html').exists())
    del self.description['decisions']
    self.write_description()
    self.cli('editor', 'untrust', trusted['sha256'])
    self.cli('build', self.desc, '--output', self.root / 'untrusted.html',
             code=2)
    self.assertFalse((self.root / 'untrusted.html').exists())
    self.cli('editor', 'trust', self.bundle, '--yes')
    with (self.bundle / 'editor.js').open('a', encoding='utf8') as stream:
      stream.write('\n// changed after reviewed trust\n')
    self.cli('build', self.desc, '--output', self.root / 'changed.html', code=2)
    self.assertFalse((self.root / 'changed.html').exists())


class EditorTransportTests(unittest.TestCase):
  def node_case(self, name):
    self.assertTrue(TRANSPORT.is_file(), 'Missing owning-editor transport API')
    node = shutil.which('node')
    self.assertIsNotNone(node,
                         'Node is required for editor transport contracts')
    proc = subprocess.run([node, str(HARNESS), str(TRANSPORT), name],
                          capture_output=True, text=True)
    self.assertEqual(proc.returncode, 0, proc.stderr + proc.stdout)

  def test_single_factory_snapshot_preserves_caller_and_factory_isolation(self):
    self.node_case('binding-snapshot')

  def test_failed_commit_restores_state_and_preserves_raw_recovery(self):
    self.node_case('commit')

  def test_rollback_failure_is_explicitly_unrecovered(self):
    self.node_case('rollback')

  def test_bad_transaction_lifecycle_and_stale_prepare_never_write(self):
    self.node_case('lifecycle')

  def test_persistence_failure_retains_unsaved_state_and_old_draft(self):
    self.node_case('persistence')

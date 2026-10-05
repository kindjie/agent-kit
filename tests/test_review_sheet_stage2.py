"""Stage 2 review-sheet contracts with generic, local fixtures."""
import hashlib
import base64
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import struct
import tempfile
import unittest
import zlib


TOOL = Path(__file__).resolve().parents[1] / 'bin' / 'review-sheet'


class Stage2Test(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    self.config = self.root / 'config'
    self.desc = self.root / 'description.json'
    self.page = self.root / 'review.html'
    self.packet = self.root / 'results.json'
    (self.root / 'sample.txt').write_text('fixture')
    self.description = {'review': 'generic', 'decisions': {
      'choice': {'kind': 'choice', 'options': ['yes', 'no']}},
      'items': [{'id': 'one', 'media': [{'kind': 'text',
        'src': 'sample.txt'}]}]}
    self.write_desc()

  def write_desc(self):
    self.desc.write_text(json.dumps(self.description))

  def run_tool(self, *args, code=0):
    env = {**os.environ, 'XDG_CONFIG_HOME': str(self.config)}
    result = subprocess.run([str(TOOL), *map(str, args)], cwd=self.root,
                            env=env, text=True, capture_output=True)
    self.assertEqual(result.returncode, code, result.stderr + result.stdout)
    return result

  def build(self, *args):
    self.run_tool('build', self.desc, '--output', self.page, *args)
    return json.loads(self.page.with_suffix('.resolved.json').read_text())

  def test_frozen_import_and_current_check_are_separate(self):
    resolved = self.build()
    frozen_path = self.page.with_suffix('.resolved.json')
    scope = resolved['scopes']['item:one:choice']
    packet = {'schema_version': 1, 'review': 'generic',
      'resolved_sha256': hashlib.sha256(frozen_path.read_bytes()).hexdigest(),
      'answers': {'item:one:choice': {'state': 'answered', 'value': 'yes',
        'digest': scope['digest'], 'media_hashes': scope['media']}}}
    packet_path = self.root / 'results.json'
    packet_path.write_text(json.dumps(packet))
    self.run_tool('import', packet_path, '--resolved', frozen_path)
    self.run_tool('import', packet_path, code=2)
    (self.root / 'sample.txt').write_text('changed')
    result = json.loads(self.run_tool('import', packet_path, '--resolved',
      frozen_path, '--check-current').stdout)
    self.assertEqual(result['answers']['item:one:choice']['state'], 'answered')
    self.assertEqual(result['currency']['status'], 'changed')

  def test_embedded_non_authority_requires_verified_bytes(self):
    self.description['items'][0]['media'][0]['mode'] = 'embed'
    self.write_desc()
    resolved = self.build()
    scope = resolved['scopes']['item:one:choice']
    packet = {'schema_version': 1, 'review': 'generic',
      'resolved_sha256': hashlib.sha256(
        self.page.with_suffix('.resolved.json').read_bytes()).hexdigest(),
      'answers': {'item:one:choice': {'state': 'answered', 'value': 'yes',
        'digest': scope['digest']}}}
    path = self.root / 'results.json'
    path.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', path, '--resolved',
      self.page.with_suffix('.resolved.json'), code=2).stdout)
    self.assertEqual(result['answers']['item:one:choice']['state'], 'invalid')
    packet['answers']['item:one:choice']['media_hashes'] = scope['media']
    path.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', path, '--resolved',
      self.page.with_suffix('.resolved.json')).stdout)
    self.assertEqual(result['answers']['item:one:choice']['evidence'],
                     'verified')

  def test_trust_refusal_and_exact_digest(self):
    folder = self.root / 'component'
    folder.mkdir()
    (folder / 'component.json').write_text(json.dumps({
      'kind': 'text', 'version': '1.0.0', 'api': 1,
      'workers': {}, 'wasm': {}}))
    (folder / 'component.js').write_text("ReviewSheet.register({kind:'text',"
      "version:'1.0.0',api:1,render(){return document.createElement('pre')}})")
    self.description['components'] = {'text': 'component'}
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page, code=2)
    trust = self.run_tool('component', 'trust', folder)
    self.assertIn('sha256', trust.stdout)
    self.build()
    (self.root / 'linked').symlink_to(self.root, target_is_directory=True)
    self.description['components'] = {'text': 'linked/component'}
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page, code=2)
    self.description['components'] = {'text': 'component'}
    self.write_desc()
    (folder / 'component.js').write_text((folder / 'component.js').read_text()
      + '\n// changed')
    self.run_tool('build', self.desc, '--output', self.page, code=2)
    (folder / 'component.js').write_text("ReviewSheet.register({kind:'text',"
      "version:'1.0.0',api:1,keys:{j:'collision'},"
      "render(){return document.createElement('pre')}})")
    self.run_tool('component', 'trust', folder)
    self.run_tool('build', self.desc, '--output', self.page, code=2)
    trust_path = self.config / 'review-sheet' / 'trusted-components.json'
    self.assertEqual(trust_path.stat().st_mode & 0o777, 0o600)

  def test_untrusted_shadow_blocks_builtin_and_explicit_path_wins(self):
    subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
    source = TOOL.parent / 'review_sheet_components' / 'text'
    repo_component = self.root / '.review-sheet' / 'components' / 'text'
    shutil.copytree(source, repo_component)
    self.run_tool('build', self.desc, '--output', self.page,
                  '--allow-tracked', code=2)

    trusted = json.loads(self.run_tool('component', 'trust',
                                      repo_component).stdout)
    resolved = self.build('--allow-tracked')
    self.assertEqual(resolved['components']['text']['path'],
                     str(repo_component.resolve()))
    explicit = self.root / 'explicit'
    shutil.copytree(source, explicit)
    (explicit / 'README.md').write_text('different reviewed bundle')
    self.description['components'] = {'text': 'explicit'}
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page,
                  '--allow-tracked', code=2)
    self.run_tool('component', 'trust', explicit)
    resolved = self.build('--allow-tracked')
    self.assertEqual(resolved['components']['text']['path'],
                     str(explicit.resolve()))
    self.run_tool('component', 'untrust', trusted['sha256'])
    del self.description['components']
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page,
                  '--allow-tracked', code=2)

  def test_component_manifest_and_declared_asset_refusals(self):
    folder = self.root / 'component'
    folder.mkdir()
    manifest = {'kind': 'text', 'version': '1.0.0', 'api': 1}
    script = ("ReviewSheet.register({kind:'text',version:'1.0.0',api:1,"
              "render(){return document.createElement('pre')}})")
    for change, source in (
        ({'api': 2}, script),
        ({'version': '2.0.0'}, script),
        ({}, script + script),
        ({'workers': {'bad': '../sample.txt'}}, script),
        ({'wasm': {'missing': 'missing.wasm'}}, script)):
      with self.subTest(change=change, source=source):
        (folder / 'component.json').write_text(json.dumps(
          {**manifest, **change}))
        (folder / 'component.js').write_text(source)
        self.run_tool('component', 'trust', folder, code=2)
    self.assertFalse((self.config / 'review-sheet' /
                      'trusted-components.json').exists())

  def test_component_kind_cannot_escape_lookup_roots(self):
    local_bin = self.root / 'bin'
    local_bin.mkdir()
    local_tool = local_bin / 'review-sheet'
    shutil.copy2(TOOL, local_tool)
    for name in ('review_sheet_page.js', 'review_sheet_page.css'):
      shutil.copy2(TOOL.parent / name, local_bin / name)
    (local_bin / 'review_sheet_components').mkdir()
    untrusted = local_bin / 'untrusted'
    untrusted.mkdir()
    (untrusted / 'component.json').write_text(json.dumps({
      'kind': '../untrusted', 'version': '1.0.0', 'api': 1}))
    (untrusted / 'component.js').write_text(
      "ReviewSheet.register({kind:'../untrusted',version:'1.0.0',api:1,"
      "render(media,api){api.ready();return document.createElement('p')}})")
    self.description['items'][0]['media'][0]['kind'] = '../untrusted'
    self.write_desc()
    escaped = subprocess.run([str(local_tool), 'build', str(self.desc),
      '--output', str(self.page)], env={**os.environ,
      'XDG_CONFIG_HOME': str(self.config)}, text=True, capture_output=True)
    self.assertEqual(escaped.returncode, 2, escaped.stderr + escaped.stdout)
    for kind in ('../text', '/text', 'a/b', 'a\\b', '.', '..'):
      with self.subTest(kind=kind):
        self.description['items'][0]['media'][0]['kind'] = kind
        self.write_desc()
        result = self.run_tool('build', self.desc, '--output', self.page,
                               code=2)
        self.assertIn('component kind must be a simple name', result.stderr)

  def test_presets_merge_delete_explain_and_save_allowlist(self):
    presets = self.root / '.review-sheet' / 'presets'
    presets.mkdir(parents=True)
    (presets / 'base.json').write_text(json.dumps({'preset_version': '1',
      'instructions': 'Base', 'decisions': {'old': {'kind': 'boolean'},
      'choice': {'kind': 'choice', 'options': ['a', 'b']}}}))
    (presets / 'child.json').write_text(json.dumps({'extends': ['base'],
      'decisions': {'old': None}, 'instructions': 'Child'}))
    self.description['extends'] = ['child']
    self.write_desc()
    explain = json.loads(self.run_tool('build', self.desc, '--output',
      self.page, '--explain').stdout.splitlines()[-1])
    resolved = json.loads(self.page.with_suffix('.resolved.json').read_text())
    self.assertNotIn('old', resolved['description']['decisions'])
    self.assertEqual(len(resolved['presets']), 2)
    self.assertTrue(any(d['path'] == 'decisions.old' for d in
                        explain['deleted']))
    saved = json.loads(self.run_tool('preset', 'save', 'reusable', '--from',
      self.desc).stdout)
    self.assertNotIn('items', saved)
    self.assertNotIn('review', saved)
    self.description['unknown_field'] = 'refuse'
    self.write_desc()
    self.run_tool('preset', 'save', 'reusable', '--from', self.desc, code=2)
    del self.description['unknown_field']
    self.write_desc()
    (presets / 'base.json').write_text(json.dumps({'extends': ['child']}))
    self.run_tool('build', self.desc, '--output', self.page, code=2)

  def test_synthetic_comparison_size_and_frame_mapping(self):
    width, height = 400, 360
    rows = b''.join(b'\0' + bytes([x % 251 for x in range(width * 4)])
                    for _ in range(height))

    def chunk(kind, data):
      return struct.pack('>I', len(data)) + kind + data + struct.pack(
        '>I', zlib.crc32(kind + data))

    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack(
      '>IIBBBBB', width, height, 8, 6, 0, 0, 0)) +
      chunk(b'IDAT', zlib.compress(rows)) + chunk(b'IEND', b''))
    groups = []
    for case in range(2):
      names = [f'{case}-{name}' for name in ('ref', 'a', 'b', 'c')]
      items = []
      for item_id in names:
        media = []
        for view in ('front', 'side'):
          folder = self.root / 'frames' / item_id / view
          folder.mkdir(parents=True)
          for frame in (0, 1, 2):
            (folder / f'{frame}.png').write_bytes(png)
          frames = []
          for index, source in enumerate((0, 0, 1, 2, 2)):
            frames.append({'index': index, 'source_frame': source,
              'phase': 'start_hold' if index == 0 else
                'end_hold' if index == 4 else 'motion',
              'src': f'{source}.png',
              'sha256': hashlib.sha256(png).hexdigest()})
          (folder / 'index.json').write_text(json.dumps({
            'width': width, 'height': height, 'frames': frames}))
          media.append({'kind': 'frame-sequence', 'view': view,
            'src': str((folder / 'index.json').relative_to(self.root))})
        items.append({'id': item_id, 'media': media})
      groups.append({'id': f'case-{case}',
        'layout': 'synchronized-comparison', 'items': items,
        'pick': {'kind': 'best', 'required': False},
        'comparison': {'reference': names[0], 'candidates': names[1:],
          'views': ['front', 'side'], 'fps': [60, 1], 'motion_frames': 3,
          'hold_start': 1, 'hold_end': 1, 'frame_count': 5,
          'key_frames': [0, 2], 'crops': {'full': [0, 0, width, height],
            'detail': [0, height // 2, width, height // 2]}}})
    self.description = {'review': 'sized', 'groups': groups}
    self.write_desc()
    resolved = self.build()
    unique_frames = 2 * 4 * 2 * 3
    encoded = len(png) * unique_frames
    self.assertLessEqual(encoded, 128 * 1024 * 1024)
    self.assertLessEqual(self.page.stat().st_size, 256 * 1024 * 1024)
    self.assertLessEqual(32 * width * height * 4, 19 * 1024 * 1024)
    self.assertEqual(resolved['scopes']['group:case-0:pick']
                     ['definition']['options'],
                     ['0-a', '0-b', '0-c', 'tie', 'none'])
    # Two entries can reuse a source path, but the page embeds both sequences.
    duplicate = copy.deepcopy(groups[0]['items'][0]['media'][0])
    groups[0]['items'][0]['media'].append(duplicate)
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page,
                  '--max-embedded-total', str(encoded), code=2)
    groups[0]['items'][0]['media'].pop()
    self.write_desc()
    bad_index = self.root / groups[0]['items'][0]['media'][0]['src']
    bad = json.loads(bad_index.read_text())
    bad['frames'][2]['source_frame'] = 0
    bad_index.write_text(json.dumps(bad))
    self.run_tool('build', self.desc, '--output', self.page, code=2)

  def test_preset_top_level_deletion_and_whole_decision_replacement(self):
    base = self.root / 'base.json'
    base.write_text(json.dumps({'decisions': {
      'choice': {'kind': 'choice', 'options': ['a'], 'required': True}},
      'instructions': 'Old instructions'}))
    self.description.update({'extends': ['base.json'], 'decisions': None,
                             'instructions': None})
    self.write_desc()
    resolved = self.build()
    self.assertNotIn('decisions', resolved['description'])
    self.assertNotIn('instructions', resolved['description'])
    self.description['decisions'] = {'choice': {'kind': 'boolean'}}
    self.write_desc()
    resolved = self.build()
    self.assertEqual(resolved['description']['decisions']['choice'],
                     {'kind': 'boolean'})

  def test_nested_preset_extends_validation_and_final_authority_rules(self):
    base = self.root / 'base.json'
    self.description['extends'] = ['base.json']
    self.write_desc()
    for extends in ('verdict', {'verdict': True}, [False]):
      with self.subTest(extends=extends):
        base.write_text(json.dumps({'extends': extends}))
        self.run_tool('build', self.desc, '--output', self.page, code=2)
    for definition in (
        {'kind': 'choice', 'options': ['yes'], 'authority': True,
         'keys': 'a'},
        {'kind': 'boolean', 'authority': 'yes'},
        {'kind': 'boolean', 'default': True}):
      with self.subTest(definition=definition):
        base.write_text(json.dumps({'decisions': {'approval': definition}}))
        self.run_tool('build', self.desc, '--output', self.page, code=2)

  def test_frozen_source_and_preset_currency_survive_missing_inputs(self):
    self.description['extends'] = ['verdict']
    self.write_desc()
    resolved = self.build()
    frozen = self.page.with_suffix('.resolved.json')
    self.assertEqual(resolved['source']['sha256'],
                     hashlib.sha256(self.desc.read_bytes()).hexdigest())
    self.assertTrue(all(Path(row['path']).is_absolute() and
                        len(row['sha256']) == 64
                        for row in resolved['presets']))
    self.packet.write_text(json.dumps({'schema_version': 1,
      'review': 'generic', 'resolved_sha256': hashlib.sha256(
        frozen.read_bytes()).hexdigest(), 'answers': {}}))
    self.desc.unlink()
    result = json.loads(self.run_tool('import', self.packet, '--resolved',
                                    frozen, '--check-current').stdout)
    self.assertEqual(result['currency']['status'], 'missing')
    self.assertIn(str(self.desc.resolve()), result['currency']['missing'])

  def test_preset_save_refuses_overwrite_and_excludes_batch_identity(self):
    self.description.update({'privacy': 'private', 'context': {'batch': 1},
      'components': {'text': 'component'}, 'settings': {'gain': 0.15}})
    self.write_desc()
    self.run_tool('preset', 'save', 'reusable', '--from', self.desc,
                  '--write', '--scope', 'user')
    saved_path = self.config / 'review-sheet' / 'presets' / 'reusable.json'
    original = saved_path.read_bytes()
    self.assertEqual(set(json.loads(original)), {'decisions', 'settings'})
    self.run_tool('preset', 'save', 'reusable', '--from', self.desc,
                  '--write', '--scope', 'user', code=2)
    self.assertEqual(saved_path.read_bytes(), original)

  def test_reveal_revision_validation_and_packet_conflict(self):
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAf'
      'FcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL/nwAAAABJRU5ErkJggg==')
    items = []
    for item_id in ('ref', 'candidate'):
      folder = self.root / item_id
      folder.mkdir()
      (folder / '0.png').write_bytes(png)
      (folder / 'index.json').write_text(json.dumps({'width': 1,
        'height': 1, 'frames': [{'index': 0, 'source_frame': 0,
          'phase': 'motion', 'src': '0.png',
          'sha256': hashlib.sha256(png).hexdigest()}]}))
      items.append({'id': item_id, 'media': [{'kind': 'frame-sequence',
        'view': 'front', 'src': item_id + '/index.json'}]})
    self.description = {'review': 'reveal-fixture', 'groups': [{
      'id': 'case', 'layout': 'synchronized-comparison',
      'pick': {'kind': 'best'}, 'items': items,
      'comparison': {'reference': 'ref', 'candidates': ['candidate'],
        'views': ['front'], 'fps': [60, 1], 'motion_frames': 1,
        'hold_start': 0, 'hold_end': 0, 'frame_count': 1,
        'key_frames': [0], 'crops': {'full': [0, 0, 1, 1]}}}]}
    self.write_desc()
    resolved = self.build()
    frozen = self.page.with_suffix('.resolved.json')
    scope = resolved['scopes']['group:case:pick']
    packet = {'schema_version': 1, 'review': 'reveal-fixture',
      'resolved_sha256': hashlib.sha256(frozen.read_bytes()).hexdigest(),
      'answers': {'group:case:pick': {'state': 'answered',
        'value': 'candidate', 'digest': scope['digest'],
        'media_hashes': scope['media']}},
      'reveals': {'case': {'digest': scope['digest'], 'revealed': True,
        'first_reveal': {'sequence': 1, 'next_decision_revision': 1},
        'decision_revision': 1, 'event_sequence': 2,
        'revealed_before_decision': True}}}
    self.packet.write_text(json.dumps(packet))
    valid = json.loads(self.run_tool('import', self.packet, '--resolved',
      frozen).stdout)
    self.assertEqual(valid['reveals']['case']['']['decision_revision'], 1)
    invalid = copy.deepcopy(packet)
    invalid['reveals']['case']['revealed'] = False
    bad_path = self.root / 'bad.json'
    bad_path.write_text(json.dumps(invalid))
    result = json.loads(self.run_tool('import', bad_path, '--resolved',
      frozen, code=2).stdout)
    self.assertIn('reveal:case', result['invalid_entries'])
    invalid = copy.deepcopy(packet)
    invalid['reveals']['case']['first_reveal']['sequence'] = 2
    bad_path.write_text(json.dumps(invalid))
    self.run_tool('import', bad_path, '--resolved', frozen, code=2)
    other = copy.deepcopy(packet)
    other['reveals']['case']['decision_revision'] = 2
    other['reveals']['case']['event_sequence'] = 3
    bad_path.write_text(json.dumps(other))
    packet['reviewer'] = other['reviewer'] = 'alice'
    self.packet.write_text(json.dumps(packet))
    bad_path.write_text(json.dumps(other))
    merged = json.loads(self.run_tool('import', self.packet, bad_path,
      '--resolved', frozen).stdout)
    self.assertEqual(merged['reveals']['case']['alice'], other['reveals']['case'])
    self.assertEqual(merged['conflicts'], [])
    reversed_result = json.loads(self.run_tool('import', bad_path,
      self.packet, '--resolved', frozen).stdout)
    self.assertEqual(reversed_result['reveals'], merged['reveals'])
    independent = copy.deepcopy(packet)
    independent['reviewer'] = 'bob'
    independent['reveals']['case']['first_reveal'] = {
      'sequence': 2, 'next_decision_revision': 2}
    independent['reveals']['case']['decision_revision'] = 2
    independent['reveals']['case']['event_sequence'] = 3
    bad_path.write_text(json.dumps(independent))
    merged = json.loads(self.run_tool('import', self.packet, bad_path,
      '--resolved', frozen).stdout)
    self.assertEqual(set(merged['reveals']['case']), {'alice', 'bob'})
    divergent = copy.deepcopy(independent)
    divergent['reviewer'] = 'alice'
    bad_path.write_text(json.dumps(divergent))
    merged = json.loads(self.run_tool('import', self.packet, bad_path,
      '--resolved', frozen, code=2).stdout)
    self.assertEqual(merged['conflicts'][0]['scope'], 'reveal:case')


if __name__ == '__main__':
  unittest.main()

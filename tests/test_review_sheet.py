"""Public review-sheet contract tests using fictional local fixtures."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / 'bin' / 'review-sheet'
PAGE_CORE = TOOL.parent / 'review_sheet_page.js'
PAGE_CSS = TOOL.parent / 'review_sheet_page.css'


class ReviewSheetTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    (self.root / 'sample.txt').write_text('sample media', encoding='utf-8')
    self.desc = self.root / 'review.json'
    self.page = self.root / 'review.html'
    self.frozen = self.page.with_suffix('.resolved.json')
    self.packet = self.root / 'review-results.json'
    self.description = {
      'review': 'sample-1', 'title': 'Sample review',
      'instructions': 'Check the sample.', 'caveats': ['Demo only.'],
      'decisions': {
        'verdict': {'kind': 'choice', 'required': True,
                    'options': ['keep', 'revise'], 'keys': '12'},
        'note': {'kind': 'text'},
      },
      'groups': [{'id': 'set-a', 'title': 'Set A', 'layout': 'list',
                  'pick': {'kind': 'best', 'required': True},
                  'items': [{'id': 'one', 'fields': {'label': 'One'},
                             'evidence': {'revision': 1},
                             'media': [{'kind': 'text',
                                        'src': 'sample.txt'}]}]}],
    }
    self.write_desc()

  def write_desc(self):
    self.desc.write_text(json.dumps(self.description), encoding='utf-8')

  def run_tool(self, *args, code=0):
    proc = subprocess.run([str(TOOL), *map(str, args)], text=True,
                          capture_output=True, cwd=self.root)
    self.assertEqual(proc.returncode, code, proc.stderr + proc.stdout)
    return proc

  def build(self, *extra):
    self.run_tool('build', self.desc, '--output', self.page, *extra)
    return json.loads((self.root / 'review.resolved.json').read_text())

  def test_build_freezes_hashes_and_digests(self):
    resolved = self.build()
    media = resolved['description']['groups'][0]['items'][0]['media'][0]
    self.assertEqual(media['sha256'],
                     hashlib.sha256(b'sample media').hexdigest())
    self.assertEqual(media['mime'], 'text/plain')
    self.assertEqual(resolved['scopes']['item:one:verdict']['digest'].__len__(),
                     64)
    self.assertIn('Content-Security-Policy', self.page.read_text())
    self.assertNotIn('https://', self.page.read_text())

  def test_presentation_is_optional_validated_and_bound(self):
    item = self.description['groups'][0]['items'][0]
    baseline = self.build()
    self.assertNotIn('presentation', baseline['description']['groups'][0]
                     ['items'][0])
    for presentation in (None, [], {'unknown': []}, {'prompt': 'missing'},
                         {'before_media': 'label'},
                         {'before_media': [0]},
                         {'after_media': ['missing']},
                         {'before_media': ['label', 'label']},
                         {'prompt': 'label', 'after_media': ['label']},
                         {'before_media': ['label'],
                          'after_media': ['label']}):
      with self.subTest(presentation=presentation):
        item['presentation'] = presentation
        self.write_desc()
        self.run_tool('build', self.desc, '--output', self.page, code=2)
    item['presentation'] = {'prompt': 'label'}
    self.write_desc()
    changed = self.build()
    self.assertEqual(changed['schema_version'], 1)
    self.assertEqual(changed['description']['groups'][0]['items'][0]
                     ['presentation'], {'prompt': 'label'})
    key = 'item:one:verdict'
    self.assertNotEqual(baseline['scopes'][key]['digest'],
                        changed['scopes'][key]['digest'])
    item['presentation'] = {}
    self.write_desc()
    self.build()

  def test_still_comparison_binds_sibling_image(self):
    group = self.description['groups'][0]
    group['comparison'] = {'reference': 'one', 'candidates': ['two']}
    for index, item_id in enumerate(('one', 'two')):
      source = f'{item_id}.svg'
      (self.root / source).write_text('<svg/>')
      item = {'id': item_id, 'media': [{'kind': 'image', 'src': source,
                                     'mode': 'embed'}]}
      if index == 0:
        group['items'][0] = item
      else:
        group['items'].append(item)
    self.write_desc()
    key = 'item:one:verdict'
    before = self.build()['scopes'][key]
    (self.root / 'two.svg').write_text('<svg width="10"/>')
    after = self.build()['scopes'][key]
    self.assertNotEqual(before['digest'], after['digest'])
    self.assertEqual(after['media']['two.svg'], hashlib.sha256(
      (self.root / 'two.svg').read_bytes()).hexdigest())
    del group['comparison']
    self.write_desc()
    before = self.build()['scopes'][key]
    (self.root / 'two.svg').write_text('<svg width="20"/>')
    self.assertEqual(before, self.build()['scopes'][key])

  def test_sibling_authority_embeds_comparison_image(self):
    group = self.description['groups'][0]
    group['comparison'] = {'reference': 'one', 'candidates': ['two']}
    for item_id in ('one', 'two'):
      (self.root / f'{item_id}.svg').write_text('<svg/>')
    group['items'][0] = {
      'id': 'one',
      'media': [{'kind': 'image', 'src': 'one.svg', 'mode': 'reference'}],
      'decisions': {'final': {'kind': 'boolean', 'authority': True}}}
    group['items'].append({'id': 'two', 'media': [
      {'kind': 'image', 'src': 'two.svg', 'mode': 'reference'}]})
    self.write_desc()
    items = self.build()['description']['groups'][0]['items']
    self.assertEqual([item['media'][0]['mode'] for item in items],
                     ['embed', 'embed'])
    del group['comparison']
    self.write_desc()
    items = self.build()['description']['groups'][0]['items']
    self.assertEqual(items[1]['media'][0]['mode'], 'reference')

  def test_inherited_authority_embeds_only_first_sibling_image(self):
    group = self.description['groups'][0]
    group['comparison'] = {'reference': 'one', 'candidates': ['two']}
    verdict = self.description['decisions']['verdict']
    verdict['authority'] = True
    del verdict['keys']
    for name in ('one.svg', 'two.svg', 'extra.svg'):
      (self.root / name).write_text('<svg/>')
    group['items'][0] = {'id': 'one', 'media': [
      {'kind': 'image', 'src': 'one.svg', 'mode': 'reference'}]}
    group['items'].append({'id': 'two', 'media': [
      {'kind': 'image', 'src': 'two.svg', 'mode': 'reference'},
      {'kind': 'image', 'src': 'extra.svg', 'mode': 'reference'}],
      'decisions': {'verdict': {'kind': 'choice', 'required': True,
                                'options': ['keep', 'revise']}}})
    self.write_desc()
    items = self.build()['description']['groups'][0]['items']
    self.assertEqual([row['mode'] for row in items[1]['media']],
                     ['embed', 'reference'])

  def test_still_comparison_metadata(self):
    group = self.description['groups'][0]
    group['items'].append({'id': 'two'})
    for layout in ('list', 'grid'):
      group['layout'] = layout
      for candidates in (3, 'two', [['two']], [], ['one'], ['missing'],
                         ['two', 'two']):
        with self.subTest(layout=layout, candidates=candidates):
          group['comparison'] = {'reference': 'one', 'candidates': candidates}
          self.write_desc()
          result = self.run_tool('build', self.desc, '--output', self.page,
                                 code=2)
          self.assertIn('comparison reference/candidates', result.stderr)
      group['comparison'] = {'reference': 'one', 'candidates': ['two']}
      self.write_desc()
      self.build()

  def test_import_states_and_staleness(self):
    resolved = self.build()
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(), 'answers': {}}
    key = 'item:one:verdict'
    packet['answers'][key] = {
      'state': 'answered', 'value': 'keep',
      'digest': resolved['scopes'][key]['digest'],
      'media_hashes': resolved['scopes'][key]['media'],
    }
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', self.frozen).stdout)
    self.assertEqual(result['answers'][key]['state'], 'answered')
    self.assertEqual(result['answers']['item:one:note']['state'],
                     'unanswered')
    self.assertEqual(self.run_tool('import', self.packet,
                                   '--resolved', self.frozen,
                                   '--require-complete', code=3).returncode, 3)
    (self.root / 'sample.txt').write_text('changed', encoding='utf-8')
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', self.frozen,
                                     '--check-current').stdout)
    self.assertEqual(result['answers'][key]['state'], 'answered')
    self.assertEqual(result['currency']['status'], 'changed')

  def test_rejects_extends_escape_and_limits(self):
    self.description['extends'] = ['preset']
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page, code=2)
    del self.description['extends']
    self.description['groups'][0]['items'][0]['media'][0]['src'] = '../x.txt'
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page, code=2)
    self.description['groups'][0]['items'][0]['media'][0]['src'] = 'sample.txt'
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page,
                  '--max-items', '0', code=2)

  def test_private_tracked_output_refused(self):
    subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
    self.run_tool('build', self.desc, '--output', self.page, code=2)
    self.run_tool('build', self.desc, '--output',
                  self.root / 'new' / 'review.html', code=2)
    self.build('--allow-tracked')

  def test_authority_embeds_and_requires_browser_hash(self):
    self.description['decisions']['verdict']['authority'] = True
    del self.description['decisions']['verdict']['keys']
    self.write_desc()
    resolved = self.build()
    self.assertEqual(resolved['description']['groups'][0]['items'][0]
                     ['media'][0]['mode'], 'embed')
    key = 'item:one:verdict'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest']}}}
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', self.frozen,
                                     code=2).stdout)
    self.assertEqual(result['answers'][key]['state'], 'invalid')

  def test_embedded_media_obeys_byte_limit(self):
    self.description['decisions']['verdict']['authority'] = True
    del self.description['decisions']['verdict']['keys']
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page,
                  '--max-embedded-file', '4', code=2)
    self.assertFalse(self.page.exists())

  def test_scope_changes_and_merge_semantics(self):
    resolved = self.build()
    key = 'item:one:verdict'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest'],
                               'media_hashes': resolved['scopes'][key]['media'],
                               'time': 'first'}}}
    second = self.root / 'second.json'
    self.packet.write_text(json.dumps(packet))
    packet['answers'][key]['time'] = 'later'
    second.write_text(json.dumps(packet))
    self.run_tool('import', self.packet, second, '--resolved', self.frozen)
    packet['answers'][key]['value'] = 'revise'
    second.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet, second,
                                     '--resolved', self.frozen,
                                     code=3).stdout)
    self.assertEqual(result['conflicts'], [{
      'scope': key,
      'entries': [
        {'state': 'answered', 'reviewer': None, 'value': 'keep'},
        {'state': 'answered', 'reviewer': None, 'value': 'revise'}]}])
    self.assertEqual(result['answers'][key], {'state': 'conflict'})
    for change in ('evidence', 'fields', 'question', 'layout'):
      with self.subTest(change=change):
        if change == 'evidence':
          self.description['groups'][0]['items'][0]['evidence'][
            'revision'] += 1
        elif change == 'fields':
          self.description['groups'][0]['items'][0]['fields'][
            'label'] += '!'
        elif change == 'question':
          self.description['decisions']['verdict']['question'] = 'Keep it?'
        else:
          self.description['groups'][0]['layout'] = 'grid'
        self.write_desc()
        result = json.loads(self.run_tool('import', self.packet,
                                         '--resolved', self.frozen,
                                         '--check-current').stdout)
        self.assertEqual(result['answers'][key]['state'], 'answered')
        self.assertEqual(result['currency']['status'], 'changed')

  def test_orphan_invalid_and_answer_bounds(self):
    resolved = self.build()
    key = 'item:one:note'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'x' * 9,
                               'digest': resolved['scopes'][key]['digest']},
                          'item:one:unknown': {'state': 'answered',
                                               'value': True},
                          'item:gone:verdict': {'state': 'answered',
                                                'value': 'keep'}}}
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', self.frozen,
                                     '--max-text-answer', '8',
                                     code=2).stdout)
    self.assertEqual(result['answers'][key]['state'], 'invalid')
    self.assertEqual(result['orphaned'], ['item:gone:verdict'])
    self.assertEqual(result['invalid_entries'], ['item:one:unknown'])
    self.run_tool('import', self.packet, '--resolved', self.frozen,
                  '--max-results', '2', code=2)

  def test_orphan_alone_exits_invalid(self):
    self.build()
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': '0' * 64,
              'answers': {'item:gone:verdict': {'state': 'answered',
                                                'value': 'keep'}}}
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', self.frozen,
                                     code=2).stdout)
    self.assertEqual(result['orphaned'], ['item:gone:verdict'])

  def test_import_preserves_evidence_and_media_hashes(self):
    self.description['decisions']['verdict']['authority'] = True
    del self.description['decisions']['verdict']['keys']
    self.write_desc()
    resolved = self.build()
    key = 'item:one:verdict'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest'],
                               'evidence': 'verified',
                               'media_hashes': resolved['scopes'][key]
                               ['media']}}}
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', self.frozen).stdout)
    self.assertEqual(result['answers'][key]['evidence'], 'verified')
    self.assertEqual(result['answers'][key]['media_hashes'],
                     resolved['scopes'][key]['media'])

  def test_media_options_and_page_core_bind_scope_digest(self):
    first = self.build()['scopes']['item:one:verdict']['digest']
    media = self.description['groups'][0]['items'][0]['media'][0]
    media['fps'] = 24
    self.write_desc()
    second = self.build()['scopes']['item:one:verdict']['digest']
    self.assertNotEqual(first, second)
    copied = self.root / 'bin'
    copied.mkdir()
    for source in (TOOL, PAGE_CORE, PAGE_CSS):
      shutil.copy2(source, copied / source.name)
    shutil.copytree(TOOL.parent / 'review_sheet_components',
                    copied / 'review_sheet_components')
    previous = second
    for name, addition in [('review_sheet_page.js', b'\n// fixture\n'),
                           ('review_sheet_page.css', b'\n/* fixture */\n')]:
      page_core = copied / name
      page_core.write_bytes(page_core.read_bytes() + addition)
      output = self.root / 'copied.html'
      proc = subprocess.run([str(copied / 'review-sheet'), 'build',
                             str(self.desc), '--output', str(output)],
                            text=True, capture_output=True)
      self.assertEqual(proc.returncode, 0, proc.stderr)
      current = json.loads((self.root / 'copied.resolved.json')
                           .read_text())['scopes']['item:one:verdict']['digest']
      self.assertNotEqual(previous, current)
      previous = current

  def test_stale_copy_does_not_conflict_with_current_answer(self):
    resolved = self.build()
    key = 'item:one:verdict'
    entry = {'state': 'answered', 'value': 'keep',
             'digest': resolved['scopes'][key]['digest']}
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': '0' * 64, 'answers': {key: entry}}
    second = self.root / 'second.json'
    self.packet.write_text(json.dumps(packet))
    packet['answers'][key] = {**entry, 'digest': '0' * 64}
    second.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet, second,
                                     '--resolved', self.frozen,
                                     code=3).stdout)
    self.assertEqual(result['conflicts'], [])
    self.assertEqual(result['answers'][key]['state'], 'stale')

  def test_conflict_wins_when_a_third_copy_is_stale(self):
    resolved = self.build()
    key = 'item:one:verdict'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256((self.root /
                'review.resolved.json').read_bytes()).hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest'],
                               'media_hashes': resolved['scopes'][key]
                               ['media']}}}
    files = [self.root / f'result-{i}.json' for i in range(3)]
    files[0].write_text(json.dumps(packet))
    packet['answers'][key]['value'] = 'revise'
    files[1].write_text(json.dumps(packet))
    packet['answers'][key]['digest'] = '0' * 64
    packet['resolved_sha256'] = '0' * 64
    files[2].write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', *files,
                                     '--resolved', self.frozen,
                                     code=3).stdout)
    self.assertEqual(result['answers'][key], {'state': 'conflict'})
    self.assertEqual(result['conflicts'][0]['entries'][:2], [
      {'state': 'answered', 'reviewer': None, 'value': 'keep'},
      {'state': 'answered', 'reviewer': None, 'value': 'revise'}])

  def test_different_reviewers_merge_identical_answers(self):
    resolved = self.build()
    key = 'item:one:verdict'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256((self.root /
                'review.resolved.json').read_bytes()).hexdigest(),
              'reviewer': 'Reviewer A',
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest'],
                               'media_hashes': resolved['scopes'][key]
                               ['media']}}}
    second = self.root / 'second.json'
    self.packet.write_text(json.dumps(packet))
    packet['reviewer'] = 'Reviewer B'
    second.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet, second,
                                     '--resolved', self.frozen).stdout)
    self.assertEqual(result['answers'][key]['state'], 'answered')
    self.assertEqual(result['conflicts'], [])

  def test_different_reviewers_only_conflict_on_different_answers(self):
    resolved = self.build()
    key = 'item:one:verdict'
    packets = []
    for reviewer, answers in (
        ('A', {key: {'state': 'answered', 'value': 'keep',
                     'digest': resolved['scopes'][key]['digest'],
                     'media_hashes': resolved['scopes'][key]['media']}}),
        ('B', {})):
      path = self.root / f'{reviewer}.json'
      path.write_text(json.dumps({'schema_version': 1,
        'review': 'sample-1', 'resolved_sha256': hashlib.sha256((self.root /
          'review.resolved.json').read_bytes()).hexdigest(),
        'reviewer': reviewer, 'answers': answers}))
      packets.append(path)
    result = json.loads(self.run_tool('import', *packets,
      '--resolved', self.frozen).stdout)
    self.assertEqual(result['conflicts'], [])
    self.assertEqual(result['answers'][key]['state'], 'answered')
    self.assertEqual(result['answers']['item:one:note']['state'], 'unanswered')

  def test_frozen_mismatch_stales_inherited_answer(self):
    resolved = self.build()
    key = 'item:one:verdict'
    self.packet.write_text(json.dumps({'schema_version': 1,
      'review': 'sample-1', 'resolved_sha256': '0' * 64,
      'answers': {key: {'state': 'inherited', 'value': 'keep',
        'digest': resolved['scopes'][key]['digest']}}}))
    result = json.loads(self.run_tool('import', self.packet,
      '--resolved',
      self.root / 'review.resolved.json', code=3).stdout)
    self.assertEqual(result['answers'][key]['state'], 'stale')

  def test_build_refuses_input_output_aliases(self):
    for output in (self.desc, self.root / 'sample.txt',
                   self.root / 'review.resolved.json'):
      with self.subTest(output=output):
        original = output.read_bytes() if output.exists() else None
        self.run_tool('build', self.desc, '--output', output, code=2)
        if original is not None:
          self.assertEqual(output.read_bytes(), original)

  def test_status_seeds_conflict_in_fixed_order(self):
    self.packet.write_text(json.dumps({'review': 'sample-1',
      'answers': {'a': {'state': 'conflict'}}}))
    self.assertEqual(self.run_tool('status', self.packet).stdout,
      'sample-1: answered 0, unanswered 0, inherited 0, stale 0, '
      'conflict 1, invalid 0\n')

  def test_symlink_escape_and_schema(self):
    outside = self.root.parent / 'outside-sample.txt'
    # Symlink to an external path need not point to a real file: the
    # confinement check must reject it before opening.
    (self.root / 'escape.txt').symlink_to(outside)
    self.description['groups'][0]['items'][0]['media'][0]['src'] = 'escape.txt'
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page, code=2)
    schema = json.loads(self.run_tool('schema').stdout)
    self.assertEqual(schema['$defs']['group']['properties']['layout']['enum'],
                     ['list', 'grid', 'synchronized-comparison'])

  def test_hotkey_collision_rejected(self):
    self.description['decisions']['defects'] = {
      'kind': 'flags', 'options': ['blur']}
    self.write_desc()
    self.run_tool('build', self.desc, '--output', self.page, code=2)

  def test_false_zero_empty_and_status(self):
    self.description['decisions'] = {
      'approved': {'kind': 'boolean', 'required': True},
      'amount': {'kind': 'number', 'min': 0, 'max': 5},
      'note': {'kind': 'text'},
    }
    self.write_desc()
    resolved = self.build()
    answers = {}
    for name, value in [('approved', False), ('amount', 0), ('note', '')]:
      key = 'item:one:' + name
      answers[key] = {'state': 'answered', 'value': value,
                      'digest': resolved['scopes'][key]['digest'],
                      'media_hashes': resolved['scopes'][key]['media']}
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(), 'answers': answers}
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', self.frozen).stdout)
    for name, value in [('approved', False), ('amount', 0), ('note', '')]:
      self.assertEqual(result['answers']['item:one:' + name]['value'], value)
    summary = self.run_tool('status', self.packet).stdout
    self.assertIn('answered 3', summary)

  def test_authority_media_change_is_stale(self):
    self.description['decisions']['verdict']['authority'] = True
    del self.description['decisions']['verdict']['keys']
    self.write_desc()
    resolved = self.build()
    key = 'item:one:verdict'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest'],
                               'media_hashes': resolved['scopes'][key]
                               ['media']}}}
    self.packet.write_text(json.dumps(packet))
    (self.root / 'sample.txt').write_text('new bytes')
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', self.frozen,
                                     '--check-current').stdout)
    self.assertEqual(result['answers'][key]['state'], 'answered')
    self.assertEqual(result['currency']['status'], 'changed')

  def test_frozen_resolved_hash_checked(self):
    resolved = self.build()
    key = 'item:one:verdict'
    frozen = self.root / 'review.resolved.json'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(frozen.read_bytes())
              .hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest'],
                               'media_hashes': resolved['scopes'][key]
                               ['media']}}}
    self.packet.write_text(json.dumps(packet))
    self.run_tool('import', self.packet, '--resolved', frozen)
    packet['resolved_sha256'] = '0' * 64
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--resolved', frozen, code=3).stdout)
    self.assertEqual(result['answers'][key]['state'], 'stale')
    packet['answers'] = {}
    self.packet.write_text(json.dumps(packet))
    self.run_tool('import', self.packet, '--resolved', frozen, code=3)

  def test_distinct_pages_have_distinct_resolved_files(self):
    self.build()
    other = self.root / 'second.html'
    self.run_tool('build', self.desc, '--output', other)
    self.assertTrue((self.root / 'review.resolved.json').is_file())
    self.assertTrue((self.root / 'second.resolved.json').is_file())

  def test_missing_git_refuses_untracked_output(self):
    env = {**os.environ, 'PATH': '/nonexistent'}
    proc = subprocess.run(['/usr/bin/python3', str(TOOL), 'build',
                           str(self.desc), '--output', str(self.page)],
                          text=True, capture_output=True, env=env)
    self.assertEqual(proc.returncode, 2, proc.stderr)
    self.assertIn('Git', proc.stderr)

  def test_git_failure_is_not_treated_as_outside_worktree(self):
    fake_bin = self.root / 'fake-bin'
    fake_bin.mkdir()
    git = fake_bin / 'git'
    git.write_text('#!/bin/sh\necho "fatal: corrupt repository" >&2\n'
                   'exit 128\n')
    git.chmod(0o755)
    proc = subprocess.run(['/usr/bin/python3', str(TOOL), 'build',
                           str(self.desc), '--output', str(self.page)],
                          text=True, capture_output=True,
                          env={**os.environ, 'PATH': str(fake_bin)})
    self.assertEqual(proc.returncode, 2, proc.stderr)


if __name__ == '__main__':
  unittest.main()

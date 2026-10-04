"""Public review-sheet contract tests using fictional local fixtures."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


TOOL = Path(__file__).resolve().parents[1] / 'bin' / 'review-sheet'


class ReviewSheetTest(unittest.TestCase):
  def setUp(self):
    self.temp = tempfile.TemporaryDirectory()
    self.addCleanup(self.temp.cleanup)
    self.root = Path(self.temp.name)
    (self.root / 'sample.txt').write_text('sample media', encoding='utf-8')
    self.desc = self.root / 'review.json'
    self.page = self.root / 'review.html'
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
                             'media': [{'kind': 'text', 'src': 'sample.txt'}]}]}],
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
    self.assertEqual(media['sha256'], hashlib.sha256(b'sample media').hexdigest())
    self.assertEqual(resolved['scopes']['item:one:verdict']['digest'].__len__(),
                     64)
    self.assertIn('Content-Security-Policy', self.page.read_text())
    self.assertNotIn('https://', self.page.read_text())

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
    }
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--description', self.desc).stdout)
    self.assertEqual(result['answers'][key]['state'], 'answered')
    self.assertEqual(result['answers']['item:one:note']['state'],
                     'unanswered')
    self.assertEqual(self.run_tool('import', self.packet,
                                   '--description', self.desc,
                                   '--require-complete', code=3).returncode, 3)
    (self.root / 'sample.txt').write_text('changed', encoding='utf-8')
    result = json.loads(self.run_tool('import', self.packet,
                                     '--description', self.desc,
                                     code=3).stdout)
    self.assertEqual(result['answers'][key]['state'], 'stale')

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
                                     '--description', self.desc,
                                     code=2).stdout)
    self.assertEqual(result['answers'][key]['state'], 'invalid')

  def test_scope_changes_and_merge_semantics(self):
    resolved = self.build()
    key = 'item:one:verdict'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest'],
                               'time': 'first'}}}
    second = self.root / 'second.json'
    self.packet.write_text(json.dumps(packet))
    packet['answers'][key]['time'] = 'later'
    second.write_text(json.dumps(packet))
    self.run_tool('import', self.packet, second, '--description', self.desc)
    packet['answers'][key]['value'] = 'revise'
    second.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet, second,
                                     '--description', self.desc,
                                     code=3).stdout)
    self.assertEqual(result['conflicts'], [key])
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
                                         '--description', self.desc,
                                         code=3).stdout)
        self.assertEqual(result['answers'][key]['state'], 'stale')

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
                                     '--description', self.desc,
                                     '--max-text-answer', '8',
                                     code=2).stdout)
    self.assertEqual(result['answers'][key]['state'], 'invalid')
    self.assertEqual(result['orphaned'], ['item:gone:verdict'])
    self.assertEqual(result['invalid_entries'], ['item:one:unknown'])
    self.run_tool('import', self.packet, '--description', self.desc,
                  '--max-results', '2', code=2)

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
                     ['list', 'grid'])

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
                      'digest': resolved['scopes'][key]['digest']}
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(
                (self.root / 'review.resolved.json').read_bytes()
              ).hexdigest(), 'answers': answers}
    self.packet.write_text(json.dumps(packet))
    result = json.loads(self.run_tool('import', self.packet,
                                     '--description', self.desc).stdout)
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
                                     '--description', self.desc,
                                     code=3).stdout)
    self.assertEqual(result['answers'][key]['state'], 'stale')

  def test_frozen_resolved_hash_checked(self):
    resolved = self.build()
    key = 'item:one:verdict'
    frozen = self.root / 'review.resolved.json'
    packet = {'schema_version': 1, 'review': 'sample-1',
              'resolved_sha256': hashlib.sha256(frozen.read_bytes())
              .hexdigest(),
              'answers': {key: {'state': 'answered', 'value': 'keep',
                               'digest': resolved['scopes'][key]['digest']}}}
    self.packet.write_text(json.dumps(packet))
    self.run_tool('import', self.packet, '--description', self.desc,
                  '--resolved', frozen)
    packet['resolved_sha256'] = '0' * 64
    self.packet.write_text(json.dumps(packet))
    self.run_tool('import', self.packet, '--description', self.desc,
                  '--resolved', frozen, code=2)


if __name__ == '__main__':
  unittest.main()

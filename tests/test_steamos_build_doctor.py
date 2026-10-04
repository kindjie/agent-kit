"""Build plans and read-only doctor checks use local fixture devices only."""
import json
import runpy
import sys
import unittest

from tests import test_steamos
from tests import test_steamos_deploy


class BuildDoctorTest(unittest.TestCase):
  setUp = test_steamos.SteamosTest.setUp
  configure = test_steamos.SteamosTest.configure
  device_home = test_steamos.SteamosTest.device_home
  run_cli = test_steamos.SteamosTest.run_cli
  fixture_tool = test_steamos.SteamosTest.fixture_tool
  project = test_steamos_deploy.SteamosDeployTest.project
  write_config = test_steamos_deploy.SteamosDeployTest.write_config
  seal = test_steamos_deploy.SteamosDeployTest.seal

  def build_config(self, **changes):
    self.project()
    self.input_dir = self.repo / 'inputs'
    self.input_dir.mkdir(exist_ok=True)
    self.settings['build'] = {
      'command': [sys.executable, 'builder.py'],
      'env': {'INPUT_DIR': '${PROJECT}/inputs',
              'BUILD_SECRET_KEY': 'do-not-print'},
      'requires': ['INPUT_DIR']}
    self.settings['build'].update(changes)
    self.write_config()

  def test_build_dry_run_overlay_and_secret_redaction(self):
    self.build_config()
    alternate = self.repo / 'alternate'
    alternate.mkdir()
    overlay = (self.config_home / 'agent-kit' / 'steamos-projects' /
               'Demo1.json')
    overlay.parent.mkdir()
    overlay.write_text(json.dumps({'build': {'env': {
      'INPUT_DIR': '${PROJECT}/alternate'}}}))
    proc = self.run_cli('build', '--project', str(self.repo), '--dry-run',
                        '--json')
    plan = json.loads(proc.stdout)
    self.assertEqual(plan['env']['INPUT_DIR'], str(alternate))
    self.assertEqual(plan['env']['BUILD_SECRET_KEY'], '<redacted>')
    self.assertNotIn('do-not-print', proc.stdout)
    self.assertFalse((self.repo / 'builder.ran').exists())
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_build_requires_names_missing_environment(self):
    self.build_config(env={'INPUT_DIR': '${PROJECT}/missing'})
    proc = self.run_cli('build', '--project', str(self.repo), code=1)
    self.assertIn('INPUT_DIR', proc.stderr)
    self.assertIn('missing', proc.stderr)

  def test_build_executes_then_stages_and_reports_version(self):
    self.build_config(outputs=['build/bundle'])
    (self.repo / 'builder.py').write_text(
      'from pathlib import Path\n'
      'Path("builder.ran").write_text("yes")\n')
    expected = self.seal()
    proc = self.run_cli('build', '--project', str(self.repo), '--json')
    self.assertEqual(json.loads(proc.stdout)['version'], expected)
    self.assertEqual((self.repo / 'builder.ran').read_text(), 'yes')
    (self.bundle / 'run').write_text('changed')
    self.run_cli('build', '--project', str(self.repo), code=1)

  def test_build_redacts_child_output_and_checks_outputs(self):
    self.build_config(outputs=['build/bundle', 'missing-output'])
    (self.repo / 'builder.py').write_text(
      'import os\nprint(os.environ["BUILD_SECRET_KEY"])\n')
    proc = self.run_cli('build', '--project', str(self.repo), code=1)
    self.assertNotIn('do-not-print', proc.stdout + proc.stderr)
    self.assertIn('<redacted>', proc.stdout)
    self.assertIn('missing-output', proc.stderr)

  def test_overlay_rejects_other_fields_and_secret_missing_path(self):
    self.build_config(env={'BUILD_SECRET_KEY': '${PROJECT}/missing'},
                      requires=['BUILD_SECRET_KEY'])
    proc = self.run_cli('build', '--project', str(self.repo),
                        '--dry-run', code=1)
    self.assertNotIn(str(self.repo / 'missing'), proc.stdout + proc.stderr)
    overlay = (self.config_home / 'agent-kit' / 'steamos-projects' /
               'Demo1.json')
    overlay.parent.mkdir()
    overlay.write_text(json.dumps({'build': {'command': ['true']}}))
    self.run_cli('build', '--project', str(self.repo), '--dry-run', code=2)

  def test_config_rejects_bad_build_and_overlay(self):
    invalid = [
      {'command': 'echo hi'},
      {'command': ['sh', '-c', 'true']},
      {'command': []},
      {'env': {'A': '${PATH}'}},
      {'env': {'BAD-NAME': 'x'}},
      {'requires': ['NOT_DECLARED']},
      {'outputs': ['../escape']},
      {'outputs': ['build/bundle', 'build/bundle']},
      {'unexpected': True},
    ]
    for change in invalid:
      with self.subTest(change=change):
        self.build_config(**change)
        self.run_cli('build', '--project', str(self.repo),
                     '--dry-run', code=2)

  def test_doctor_no_project_is_read_only(self):
    proc = self.run_cli('doctor', '--device', 'unit', '--json')
    report = json.loads(proc.stdout)
    self.assertIn('checks', report)
    self.assertIn('devices', report)
    home = self.device_home()
    self.assertFalse((home / '.agent-kit-steamos-lease').exists())
    self.assertFalse((home / '.agent-kit-steamos-lease.log').exists())
    self.assertFalse((home / 'devkit-utils').exists())

  def test_doctor_reports_project_stage_and_missing_requires(self):
    self.build_config(env={'INPUT_DIR': '${PROJECT}/missing'})
    report = json.loads(self.run_cli('doctor', '--project', str(self.repo),
                                     '--json', code=1).stdout)
    self.assertTrue(any(c['status'] == 'fail' and 'INPUT_DIR' in c['message']
                        for c in report['checks']))
    self.assertTrue(any(c['name'] == 'stage' and c['status'] == 'ok'
                        for c in report['checks']))
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_doctor_reachability_failure_and_stale_stage_warning(self):
    self.project()
    (self.bundle / 'run').write_text('changed')
    self.env['FAKE_SSH_DOWN'] = '10.0.0.5 unit.local'
    report = json.loads(self.run_cli('doctor', '--project', str(self.repo),
                                     '--device', 'unit', '--json',
                                     code=1).stdout)
    self.assertTrue(any(c['name'] == 'stage' and c['status'] == 'warn'
                        for c in report['checks']))
    self.assertTrue(any(c['name'] == 'reachable' and c['status'] == 'fail'
                        for c in report['checks']))

  def test_doctor_glibc_space_docker_and_read_only_script(self):
    self.build_config()
    (self.repo / 'builder.py').write_text('# docker run\n')
    self.fixture_tool('docker', 'import sys\nsys.exit(1)')
    self.fixture_tool('readelf', "print('UND symbol@GLIBC_2.50')")
    self.fixture_tool('ldd', "print('ldd (GNU libc) 2.41')")
    self.fixture_tool('df', "print('Filesystem blocks used available')\n"
                            "print('disk 100 100 0')")
    report = json.loads(self.run_cli('doctor', '--project', str(self.repo),
                                     '--device', 'unit', '--json',
                                     code=1).stdout)
    statuses = {(c['scope'], c['name']): c['status']
                for c in report['checks']}
    self.assertEqual(statuses['local', 'docker-daemon'], 'fail')
    self.assertEqual(statuses['device:unit', 'glibc'], 'fail')
    self.assertEqual(statuses['device:unit', 'free-space'], 'fail')
    self.assertEqual(statuses['device:unit', 'governor-helper'], 'warn')
    self.assertFalse((self.device_home() / '.agent-kit-steamos-lease').exists())
    source = runpy.run_path(str(test_steamos.BIN.with_name(
      'steamos_doctor.py')))['DOCTOR_SCRIPT']
    for forbidden in ('sudo ', 'mkdir ', 'rm ', 'mv ', '>>'):
      self.assertNotIn(forbidden, source)

  def test_doctor_all_devices_and_compatible_bundle(self):
    self.project()
    self.configure({'devices': {
      'first': {'address': '10.0.0.5'},
      'second': {'address': '10.0.0.6'}}})
    self.fixture_tool('readelf', "print('UND symbol@GLIBC_2.30')")
    self.fixture_tool('ldd', "print('ldd (GNU libc) 2.41')")
    report = json.loads(self.run_cli('doctor', '--project', str(self.repo),
                                     '--all', '--json').stdout)
    self.assertEqual([item['name'] for item in report['devices']],
                     ['first', 'second'])
    for name in ('first', 'second'):
      checks = {c['name']: c['status'] for c in report['checks']
                if c['scope'] == f'device:{name}'}
      self.assertEqual(checks['glibc'], 'ok')
      self.assertEqual(checks['free-space'], 'ok')
    self.run_cli('doctor', '--all', '--device', 'first', code=2)


if __name__ == '__main__':
  unittest.main()

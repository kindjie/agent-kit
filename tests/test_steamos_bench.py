"""Benchmark controls use fake ssh, sysfs, sudo, taskset and perf only."""
import json
import os
from pathlib import Path
import signal
import runpy
import subprocess
import sys
import time
import unittest

from tests import test_steamos

BIN = test_steamos.BIN


class SteamosBenchTest(unittest.TestCase):
  setUp = test_steamos.SteamosTest.setUp
  configure = test_steamos.SteamosTest.configure
  device_home = test_steamos.SteamosTest.device_home
  run_cli = test_steamos.SteamosTest.run_cli
  fixture_tool = test_steamos.SteamosTest.fixture_tool
  age_lease = test_steamos.SteamosTest.age_lease

  def fixture(self):
    # Record the actual transport command independently of argv output.
    ssh = test_steamos.FAKE_SSH.replace(
      "root = os.environ['FAKE_SSH_ROOT']",
      "root = os.environ['FAKE_SSH_ROOT']\n"
      "with open(os.path.join(root, 'transport.log'), 'a') as f:\n"
      "  f.write(command + '\\n')")
    (self.root / 'bin/ssh').write_text(ssh)
    home = self.device_home()
    root = home / '.steamos-test-sysfs'
    self.env['STEAMOS_TEST_SYSFS'] = str(root)
    for cpu, governor in (('cpu0', 'schedutil'), ('cpu1', 'powersave')):
      path = root / 'devices/system/cpu' / cpu / 'cpufreq/scaling_governor'
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(governor + '\n')
    for name, value in (('type', 'Mains'), ('online', '1')):
      path = root / 'class/power_supply/AC' / name
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text(value + '\n')
    for relative in ('class/hwmon/hwmon0/temp1_input',
                     'class/thermal/thermal_zone0/temp'):
      path = root / relative
      path.parent.mkdir(parents=True, exist_ok=True)
      path.write_text('42000\n')
    self.fixture_tool('sudo', '''
      import json, os, pathlib, sys
      args = sys.argv[1:]
      home = pathlib.Path.home()
      with (home / 'sudo.log').open('a') as f:
        f.write(json.dumps(args) + '\\n')
      assert args[:2] == ['-n', os.environ.get('FAKE_HELPER',
        '/etc/agent-kit/steamos-governor')], args
      if os.environ.get('FAKE_SUDO_REFUSE'):
        print('sudo: a password is required', file=sys.stderr)
        sys.exit(1)
      cpu, governor = args[2:]
      if os.environ.get('FAKE_PIN_FAIL') and cpu == 'cpu1' and \
          governor == 'performance':
        sys.exit(1)
      if (os.environ.get('FAKE_RESTORE_FAIL') or
          os.environ.get('FAKE_RESTORE_CPU') == cpu) and \
          governor != 'performance':
        log = (home / 'sudo.log').read_text()
        if 'performance' in log:
          sys.exit(1)
      path = pathlib.Path(os.environ['STEAMOS_TEST_SYSFS']) / \
        'devices/system/cpu' / cpu / 'cpufreq/scaling_governor'
      path.write_text(governor + '\\n')
    ''')
    self.fixture_tool('taskset', '''
      import json, os, pathlib, sys
      args = sys.argv[1:]
      assert args[:2] == ['--cpu-list', '0-1'], args
      (pathlib.Path.home() / 'taskset.json').write_text(json.dumps(args))
      os.execvp(args[2], args[2:])
    ''')
    self.fixture_tool('perf', '''
      import json, os, pathlib, sys
      args = sys.argv[1:]
      assert args[:3] == ['stat', '-x,', '-o'], args
      pathlib.Path(args[3]).write_text('12,,cycles,1,100.00,\\n')
      assert args[4] == '--', args
      (pathlib.Path.home() / 'perf.json').write_text(json.dumps(args))
      os.execvp(args[5], args[5:])
    ''')
    self.fixture_tool('scp', '''
      import os, pathlib, shutil, sys
      remote, out = sys.argv[-2:]
      assert 'BatchMode=yes' in sys.argv
      with (pathlib.Path(os.environ['FAKE_SSH_ROOT']) / 'scp.log').open('a') as f:
        f.write(remote + '\\n')
      shutil.copyfile(remote.split(':', 1)[1], out)
    ''')
    self.run_cli('lease', 'take', 'fake benchmark')
    return root

  def governors(self):
    root = Path(self.env['STEAMOS_TEST_SYSFS']) / 'devices/system/cpu'
    return {p.parent.parent.name: p.read_text().strip()
            for p in root.glob('cpu*/cpufreq/scaling_governor')}

  def bench(self, *flags, command=None, code=0):
    out = self.root / ('results-' + str(time.time_ns()))
    command = command or ['python3', '-c',
                         'import sys; print("output"); '
                         'print("error", file=sys.stderr)']
    proc = self.run_cli('bench', 'run', '--json', '--out', str(out),
                        *flags, '--', *command, code=code)
    result = json.loads(proc.stdout)
    self.assertEqual(json.loads((out / 'summary.json').read_text())
                     ['exit_status'], result['exit_status'])
    return out, result, proc

  def assert_restored(self):
    self.assertEqual(self.governors(),
                     {'cpu0': 'schedutil', 'cpu1': 'powersave'})

  def bench_config(self, **settings):
    self.configure({'default': 'unit', 'devices': {
      'unit': {'address': '10.0.0.5', 'name': 'unit'}}, 'bench': settings})

  def test_custom_helper_and_invalid_config_before_ssh(self):
    self.fixture()
    self.env['FAKE_HELPER'] = '/etc/custom/governor'
    self.bench_config(governor_helper=self.env['FAKE_HELPER'])
    self.bench('--pin-governor')
    self.assert_restored()
    (self.root / 'hosts.log').unlink(missing_ok=True)
    for settings in ({'governor_helper': 'relative/helper'},
                     {'governor_helper': 7}, {'keep_runs': 0},
                     {'keep_runs': -1}, {'keep_runs': True},
                     {'keep_runs': 1.5}, {'keep_runs': '20'}):
      with self.subTest(settings=settings):
        self.bench_config(**settings)
        self.run_cli('bench', 'run', '--', 'true', code=2)
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_retention_default_and_custom_never_follow_symlinks(self):
    self.fixture()
    root = self.device_home() / '.agent-kit-steamos-bench'
    root.mkdir()
    outside = self.device_home() / 'outside'
    outside.mkdir()
    (outside / 'keep').write_text('preserved')
    (root / 'run-link').symlink_to(outside, target_is_directory=True)
    (root / 'run-file').write_text('preserved')
    (root / 'other').mkdir()
    for number in range(23):
      path = root / f'run-old-{number:02d}'
      path.mkdir()
      (path / 'nested-link').symlink_to(outside, target_is_directory=True)
      os.utime(path, (number + 1, number + 1))
    self.bench()
    def runs():
      return sorted(p.name for p in root.glob('run-*')
                    if p.is_dir() and not p.is_symlink())
    self.assertEqual(len(runs()), 20)
    self.assertNotIn('run-old-03', runs())
    self.assertIn('run-old-04', runs())
    self.bench_config(keep_runs=2)
    self.bench()
    self.assertEqual(len(runs()), 2)
    self.assertTrue((root / 'run-link').is_symlink())
    self.assertEqual((outside / 'keep').read_text(), 'preserved')
    self.assertEqual((root / 'run-file').read_text(), 'preserved')
    self.assertTrue((root / 'other').is_dir())

  def test_stdout_and_stderr_bounded_with_truncation_note(self):
    self.fixture()
    out, result, _ = self.bench(command=['python3', '-c',
      'import os; block = b"x" * (1024 * 1024); '
      '[(os.write(1, block), os.write(2, block)) for _ in range(65)]'])
    self.assertEqual(result['exit_status'], 0)
    for name in ('stdout.log', 'stderr.log'):
      path = out / name
      self.assertEqual(path.stat().st_size, 64 * 1024 * 1024)
      with path.open('rb') as stream:
        stream.seek(-100, 2)
        self.assertIn(b'truncated', stream.read())

  def test_restore_failure_part_way_still_restores_later_cpu(self):
    self.fixture()
    self.env['FAKE_RESTORE_CPU'] = 'cpu0'
    _, result, proc = self.bench('--pin-governor', code=1)
    self.assertFalse(result['restoration_ok'])
    self.assertEqual(self.governors(),
                     {'cpu0': 'performance', 'cpu1': 'powersave'})
    self.assertIn('failed to restore', proc.stderr)
    self.assertEqual(result['governors']['after'], self.governors())

  def test_heartbeat_timeout_and_closed_stdin_restore(self):
    self.fixture()
    script = runpy.run_path(str(BIN))['BENCH_SCRIPT']
    for close in (False, True):
      with self.subTest(closed_stdin=close):
        marker = self.device_home() / 'heartbeat-started'
        marker.unlink(missing_ok=True)
        request = dict(command=['python3', '-c',
          'from pathlib import Path; import time; '
          'Path("heartbeat-started").touch(); time.sleep(30)'],
          pin_governor=True, require_power=False, cpus=None,
          perf_stat=False, thermals=None, keep_runs=20,
          governor_helper='/etc/agent-kit/steamos-governor')
        proc = subprocess.Popen([sys.executable, '-c', script],
          cwd=self.device_home(),
          env=dict(self.env, HOME=str(self.device_home())),
          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
          stderr=subprocess.PIPE, text=True)
        try:
          proc.stdin.write(json.dumps(request) + '\n')
          proc.stdin.flush()
          deadline = time.monotonic() + 5
          while not marker.exists():
            if time.monotonic() > deadline or proc.poll() is not None:
              self.fail('heartbeat command never started')
            time.sleep(.02)
          if close:
            proc.stdin.close()
            proc.stdin = None
          proc.wait(timeout=8)
          stdout, stderr = proc.communicate(timeout=2)
          self.assertEqual(proc.returncode, 0, stderr)
          result = json.loads(stdout.splitlines()[-1])
          self.assertEqual(result['exit_status'], 128 + signal.SIGHUP)
          self.assertTrue(result['restoration_ok'])
          self.assert_restored()
        finally:
          if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=8)

  def test_restore_success_and_artifacts(self):
    self.fixture()
    out, result, _ = self.bench('--pin-governor', '--require-power',
                               '--cpus', '0-1', '--perf-stat',
                               '--thermals', '.05')
    self.assert_restored()
    self.assertTrue(result['restoration_ok'])
    self.assertEqual(result['governors']['during'],
                     {'cpu0': 'performance', 'cpu1': 'performance'})
    self.assertEqual(result['governors']['before'],
                     result['governors']['after'])
    self.assertTrue(result['power']['before']['external_power'])
    self.assertLessEqual(result['start_device_time'], result['end_device_time'])
    self.assertEqual((out / 'stdout.log').read_text(), 'output\n')
    self.assertEqual((out / 'stderr.log').read_text(), 'error\n')
    self.assertIn('cycles', (out / 'perf-stat.csv').read_text())
    thermal = (out / 'thermals.csv').read_text()
    self.assertIn('42000', thermal)
    self.assertIn('thermal_zone0', thermal)
    self.assertIn('hwmon0', thermal)
    self.assertTrue(Path(result['remote_directory']).is_dir())
    self.assertTrue((self.device_home() / 'taskset.json').exists())

  def test_failure_restores_and_returns_four(self):
    self.fixture()
    _, result, _ = self.bench('--pin-governor',
                              command=['python3', '-c', 'raise SystemExit(7)'],
                              code=4)
    self.assertEqual(result['exit_status'], 7)
    self.assert_restored()

  def test_no_pin_does_not_call_sudo(self):
    self.fixture()
    self.env['FAKE_SUDO_REFUSE'] = '1'
    self.bench()
    self.assert_restored()
    self.assertFalse((self.device_home() / 'sudo.log').exists())

  def test_benchmark_error_json_strips_connection_marker(self):
    self.fixture()
    self.env['FAKE_SUDO_REFUSE'] = '1'
    sudo = self.root / 'bin/sudo'
    sudo.write_text(sudo.read_text().replace(
      'sudo: a password is required', 'connected=1 sudo: password required'))
    _, result, proc = self.bench('--pin-governor', code=1)
    self.assertIn('password required', result['error'])
    self.assertNotIn('connected=1', result['error'])
    self.assertNotIn('connected=1', proc.stderr)

  def test_sudo_refusal_exact_command_and_no_command_run(self):
    self.fixture()
    self.env['FAKE_SUDO_REFUSE'] = '1'
    _, result, proc = self.bench('--pin-governor', code=1)
    self.assertIn('sudo -n /etc/agent-kit/steamos-governor cpu0 schedutil',
                  proc.stderr)
    self.assertIn('steamos.md', proc.stderr)
    self.assertIn('sudo: a password is required', proc.stderr)
    self.assertIsNone(result['exit_status'])
    self.assert_restored()

  def test_partial_pin_failure_restores_every_cpu(self):
    self.fixture()
    self.env['FAKE_PIN_FAIL'] = '1'
    _, result, _ = self.bench('--pin-governor', code=1)
    self.assertIsNone(result['exit_status'])
    self.assertTrue(result['restoration_ok'])
    self.assert_restored()

  def test_restore_failure_is_not_reported_as_success(self):
    self.fixture()
    self.env['FAKE_RESTORE_FAIL'] = '1'
    _, result, proc = self.bench('--pin-governor', code=1)
    self.assertFalse(result['restoration_ok'])
    self.assertIn('restore', proc.stderr)

  def test_command_failure_keeps_exit_four_if_restore_also_fails(self):
    self.fixture()
    self.env['FAKE_RESTORE_FAIL'] = '1'
    _, result, proc = self.bench('--pin-governor', command=[
      'python3', '-c', 'raise SystemExit(7)'], code=4)
    self.assertEqual(result['exit_status'], 7)
    self.assertFalse(result['restoration_ok'])
    self.assertIn('restore', proc.stderr)

  def test_perf_absent_runs_and_taskset_absent_refuses(self):
    self.fixture()
    # A closed PATH proves absence even if Linux provides system perf.
    tools = self.root / 'portable-tools'
    tools.mkdir()
    for name in ('python3', 'sh', 'cat', 'sed', 'head', 'tr', 'date', 'find'):
      (tools / name).symlink_to(test_steamos.shutil.which(name))
    for name in ('ssh', 'scp', 'sudo'):
      (tools / name).symlink_to(self.root / 'bin' / name)
    self.env['PATH'] = str(tools)
    out, result, proc = self.bench('--perf-stat')
    self.assertFalse(result['perf_stat'])
    self.assertIn('without perf', proc.stderr)
    self.assertFalse((out / 'perf-stat.csv').exists())
    _, result, proc = self.bench('--cpus', '0-1', '--pin-governor', code=1)
    self.assertIn('taskset', proc.stderr)
    self.assertIsNone(result['exit_status'])
    self.assertFalse((self.device_home() / 'sudo.log').exists())

  def test_thermal_trace_samples_throughout_run(self):
    self.fixture()
    out, _, _ = self.bench('--thermals', '.05', command=[
      'python3', '-c', 'import time; time.sleep(.2)'])
    lines = (out / 'thermals.csv').read_text().splitlines()
    self.assertGreaterEqual(len(lines), 7)

  def test_existing_output_preserved_before_ssh(self):
    out = self.root / 'existing'
    out.mkdir()
    (out / 'keep').write_text('existing result')
    self.run_cli('bench', 'run', '--out', str(out), '--', 'true', code=2)
    self.assertEqual((out / 'keep').read_text(), 'existing result')
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_power_refusal(self):
    root = self.fixture()
    (root / 'class/power_supply/AC/online').write_text('0\n')
    _, result, proc = self.bench('--require-power', '--pin-governor', code=1)
    self.assertIn('external power', proc.stderr)
    self.assertIsNone(result['exit_status'])
    self.assertFalse((self.device_home() / 'sudo.log').exists())

  def test_lease_required_and_expiry_refused(self):
    self.fixture()
    self.run_cli('bench', 'run', '--', 'true', holder='other', code=1)
    self.age_lease(5 * 3600)
    self.run_cli('bench', 'run', '--', 'true', code=1)
    self.assertFalse((self.device_home() / 'sudo.log').exists())

  def test_cpu_interval_and_command_validation_before_ssh(self):
    for cpus in ('', '-1', '2-1', '0,,1', '0;touch injected', '0-', '0:2',
                 '0-2,1', '1.5'):
      self.run_cli('bench', 'run', '--cpus', cpus, '--', 'true', code=2)
    for interval in ('0', '-1', 'nan', 'inf'):
      self.run_cli('bench', 'run', '--thermals', interval, '--', 'true', code=2)
    self.run_cli('bench', 'run', '--', code=2)
    self.run_cli('bench', 'run', 'true', code=2)
    self.assertFalse((self.root / 'hosts.log').exists())

  def test_argv_is_never_shell_evaluated(self):
    self.fixture()
    args = ['a b', '$(touch injected)', '`touch injected`',
            '; touch injected', "a'\"b", '', 'line\nnext', '--json']
    out, result, _ = self.bench(command=[
      'python3', '-c', 'import json,sys; print(json.dumps(sys.argv[1:]))',
      *args])
    self.assertEqual(json.loads((out / 'stdout.log').read_text()), args)
    self.assertEqual(result['command'][-len(args):], args)
    self.assertFalse((self.device_home() / 'injected').exists())
    transport = (self.root / 'transport.log').read_text()
    self.assertNotIn('touch injected', transport)
    self.assertNotIn('print(json.dumps(sys.argv[1:]))', transport)

  def test_missing_command_returns_four_with_copied_error(self):
    self.fixture()
    out, result, _ = self.bench('--pin-governor',
                                command=['/missing-benchmark'], code=4)
    self.assertEqual(result['exit_status'], 127)
    self.assertIn('/missing-benchmark', (out / 'stderr.log').read_text())
    self.assert_restored()

  def test_host_signal_closes_transport_and_restores(self):
    self.fixture()
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
      with self.subTest(signal=signum):
        marker = self.device_home() / 'started'
        marker.unlink(missing_ok=True)
        out = self.root / ('signal-' + str(signum))
        proc = subprocess.Popen([
          sys.executable, str(BIN), 'bench', 'run', '--pin-governor',
          '--json', '--out', str(out), '--', 'python3', '-c',
          'from pathlib import Path; import time; '
          'Path("started").touch(); time.sleep(30)'], env=self.env,
          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
          deadline = time.monotonic() + 5
          while not marker.exists() and proc.poll() is None:
            if time.monotonic() > deadline:
              self.fail('benchmark never started')
            time.sleep(.02)
          proc.send_signal(signum)
          stdout, stderr = proc.communicate(timeout=8)
          self.assertEqual(proc.returncode, 4, stdout + stderr)
          self.assertTrue(json.loads(stdout)['restoration_ok'])
          self.assert_restored()
        finally:
          if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=8)

  def test_overlapping_runs_refuse_without_interleaving_governors(self):
    self.fixture()
    out = self.root / 'first-run'
    proc = subprocess.Popen([
      sys.executable, str(BIN), 'bench', 'run', '--pin-governor',
      '--json', '--out', str(out), '--', 'python3', '-c',
      'from pathlib import Path; import time; '
      'Path("started").touch(); time.sleep(30)'], env=self.env,
      stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
      deadline = time.monotonic() + 5
      while not (self.device_home() / 'started').exists():
        if time.monotonic() > deadline or proc.poll() is not None:
          self.fail('first benchmark did not start')
        time.sleep(.02)
      root = self.device_home() / '.agent-kit-steamos-bench'
      directories = list(root.glob('run-*'))
      refused = self.root / 'refused'
      second = self.run_cli('bench', 'run', '--out', str(refused),
                            '--pin-governor', '--', 'true', code=1)
      self.assertEqual(list(root.glob('run-*')), directories)
      self.assertFalse(refused.exists())
      self.assertIn('another bench run', second.stderr)
      self.assertEqual(self.governors(),
                       {'cpu0': 'performance', 'cpu1': 'performance'})
      proc.send_signal(signal.SIGTERM)
      stdout, stderr = proc.communicate(timeout=8)
      self.assertEqual(proc.returncode, 4, stdout + stderr)
      self.assert_restored()
      # The retained lock file does not block the next invocation.
      self.bench('--pin-governor')
      self.assert_restored()
    finally:
      if proc.poll() is None:
        proc.kill()
        proc.communicate(timeout=8)

  def test_device_signals_restore_and_copy(self):
    self.fixture()
    # The command signals its device-side supervisor, not the ssh fake.
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
      with self.subTest(signal=signum):
        _, result, _ = self.bench('--pin-governor', command=[
          'python3', '-c', f'import os,signal,time; '
          f'os.kill(os.getppid(), {int(signum)}); time.sleep(10)'], code=4)
        self.assertEqual(result['exit_status'], 128 + signum)
        self.assert_restored()

  def test_disconnect_during_command_restores_without_retry(self):
    self.fixture()
    # Replace ssh only for the run: lease checks complete normally; the
    # benchmark transport disappears after pinning and starting COMMAND.
    self.fixture_tool('ssh', '''
      import os, pathlib, subprocess, sys, time
      args = sys.argv[1:]
      while args and args[0].startswith('-'):
        flag = args.pop(0)
        if flag in ('-o', '-i'): args.pop(0)
      home = pathlib.Path(os.environ['FAKE_SSH_ROOT']) / 'devices/10.0.0.5'
      child = subprocess.Popen(['sh', '-c', ' '.join(args[1:])],
        cwd=home, env=dict(os.environ, HOME=str(home)), stdin=subprocess.PIPE)
      if 'steamos-bench' not in args[-1]:
        child.communicate(sys.stdin.buffer.read())
        sys.exit(child.returncode)
      child.stdin.write(sys.stdin.buffer.readline()); child.stdin.flush()
      deadline = time.monotonic() + 5
      while not (home / 'started').exists():
        if time.monotonic() > deadline: raise RuntimeError('never started')
        time.sleep(.02)
      child.stdin.close()
      # Worker outlives the transport and finishes its own cleanup.
      sys.exit(255)
    ''')
    proc = self.run_cli('bench', 'run', '--pin-governor', '--',
                        'python3', '-c',
                        'from pathlib import Path; import time; '
                        'Path("started").touch(); time.sleep(30)', code=3)
    self.assertIn('dropped', proc.stderr)
    self.assertIn('run-', proc.stderr)
    self.assert_restored()
    summaries = list(self.device_home().glob(
      '.agent-kit-steamos-bench/run-*/summary.json'))
    self.assertEqual(len(summaries), 1)
    self.assertTrue(json.loads(summaries[0].read_text())['restoration_ok'])


if __name__ == '__main__':
  unittest.main()

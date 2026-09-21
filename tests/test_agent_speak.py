import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "bin" / "agent-speak.sh"

CODEX_PAYLOAD = '{"type":"turn-ended","last-assistant-message":"done"}'


class AgentSpeakOptionTests(unittest.TestCase):
  """Option handling, with speech captured rather than performed."""

  def setUp(self):
    self.tempdir = tempfile.TemporaryDirectory()
    self.root = Path(self.tempdir.name)
    self.mock_bin = self.root / "mock-bin"
    self.mock_bin.mkdir()
    self.spoken = self.root / "spoken.txt"
    # Shadow every speech backend so a regression is recorded, not heard.
    for tool in ("say", "spd-say", "espeak-ng"):
      path = self.mock_bin / tool
      path.write_text(
        f'#!/bin/sh\nprintf "{tool} %s\\n" "$*" >>"{self.spoken}"\n',
        encoding="utf-8",
      )
      path.chmod(0o755)
    self.addCleanup(self.tempdir.cleanup)

  def run_script(self, *args):
    env = dict(os.environ)
    env["PATH"] = f"{self.mock_bin}{os.pathsep}{env['PATH']}"
    # Keep mute state and the speech lock out of the real home.
    env["XDG_CONFIG_HOME"] = str(self.root / "config")
    env["TMPDIR"] = str(self.root)
    return subprocess.run(
      [str(SCRIPT), *args],
      capture_output=True,
      text=True,
      env=env,
      timeout=30,
    )

  def spoken_text(self):
    if not self.spoken.exists():
      return ""
    return self.spoken.read_text(encoding="utf-8")

  def test_unknown_option_errors_without_speaking(self):
    result = self.run_script("--codex", CODEX_PAYLOAD)
    self.assertEqual(result.returncode, 2)
    self.assertIn("unknown option", result.stderr)
    self.assertIn("--codex", result.stderr)
    self.assertEqual(self.spoken_text(), "")

  def test_unknown_option_never_speaks_a_json_payload(self):
    """A notify hook wired here must not read its payload aloud."""
    self.run_script("--codex", CODEX_PAYLOAD)
    self.assertNotIn("last-assistant-message", self.spoken_text())
    self.assertNotIn("turn-ended", self.spoken_text())

  def test_plain_message_is_spoken(self):
    result = self.run_script("needs a decision")
    self.assertEqual(result.returncode, 0)
    self.assertIn("needs a decision", self.spoken_text())

  def test_double_dash_allows_a_leading_dash_message(self):
    result = self.run_script("--", "--not an option")
    self.assertEqual(result.returncode, 0)
    self.assertIn("--not an option", self.spoken_text())

  def test_mute_round_trip_suppresses_speech(self):
    self.assertEqual(self.run_script("--mute").returncode, 0)
    status = self.run_script("--status")
    self.assertEqual(status.returncode, 1)
    self.assertIn("muted", status.stdout)

    self.run_script("silence please")
    self.assertEqual(self.spoken_text(), "")

    self.assertEqual(self.run_script("--unmute").returncode, 0)
    self.run_script("speak again")
    self.assertIn("speak again", self.spoken_text())

  def test_toggle_mute_flips_state(self):
    self.run_script("--toggle-mute")
    self.assertEqual(self.run_script("--status").returncode, 1)
    self.run_script("--toggle-mute")
    self.assertEqual(self.run_script("--status").returncode, 0)


if __name__ == "__main__":
  unittest.main()

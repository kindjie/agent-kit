"""Advisory location and repository diagnostics for changelog entries."""

from __future__ import annotations

import subprocess

from tests.agent_records_support import BIN, RecordsFixture


class ChangelogLocationTest(RecordsFixture):
  def entry(self, scope, location, *extra, slug="state"):
    result = subprocess.run(
      ["python3", str(BIN / "agent-changelog"), "--agent", "agent-a", "new",
       "--scope", scope, "--slug", slug, "--kind", "scratch",
       "--location", location, "--why", "needed",
       "--cleanup-when", "later", "--cleanup-how", "remove", *extra],
      cwd=self.repo, env=self.env, text=True, capture_output=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    return result.stdout.strip(), result.stderr

  def setUp(self):
    super().setUp()
    self.init()
    self.repo = self.base / "arenarch"
    self.repo.mkdir()
    subprocess.run(["git", "init", "-q", str(self.repo)],
                   env=self.env, check=True)

  def test_remote_nested_and_non_path_locations(self):
    import sys
    sys.path.insert(0, str(BIN))
    from agent_records_changelog import location_warning, parse_location
    self.assertEqual(parse_location("deck:~/game (installed build)"),
                     ["deck:~/game"])
    self.assertEqual(parse_location("build-host:/srv/out"),
                     ["build-host:/srv/out"])
    self.assertTrue(parse_location("/data/{a/{x,y},b}"))
    self.assertFalse(parse_location("/data/{a,b"))
    self.assertIsNone(location_warning("cloud project p1, region r1",
                                       "service"))
    self.assertIsNone(location_warning("local port 8000", "tool + service"))
    self.assertIsNotNone(location_warning("cloud project p1", "scratch"))

  def test_location_forms_and_writer_warnings(self):
    valid = (
      "~/git/arenarch-work (kept for review)",
      "/tmp/{a,b}; ./build/output",
      "~/git/a\n/var/tmp/b",
    )
    import sys
    sys.path.insert(0, str(BIN))
    from agent_records_changelog import parse_location
    self.assertEqual(parse_location("/tmp/{a,b}; ./build/output"),
                     ["/tmp/a", "/tmp/b", "./build/output"])
    for value in valid:
      with self.subTest(value=value):
        self.assertTrue(parse_location(value))
    for value in ("some prose", "~/git/a + ~/git/b", "~/git/a and ~/git/b",
                  "~/git/a -> ~/git/b", "~/git/a branch topic",
                  "~/git/{a,b", "~/git/a;/tmp/b", ""):
      with self.subTest(value=value):
        self.assertFalse(parse_location(value))
    entry, stderr = self.entry("arenarch", "prose", "--repo", "arenarch")
    self.assertIn("WARN", stderr)
    self.assertIn("location", stderr)
    self.assertIn("location: prose", (self.changes / entry).read_text())
    result = subprocess.run(
      ["python3", str(BIN / "agent-changelog"), "--agent", "agent-a",
       "update", entry, "--location", "~/git/a -> ~/git/b"],
      cwd=self.repo, env=self.env, text=True, capture_output=True)
    self.assertEqual(result.returncode, 0, result.stderr)
    self.assertIn("WARN", result.stderr)

  def test_lint_warns_without_failing(self):
    self.entry("arenarch", "~/git/arenarch-work", "--repo", "arenarch")
    entry, _ = self.entry("arenarch", "prose", slug="other")
    path = self.changes / entry
    path.write_text(path.read_text().replace("repos: arenarch", "repos: "))
    absolute, _ = self.entry("arenarch", "./build", slug="absolute")
    absolute_path = self.changes / absolute
    absolute_path.write_text(absolute_path.read_text().replace(
      "repos: arenarch", "repos: /opt/fictional/arenarch"))
    result = subprocess.run(
      ["python3", str(BIN / "agent-changelog"), "lint"], cwd=self.repo,
      env=self.env, text=True, capture_output=True)
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertIn("WARN", result.stdout)
    self.assertIn("unparseable location", result.stdout)
    self.assertIn("empty repos", result.stdout)
    self.assertIn("absolute path in repos", result.stdout)

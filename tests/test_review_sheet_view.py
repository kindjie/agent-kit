"""API2 owner views use explicit roots and the host's single lifecycle."""
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class EditorViewTests(unittest.TestCase):
  def check_case(self, name):
    node = shutil.which('node')
    self.assertIsNotNone(node, 'Node is required for editor view contracts')
    result = subprocess.run([node,
      str(ROOT / 'tests' / 'review_sheet_view_harness.cjs'),
      str(ROOT / 'bin' / 'review_sheet_page.js'), name],
      capture_output=True, text=True)
    self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

  def test_media_status_skips_empty_primitive_scopes_without_skipping_readiness(self):
    self.check_case('media-status')

  def test_all_alerts_live_in_flow_after_sticky_controls(self):
    self.check_case('messages-flow')

  def test_mount_uses_restored_owner_and_explicit_item_media_roots(self):
    self.check_case('mount')

  def test_one_owner_first_keyboard_dispatch_preserves_editable_escape(self):
    self.check_case('key')

  def test_focus_and_visibility_validate_bound_item_identity(self):
    self.check_case('focus')

  def test_visual_failure_preserves_valid_state_export_and_reports_warning(self):
    self.check_case('failure')

  def test_abort_precedes_exactly_once_view_disposal(self):
    self.check_case('dispose')

  def test_refresh_tracks_actual_owner_edits_import_undo_and_refusal(self):
    self.check_case('refresh')

  def test_competing_or_unreadable_draft_blocks_before_owner_mutation(self):
    self.check_case('concurrency')

  def test_group_navigation_and_search_keep_owner_visibility(self):
    self.check_case('navigation-review')

  def test_exact_results_limit_remains_exportable_for_api1_and_api2(self):
    self.check_case('export-limit-review')

  def test_owner_key_retains_undo_without_an_undeclared_default_hook(self):
    self.check_case('undo-review')

  def test_editor_state_controls_are_distinct_from_canonical_export(self):
    self.check_case('labels-review')

  def test_reviewer_survives_restore_import_refusal_and_full_envelope_bounds(self):
    self.check_case('reviewer-review')

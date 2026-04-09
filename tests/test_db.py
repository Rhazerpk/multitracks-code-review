"""Unit tests for the SQLite persistence layer (web/db.py)."""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

import db as db_module
from db import get_analytics, get_history, get_review_by_uuid, init_db, save_review


class TestDatabase(unittest.TestCase):

	def setUp(self):
		"""Use a fresh temp database for each test."""
		self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
		self._tmp.close()
		init_db(self._tmp.name)

	def tearDown(self):
		Path(self._tmp.name).unlink(missing_ok=True)

	def _sample_review(self, issue_key="MT-1001", pr_number=42, errors=2, warnings=3):
		return {
			"issue_key": issue_key,
			"pr_number": pr_number,
			"pr_title": "Test PR",
			"pr_url": "https://github.com/test/repo/pull/42",
			"errors": errors,
			"warnings": warnings,
			"suggestions": 1,
			"scope_score": 85,
			"summary": "2 errors found.",
			"diff_text": "--- a/file.ts\n+++ b/file.ts\n@@ -1 +1 @@\n+console.log('hi');",
			"files": [
				{
					"file_path": "src/service.ts",
					"error_count": errors,
					"warning_count": warnings,
					"suggestion_count": 1,
					"issues": [
						{
							"line": 10,
							"rule_id": "TS-001",
							"severity": "warning",
							"message": "Avoid console.log",
							"original_line": "console.log('hi');",
							"suggested_fix": None,
							"suppressed": False,
						},
						{
							"line": 15,
							"rule_id": "CS-ASYNC-001",
							"severity": "error",
							"message": "No async void",
							"original_line": "async void DoStuff()",
							"suggested_fix": None,
							"suppressed": True,  # suppressed
						},
					],
				}
			],
		}

	def test_save_and_retrieve_review(self):
		uuid = save_review(self._sample_review())
		self.assertIsNotNone(uuid)
		self.assertEqual(len(uuid), 36)  # UUID format

		review = get_review_by_uuid(uuid)
		self.assertIsNotNone(review)
		self.assertEqual(review["issue_key"], "MT-1001")
		self.assertEqual(review["pr_number"], 42)
		self.assertEqual(review["errors"], 2)

	def test_review_has_files_and_issues(self):
		uuid = save_review(self._sample_review())
		review = get_review_by_uuid(uuid)

		self.assertEqual(len(review["files"]), 1)
		self.assertEqual(review["files"][0]["file_path"], "src/service.ts")
		self.assertEqual(len(review["files"][0]["issues"]), 2)

	def test_suppressed_issue_stored_correctly(self):
		uuid = save_review(self._sample_review())
		review = get_review_by_uuid(uuid)
		issues = review["files"][0]["issues"]
		suppressed_issues = [i for i in issues if i["suppressed"]]
		self.assertEqual(len(suppressed_issues), 1)
		self.assertEqual(suppressed_issues[0]["rule_id"], "CS-ASYNC-001")

	def test_get_history_returns_reviews(self):
		save_review(self._sample_review("MT-1001"))
		save_review(self._sample_review("MT-1002"))

		history = get_history(limit=10)
		self.assertEqual(len(history), 2)
		# Most recent first
		self.assertEqual(history[0]["issue_key"], "MT-1002")

	def test_get_history_limit_respected(self):
		for i in range(5):
			save_review(self._sample_review(f"MT-{1000 + i}"))

		history = get_history(limit=3)
		self.assertEqual(len(history), 3)

	def test_unknown_uuid_returns_none(self):
		result = get_review_by_uuid("nonexistent-uuid")
		self.assertIsNone(result)

	def test_analytics_top_violations(self):
		save_review(self._sample_review())
		analytics = get_analytics()

		self.assertIn("top_violations", analytics)
		self.assertIn("score_trend", analytics)
		self.assertIn("files_with_most_issues", analytics)
		self.assertEqual(analytics["total_reviews"], 1)

		# TS-001 should appear (suppressed=0), CS-ASYNC-001 should not (suppressed=1)
		rule_ids = [v["rule_id"] for v in analytics["top_violations"]]
		self.assertIn("TS-001", rule_ids)
		self.assertNotIn("CS-ASYNC-001", rule_ids)

	def test_diff_truncated_at_500kb(self):
		large_diff = "x" * 600_000
		review_data = self._sample_review()
		review_data["diff_text"] = large_diff

		uuid = save_review(review_data)
		review = get_review_by_uuid(uuid)
		self.assertLessEqual(len(review["diff_text"]), 500_020)  # 500k + "[diff truncated]"
		self.assertIn("[diff truncated]", review["diff_text"])


if __name__ == "__main__":
	unittest.main()

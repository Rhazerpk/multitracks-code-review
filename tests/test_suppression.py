"""Tests for the review-ignore false positive suppression system."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "web"))

from rules.base import ReviewComment
from web.app import apply_suppression


def _make_comment(file_path, line_number, rule_id, severity="warning"):
	return ReviewComment(
		file_path=file_path,
		line_number=line_number,
		message="Test issue",
		severity=severity,
		rule_id=rule_id,
	)


class TestApplySuppression(unittest.TestCase):

	def test_suppress_on_same_line(self):
		"""Suppression comment on the same line as the finding."""
		comments = [_make_comment("file.ts", 5, "TS-001")]
		changed = {
			"file.ts": {
				5: 'console.log("x"); // review-ignore: TS-001',
			}
		}
		result = apply_suppression(comments, changed)
		self.assertTrue(result[0].suppressed)

	def test_suppress_on_preceding_line(self):
		"""Suppression comment one line before the finding."""
		comments = [_make_comment("file.ts", 6, "TS-001")]
		changed = {
			"file.ts": {
				5: "// review-ignore: TS-001",
				6: 'console.log("x");',
			}
		}
		result = apply_suppression(comments, changed)
		self.assertTrue(result[0].suppressed)

	def test_suppress_wrong_rule_id_does_not_suppress(self):
		"""A suppress comment for a different rule ID should not suppress."""
		comments = [_make_comment("file.ts", 5, "TS-001")]
		changed = {
			"file.ts": {
				5: 'console.log("x"); // review-ignore: TS-999',
			}
		}
		result = apply_suppression(comments, changed)
		self.assertFalse(result[0].suppressed)

	def test_suppress_case_insensitive(self):
		"""Suppression should be case-insensitive on rule IDs."""
		comments = [_make_comment("file.ts", 5, "TS-001")]
		changed = {
			"file.ts": {
				5: 'console.log("x"); // review-ignore: ts-001',
			}
		}
		result = apply_suppression(comments, changed)
		self.assertTrue(result[0].suppressed)

	def test_no_suppress_without_comment(self):
		"""Normal findings should not be suppressed."""
		comments = [_make_comment("file.ts", 5, "TS-001")]
		changed = {
			"file.ts": {
				5: 'console.log("x");',
			}
		}
		result = apply_suppression(comments, changed)
		self.assertFalse(result[0].suppressed)

	def test_sql_style_suppression(self):
		"""Hash-style suppression comments (SQL/config files)."""
		comments = [_make_comment("query.sql", 3, "SQL-BP-001")]
		changed = {
			"query.sql": {
				3: "SELECT @@IDENTITY -- review-ignore: SQL-BP-001",
			}
		}
		result = apply_suppression(comments, changed)
		self.assertTrue(result[0].suppressed)

	def test_multiple_comments_partial_suppression(self):
		"""Only the matching comment is suppressed, others are not."""
		comments = [
			_make_comment("file.ts", 5, "TS-001"),
			_make_comment("file.ts", 10, "TS-003"),
		]
		changed = {
			"file.ts": {
				5: 'console.log("x"); // review-ignore: TS-001',
				10: 'const x: any = {};',
			}
		}
		result = apply_suppression(comments, changed)
		self.assertTrue(result[0].suppressed)
		self.assertFalse(result[1].suppressed)

	def test_different_files_not_cross_contaminated(self):
		"""Suppression in file A should not affect findings in file B."""
		comments = [
			_make_comment("a.ts", 1, "TS-001"),
			_make_comment("b.ts", 1, "TS-001"),
		]
		changed = {
			"a.ts": {1: 'console.log("x"); // review-ignore: TS-001'},
			"b.ts": {1: 'console.log("y");'},
		}
		result = apply_suppression(comments, changed)
		self.assertTrue(result[0].suppressed)
		self.assertFalse(result[1].suppressed)


if __name__ == "__main__":
	unittest.main()

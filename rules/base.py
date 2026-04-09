"""Base rule class for the code review bot."""

from dataclasses import dataclass, field


@dataclass
class ReviewComment:
	"""Represents a single review comment to post on a PR."""
	file_path: str
	line_number: int
	message: str
	severity: str  # "error", "warning", "suggestion"
	rule_id: str
	original_line: str | None = None      # The original line content
	suggested_fix: str | None = None      # The corrected line content (if auto-fixable)
	suppressed: bool = False              # True when a review-ignore comment suppresses this finding

	def format_message(self) -> str:
		severity_icons = {
			"error": "🔴",
			"warning": "🟡",
			"suggestion": "🔵",
		}
		icon = severity_icons.get(self.severity, "⚪")
		return f"{icon} **{self.severity.upper()}** (`{self.rule_id}`): {self.message}"


class BaseRule:
	"""Base class for all review rules."""

	file_patterns: list[str] = []
	category: str = "general"

	def applies_to(self, file_path: str) -> bool:
		"""Check if this rule set applies to the given file."""
		if not self.file_patterns:
			return True
		return any(file_path.lower().endswith(ext) for ext in self.file_patterns)

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		"""
		Analyze changed lines and return review comments.

		Args:
			file_path: Path to the file being reviewed.
			changed_lines: Dict mapping line numbers to their content.

		Returns:
			List of ReviewComment objects, optionally with suggested_fix set.
		"""
		raise NotImplementedError

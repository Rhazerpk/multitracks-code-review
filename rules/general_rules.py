"""
General code review rules: security checks and quality standards.

These rules apply across all file types and catch common issues
that could affect security, reliability, or maintainability.
"""

import re

from .base import BaseRule, ReviewComment


class SecurityRules(BaseRule):
	"""Detects potential security issues in code changes."""

	file_patterns = [".cs", ".sql", ".config", ".json", ".xml", ".yml", ".yaml"]
	category = "Security"

	# Connection strings or credentials in code
	_CONNECTION_STRING_PATTERN = re.compile(
		r'(?:password|pwd|secret|apikey|api_key|connectionstring|conn_str)\s*[=:]\s*["\'][^"\']{8,}',
		re.IGNORECASE,
	)

	# Hardcoded IP addresses (not localhost)
	_HARDCODED_IP_PATTERN = re.compile(
		r'(?<!")(?:(?:25[0-5]|2[0-4]\d|1?\d{1,2})\.){3}(?:25[0-5]|2[0-4]\d|1?\d{1,2})(?!")'
	)

	# SQL string concatenation (potential SQL injection)
	_SQL_CONCAT_PATTERN = re.compile(
		r'(?:ExecuteStoredProcedure|ExecuteReader|ExecuteNonQuery|ExecuteScalar|SqlCommand)\s*\('
		r'[^)]*\+\s*(?:\w+|")',
		re.IGNORECASE,
	)

	# Raw SQL queries built with string concatenation
	_RAW_SQL_PATTERN = re.compile(
		r'(?:\"SELECT|\"INSERT|\"UPDATE|\"DELETE)\b[^"]*\"\s*\+',
		re.IGNORECASE,
	)

	# Sensitive file patterns
	_SENSITIVE_FILE_PATTERN = re.compile(
		r'(?:\.env|credentials|secrets?\.|appsettings\.Production|web\.config$)',
		re.IGNORECASE,
	)

	# TODO/HACK/FIXME with security implications
	_SECURITY_TODO_PATTERN = re.compile(
		r'(?:TODO|HACK|FIXME|XXX).*(?:security|auth|password|credential|token|secret|encrypt)',
		re.IGNORECASE,
	)

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []

		# Check if the file itself is sensitive
		if self._SENSITIVE_FILE_PATTERN.search(file_path):
			if changed_lines:
				first_line = min(changed_lines.keys())
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=first_line,
					message="This file may contain sensitive configuration. Ensure no secrets, "
							"passwords, or API keys are committed. Use environment variables "
							"or Azure App Configuration instead.",
					severity="warning",
					rule_id="SEC-001",
				))

		for line_num, line in changed_lines.items():
			stripped = line.strip()

			# Skip comments in C# files
			if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
				# Still check for security TODOs in comments
				if self._SECURITY_TODO_PATTERN.search(stripped):
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message="Security-related TODO/HACK found. Ensure this is tracked "
								"and resolved before deployment.",
						severity="warning",
						rule_id="SEC-005",
					))
				continue

			# Rule: No hardcoded credentials
			if self._CONNECTION_STRING_PATTERN.search(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Possible hardcoded credential or connection string detected. "
							"Use configuration files with `.tmpl` placeholders, environment "
							"variables, or Azure App Configuration.",
					severity="error",
					rule_id="SEC-002",
				))

			# Rule: SQL injection via string concatenation
			if file_path.endswith(".cs"):
				if self._SQL_CONCAT_PATTERN.search(stripped) or self._RAW_SQL_PATTERN.search(stripped):
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message="Potential SQL injection: avoid building SQL queries with string "
								"concatenation. Use stored procedures with parameterized queries "
								"via `SQL.Parameters.Add()` or Dapper `DynamicParameters`.",
						severity="error",
						rule_id="SEC-003",
					))

		return comments


class GeneralQualityRules(BaseRule):
	"""General code quality checks that apply across languages."""

	category = "Quality"

	# Large file warning threshold
	_LARGE_CHANGE_THRESHOLD = 50  # lines changed in single file

	# Console.WriteLine left in production code
	_CONSOLE_WRITELINE_PATTERN = re.compile(r'Console\.Write(?:Line)?\(')

	# Debugger statements
	_DEBUGGER_PATTERN = re.compile(
		r'(?:Debugger\.(?:Break|Launch)|System\.Diagnostics\.Debugger|#if\s+DEBUG)',
	)

	# Commented-out code blocks (multiple consecutive commented lines)
	_COMMENTED_CODE_PATTERN = re.compile(
		r'^\s*//\s*(?:public|private|protected|internal|var|int|string|bool|if|for|while|return|await|try|catch)\b'
	)

	# Empty catch blocks
	_EMPTY_CATCH_PATTERN = re.compile(r'catch\s*(?:\([^)]*\))?\s*\{\s*\}')

	def applies_to(self, file_path: str) -> bool:
		"""Apply to C# and SQL files."""
		return file_path.lower().endswith((".cs", ".sql"))

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []
		commented_code_streak = 0
		streak_start = 0

		# Rule: Large change warning
		if len(changed_lines) > self._LARGE_CHANGE_THRESHOLD:
			first_line = min(changed_lines.keys())
			comments.append(ReviewComment(
				file_path=file_path,
				line_number=first_line,
				message=f"Large change detected ({len(changed_lines)} lines modified). "
						f"Consider breaking this into smaller, focused commits for easier review.",
				severity="suggestion",
				rule_id="GEN-001",
			))

		sorted_lines = sorted(changed_lines.items())

		for i, (line_num, line) in enumerate(sorted_lines):
			stripped = line.strip()

			if file_path.endswith(".cs"):
				# Rule: Console.WriteLine in production code
				if self._CONSOLE_WRITELINE_PATTERN.search(stripped):
					# Exclude test files and process files
					if "Tests" not in file_path and "Process/" not in file_path:
						comments.append(ReviewComment(
							file_path=file_path,
							line_number=line_num,
							message="Avoid `Console.WriteLine` in production code. Use "
									"`AppInsightsLoggerService` for logging instead.",
							severity="warning",
							rule_id="GEN-002",
						))

				# Rule: Debugger statements
				if self._DEBUGGER_PATTERN.search(stripped):
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message="Debugger statement detected. Remove before merging.",
						severity="error",
						rule_id="GEN-003",
					))

				# Rule: Track commented-out code blocks
				if self._COMMENTED_CODE_PATTERN.match(stripped):
					if commented_code_streak == 0:
						streak_start = line_num
					commented_code_streak += 1
				else:
					if commented_code_streak >= 3:
						comments.append(ReviewComment(
							file_path=file_path,
							line_number=streak_start,
							message=f"Block of commented-out code ({commented_code_streak} lines). "
									f"Remove dead code instead of commenting it out — "
									f"version control preserves history.",
							severity="suggestion",
							rule_id="GEN-004",
						))
					commented_code_streak = 0

				# Rule: Empty catch blocks
				if self._EMPTY_CATCH_PATTERN.search(stripped):
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message="Empty catch block detected. At minimum, log the exception "
								"using `AppInsightsLoggerService`.",
						severity="warning",
						rule_id="GEN-005",
					))

		# Check for trailing commented-out code
		if commented_code_streak >= 3:
			comments.append(ReviewComment(
				file_path=file_path,
				line_number=streak_start,
				message=f"Block of commented-out code ({commented_code_streak} lines). "
						f"Remove dead code instead of commenting it out.",
				severity="suggestion",
				rule_id="GEN-004",
			))

		return comments


class SensitiveDataLoggingRules(BaseRule):
	"""Detects possible logging of sensitive data in C#, TypeScript, and JavaScript."""

	file_patterns = [".cs", ".ts", ".js"]
	category = "Security"

	_LOG_CALL_PATTERN = re.compile(
		r'(?:'
		r'(?:log(?:ger)?)\s*\.\s*(?:Info|Debug|Error|Warn(?:ing)?|Log|Write(?:Line)?|Trace|Fatal|Verbose)'
		r'|Console\s*\.\s*Write(?:Line)?'
		r'|console\s*\.\s*(?:log|warn|error|info|debug|trace)'
		r')\s*\(',
		re.IGNORECASE,
	)

	_SENSITIVE_KEYWORDS_PATTERN = re.compile(
		r'\b(?:password|passwd|token|secret|apikey|api_key|credential|ssn|creditcard)\b',
		re.IGNORECASE,
	)

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []

		for line_num, line in changed_lines.items():
			stripped = line.strip()

			if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
				continue

			if (self._LOG_CALL_PATTERN.search(stripped)
					and self._SENSITIVE_KEYWORDS_PATTERN.search(stripped)):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Possible sensitive data in log statement. "
							"Avoid logging passwords, tokens, or credentials.",
					severity="error",
					rule_id="SEC-006",
				))

		return comments

"""
SQL code review rules based on MultiTracks.com coding standards.

Source: .github/sql.md
"""

import re

from .base import BaseRule, ReviewComment

MAX_LINE_WIDTH = 138


class SqlFormattingRules(BaseRule):
	"""Enforces SQL formatting conventions."""

	file_patterns = [".sql"]
	category = "SQL Formatting"

	# Detects lowercase SQL keywords (common ones)
	_SQL_KEYWORDS = [
		"select", "from", "where", "insert into", "update", "set",
		"delete", "join", "inner join", "left join", "right join",
		"cross join", "on", "and", "or", "begin", "end", "if",
		"else", "while", "declare", "exec", "execute", "create",
		"alter", "drop", "values", "order by", "group by", "having",
		"union", "exists", "in", "not", "null", "is", "as", "case",
		"when", "then", "end", "between", "like", "top", "distinct",
		"into", "with",
	]

	# Matches "LEFT OUTER JOIN" (OUTER should be removed)
	_OUTER_JOIN_PATTERN = re.compile(r'\bLEFT\s+OUTER\s+JOIN\b', re.IGNORECASE)

	# Matches @@IDENTITY usage
	_AT_IDENTITY_PATTERN = re.compile(r'@@IDENTITY\b')

	# Matches NOT ... IS NULL anti-pattern
	_NOT_IS_NULL_PATTERN = re.compile(r'\bNOT\s+\w+\s+IS\s+NULL\b', re.IGNORECASE)

	# Matches [dbo].[TableName] pattern
	_DBO_BRACKET_PATTERN = re.compile(r'\[dbo\]\.\[(\w+)\]')

	# Detects multiple consecutive empty lines
	_DOUBLE_EMPTY_PATTERN = re.compile(r'^\s*$')

	# Detects extra spaces between terms
	_EXTRA_SPACES_PATTERN = re.compile(r'(?<!\s)  +(?!\s*$|--)')

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []
		sorted_lines = sorted(changed_lines.items())

		prev_was_empty = False

		for i, (line_num, line) in enumerate(sorted_lines):
			stripped = line.strip()

			# Skip single-line comments
			if stripped.startswith("--"):
				prev_was_empty = False
				continue

			# Rule: Line length
			expanded = line.rstrip("\n").expandtabs(4)
			if len(expanded) > MAX_LINE_WIDTH:
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message=f"Line exceeds {MAX_LINE_WIDTH} characters ({len(expanded)} chars).",
					severity="suggestion",
					rule_id="SQL-FMT-001",
				))

			# Rule: No consecutive empty lines
			is_empty = self._DOUBLE_EMPTY_PATTERN.match(stripped) is not None
			if is_empty and prev_was_empty:
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Avoid multiple consecutive empty lines between statements.",
					severity="suggestion",
					rule_id="SQL-FMT-002",
				))
			prev_was_empty = is_empty

			if not stripped:
				continue

			# Rule: SQL keywords should be UPPERCASE
			# Check each word against known keywords
			self._check_keyword_casing(file_path, line_num, stripped, comments)

			# Rule: No LEFT OUTER JOIN (use LEFT JOIN)
			if self._OUTER_JOIN_PATTERN.search(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Use `LEFT JOIN` instead of `LEFT OUTER JOIN`. "
							"The `OUTER` keyword is redundant.",
					severity="warning",
					rule_id="SQL-FMT-003",
					original_line=line,
					suggested_fix=self._OUTER_JOIN_PATTERN.sub("LEFT JOIN", line),
				))

			# Rule: No [dbo].[Table] in INSERT/SELECT/UPDATE statements
			dbo_match = self._DBO_BRACKET_PATTERN.search(stripped)
			if dbo_match and not stripped.upper().startswith("CREATE"):
				table_name = dbo_match.group(1)
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message=f"Use `{table_name}` instead of `[dbo].[{table_name}]`. "
							f"Brackets and schema should be used only when needed.",
					severity="warning",
					rule_id="SQL-FMT-004",
				))

			# Rule: Extra spaces between terms
			# Remove string content and comments before checking
			code_only = re.sub(r"'[^']*'", "''", stripped)
			code_only = re.sub(r"--.*$", "", code_only)
			if self._EXTRA_SPACES_PATTERN.search(code_only):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Avoid extra spaces between terms. Use single spaces.",
					severity="suggestion",
					rule_id="SQL-FMT-005",
				))

		return comments

	def _check_keyword_casing(
		self, file_path: str, line_num: int, line: str, comments: list[ReviewComment]
	) -> None:
		"""Check that SQL keywords are uppercase."""
		# Remove string literals and comments for analysis
		cleaned = re.sub(r"'[^']*'", "''", line)
		cleaned = re.sub(r"--.*$", "", cleaned)

		# Check multi-word keywords first
		multi_word_keywords = [
			"insert into", "inner join", "left join", "right join",
			"cross join", "order by", "group by",
		]
		for kw in multi_word_keywords:
			pattern = re.compile(r'\b' + kw + r'\b', re.IGNORECASE)
			match = pattern.search(cleaned)
			if match and match.group(0) != kw.upper():
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message=f"SQL keyword `{match.group(0)}` should be uppercase: `{kw.upper()}`.",
					severity="warning",
					rule_id="SQL-FMT-006",
				))
				return  # One keyword warning per line is enough

		# Check single-word keywords
		single_keywords = [
			"select", "from", "where", "update", "delete", "set",
			"join", "on", "and", "or", "begin", "end", "declare",
			"exec", "execute", "values", "having", "union", "exists",
			"between", "like", "distinct", "with", "as",
		]
		words = re.findall(r'\b\w+\b', cleaned)
		for word in words:
			if word.lower() in single_keywords and word != word.upper() and word != word.lower().capitalize():
				# It's a mixed-case or lowercase keyword
				if word.lower() == word and word in single_keywords:
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message=f"SQL keyword `{word}` should be uppercase: `{word.upper()}`.",
						severity="warning",
						rule_id="SQL-FMT-006",
					))
					return


class SqlBestPracticeRules(BaseRule):
	"""Enforces SQL best practices specific to MultiTracks.com."""

	file_patterns = [".sql"]
	category = "SQL Best Practices"

	_AT_IDENTITY_PATTERN = re.compile(r'@@IDENTITY\b')
	_NOT_IS_NULL_PATTERN = re.compile(r'\bNOT\s+(@?\w+)\s+IS\s+NULL\b', re.IGNORECASE)

	# Detects UPDATE with alias pattern (UPDATE c SET ... instead of UPDATE Customer SET ...)
	_UPDATE_ALIAS_PATTERN = re.compile(
		r'^\s*UPDATE\s+([a-z]{1,5})\s+SET\b',
		re.IGNORECASE,
	)

	# Detects IF without parenthesis
	_IF_NO_PAREN_PATTERN = re.compile(r'^\s*IF\s+(?!\()\s*@', re.IGNORECASE)

	# Detects SELECT @var = @@IDENTITY
	_SCOPE_IDENTITY_PATTERN = re.compile(r'@@IDENTITY', re.IGNORECASE)

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []

		for line_num, line in changed_lines.items():
			stripped = line.strip()

			# Skip comments
			if stripped.startswith("--"):
				continue

			# Rule: Use SCOPE_IDENTITY() over @@IDENTITY
			if self._AT_IDENTITY_PATTERN.search(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Use `SCOPE_IDENTITY()` instead of `@@IDENTITY`. The codebase uses "
							"nested stored procedures where `@@IDENTITY` is unreliable.",
					severity="error",
					rule_id="SQL-BP-001",
					original_line=line,
					suggested_fix=self._AT_IDENTITY_PATTERN.sub("SCOPE_IDENTITY()", line),
				))

			# Rule: IS NOT NULL preferred over NOT ... IS NULL
			not_null_match = self._NOT_IS_NULL_PATTERN.search(stripped)
			if not_null_match:
				var_name = not_null_match.group(1)
				fix = re.sub(r'\bNOT\s+(@?\w+)\s+IS\s+NULL\b', r'\1 IS NOT NULL', line, flags=re.IGNORECASE)
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message=f"Use `{var_name} IS NOT NULL` instead of `NOT {var_name} IS NULL`.",
					severity="warning",
					rule_id="SQL-BP-002",
					original_line=line,
					suggested_fix=fix,
				))

			# Rule: UPDATE should not use alias
			alias_match = self._UPDATE_ALIAS_PATTERN.match(stripped)
			if alias_match:
				alias = alias_match.group(1)
				# Short aliases (1-5 chars, all lowercase) are likely table aliases
				if alias.islower() and len(alias) <= 5:
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message=f"Avoid using table aliases in UPDATE statements. "
								f"Use the full table name: `UPDATE TableName SET ...`",
						severity="warning",
						rule_id="SQL-BP-003",
					))

			# Rule: IF blocks should use parenthesis
			if self._IF_NO_PAREN_PATTERN.match(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="IF conditions should use parentheses: `IF (@variable > 0)`.",
					severity="warning",
					rule_id="SQL-BP-004",
				))

		return comments


class SqlPerformanceRules(BaseRule):
	"""Enforces SQL performance best practices."""

	file_patterns = [".sql"]
	category = "SQL Performance"

	# Matches SELECT * or SELECT TOP N * (case insensitive).
	# Covers: SELECT *, SELECT TOP 10 *, SELECT TOP(10) *
	_SELECT_STAR_PATTERN = re.compile(
		r'\bSELECT\s+(?:TOP\s*\(?\d+\)?\s+)?\*',
		re.IGNORECASE,
	)

	# Matches DECLARE ... CURSOR on the same line (case insensitive).
	_CURSOR_PATTERN = re.compile(
		r'\bDECLARE\b.+\bCURSOR\b',
		re.IGNORECASE,
	)

	# Matches BEGIN TRAN or BEGIN TRANSACTION (case insensitive).
	_BEGIN_TRAN_PATTERN = re.compile(
		r'\bBEGIN\s+TRAN(?:SACTION)?\b',
		re.IGNORECASE,
	)

	# Matches ROLLBACK (case insensitive).
	_ROLLBACK_PATTERN = re.compile(
		r'\bROLLBACK\b',
		re.IGNORECASE,
	)

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []

		# SQL-BP-005: BEGIN TRAN without ROLLBACK — file-level analysis.
		# Inspect all changed lines together to detect the pattern across the diff.
		all_line_contents = [line for _, line in sorted(changed_lines.items())]
		full_text = "\n".join(all_line_contents)

		has_begin_tran = self._BEGIN_TRAN_PATTERN.search(full_text) is not None
		has_rollback = self._ROLLBACK_PATTERN.search(full_text) is not None

		if has_begin_tran and not has_rollback:
			# Find the first line number where BEGIN TRAN appears
			for line_num, line in sorted(changed_lines.items()):
				if self._BEGIN_TRAN_PATTERN.search(line):
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message="Transaction is missing ROLLBACK error handling. "
								"Use BEGIN TRY/CATCH with ROLLBACK.",
						severity="warning",
						rule_id="SQL-BP-005",
					))
					break

		# Per-line rules
		for line_num, line in changed_lines.items():
			stripped = line.strip()

			# Skip comments
			if stripped.startswith("--"):
				continue

			# SQL-PERF-001: SELECT * not allowed
			if self._SELECT_STAR_PATTERN.search(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Avoid SELECT *. List only the columns you need for better "
							"performance and maintainability.",
					severity="warning",
					rule_id="SQL-PERF-001",
				))

			# SQL-PERF-002: cursor usage
			if self._CURSOR_PATTERN.search(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Avoid cursors. Use set-based operations "
							"(JOIN, UPDATE...FROM, etc.) for better performance.",
					severity="warning",
					rule_id="SQL-PERF-002",
				))

		return comments

# Alias for test compatibility
SqlPerfRules = SqlPerformanceRules

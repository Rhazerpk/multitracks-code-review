"""
C# code review rules based on MultiTracks.com coding standards.

Source: .github/csharp.md and .github/copilot-instructions.md
"""

import re

from .base import BaseRule, ReviewComment

# Maximum line width per project standards
MAX_LINE_WIDTH = 138


class CSharpNamingRules(BaseRule):
	"""Enforces C# naming conventions specific to MultiTracks.com."""

	file_patterns = [".cs"]
	category = "C# Naming"

	# Regex patterns for detecting naming violations
	# Matches lowercase "Id" at end of identifier (e.g., customerId, userId)
	# but not inside words like "Identify" or "Video"
	_ID_PATTERN = re.compile(
		r'\b([a-zA-Z_]+[a-z])Id\b'
	)

	# Matches private field declarations without underscore prefix
	# e.g., "private int customerID;" or "private readonly string name;"
	_PRIVATE_FIELD_PATTERN = re.compile(
		r'private\s+(?:readonly\s+)?(?:static\s+)?(?:\w+(?:<[^>]+>)?)\s+([a-zA-Z][a-zA-Z0-9]*)\s*[;=]'
	)

	# Matches type-prefixed variable names (Hungarian notation)
	# e.g., "int iCustomer", "string strName", "bool bFlag"
	_HUNGARIAN_PATTERN = re.compile(
		r'(?:int|long|short|byte)\s+(i[A-Z]\w*)|'
		r'(?:string)\s+(str[A-Z]\w*)|'
		r'(?:bool)\s+(b[A-Z]\w*)|'
		r'(?:double|float|decimal)\s+(d[A-Z]\w*|f[A-Z]\w*)'
	)

	# Matches var usage with primitive types
	# e.g., "var count = 0;", "var name = "";"
	_VAR_PRIMITIVE_PATTERN = re.compile(
		r'var\s+\w+\s*=\s*(?:'
		r'0(?:\.\d+)?'     # numeric literal
		r'|[1-9]\d*(?:\.\d+)?'  # non-zero numeric
		r'|-\d+(?:\.\d+)?'  # negative numeric
		r'|""'              # empty string
		r'|"[^"]*"'        # string literal (simple, no interpolation)
		r'|true|false'      # boolean
		r')\s*;'
	)

	# Matches protected/public fields that should be properties
	_PUBLIC_FIELD_PATTERN = re.compile(
		r'(?:public|protected)\s+(?!(?:class|interface|enum|struct|delegate|event|override|virtual|abstract|static\s+class|async|partial))'
		r'(?:readonly\s+)?(?:\w+(?:<[^>]+>)?)\s+([A-Z]\w*)\s*;'
	)

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []

		for line_num, line in changed_lines.items():
			stripped = line.strip()

			# Skip comments and string literals
			if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
				continue

			# Rule: ID should be uppercase
			match = self._ID_PATTERN.search(line)
			if match:
				# Verify it's not inside a string literal or a method call like .ToLowerInvariant()
				full_word = match.group(0)
				# Avoid false positives on well-known framework names
				if full_word not in ("classId", "Guid", "void", "Android", "roid"):
					fixed_word = full_word[:-2] + "ID"
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message=f"`{full_word}` should use uppercase `ID` (e.g., `{fixed_word}`). "
								f"Per project standards, the `ID` suffix is always uppercased.",
						severity="warning",
						rule_id="CS-NAME-001",
						original_line=line,
						suggested_fix=line.replace(full_word, fixed_word, 1),
					))

			# Rule: Private fields must have underscore prefix
			field_match = self._PRIVATE_FIELD_PATTERN.search(stripped)
			if field_match:
				field_name = field_match.group(1)
				if not field_name.startswith("_") and field_name not in ("value", "sender", "args", "e"):
					fix = re.sub(r'\b' + re.escape(field_name) + r'\b', f'_{field_name}', line, count=1)
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message=f"Private field `{field_name}` should have an underscore prefix: `_{field_name}`. "
								f"Per project standards, all class members use `_camelCase`.",
						severity="warning",
						rule_id="CS-NAME-002",
						original_line=line,
						suggested_fix=fix,
					))

			# Rule: No Hungarian notation
			hungarian_match = self._HUNGARIAN_PATTERN.search(stripped)
			if hungarian_match:
				var_name = next(g for g in hungarian_match.groups() if g)
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message=f"Avoid type-prefixed variable names like `{var_name}`. "
							f"Use a descriptive name without type suffixes.",
					severity="warning",
					rule_id="CS-NAME-003",
				))

			# Rule: No var for primitive types
			if self._VAR_PRIMITIVE_PATTERN.search(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Do not use `var` for primitive types. Use the explicit type name "
							"(`int`, `string`, `bool`, `decimal`, etc.) instead.",
					severity="warning",
					rule_id="CS-NAME-004",
				))

			# Rule: Protected/public fields should be properties
			pub_match = self._PUBLIC_FIELD_PATTERN.search(stripped)
			if pub_match:
				field_name = pub_match.group(1)
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message=f"Public/protected field `{field_name}` should be exposed as a property "
							f"with `{{ get; set; }}`.",
					severity="warning",
					rule_id="CS-NAME-005",
				))

		return comments


class CSharpStyleRules(BaseRule):
	"""Enforces C# style conventions specific to MultiTracks.com."""

	file_patterns = [".cs"]
	category = "C# Style"

	# Detects this. qualifier usage
	_THIS_QUALIFIER_PATTERN = re.compile(r'\bthis\.(?!GetType|Equals|ReferenceEquals|MemberwiseClone)')

	# Detects missing braces after control flow (single-line if/else/for/while)
	_CONTROL_FLOW_NO_BRACE = re.compile(
		r'^\s*(?:if|else if|else|for|foreach|while)\s*(?:\(.*\))?\s*$'
	)

	# Detects Entity Framework usage
	_EF_PATTERN = re.compile(
		r'(?:DbContext|DbSet|\.Include\(|\.ThenInclude\(|EntityFramework|\.SaveChanges|\.ToListAsync|'
		r'using\s+.*EntityFramework|Microsoft\.EntityFrameworkCore)',
		re.IGNORECASE,
	)

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []
		sorted_lines = sorted(changed_lines.items())

		for i, (line_num, line) in enumerate(sorted_lines):
			stripped = line.strip()

			# Skip comments
			if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
				continue

			# Rule: Line length limit
			# Use expandtabs to account for tab width of 4
			expanded = line.rstrip("\n").expandtabs(4)
			if len(expanded) > MAX_LINE_WIDTH:
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message=f"Line exceeds {MAX_LINE_WIDTH} characters ({len(expanded)} chars). "
							f"Consider breaking this line.",
					severity="suggestion",
					rule_id="CS-STYLE-001",
				))

			# Rule: No this. qualifier
			if self._THIS_QUALIFIER_PATTERN.search(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Avoid `this.` qualifier. Use the `_field` naming convention "
							"for class members instead.",
					severity="warning",
					rule_id="CS-STYLE-002",
					original_line=line,
					suggested_fix=self._THIS_QUALIFIER_PATTERN.sub("", line),
				))

			# Rule: Control flow must use braces
			if self._CONTROL_FLOW_NO_BRACE.match(stripped):
				# Check if next changed line is NOT an opening brace
				if i + 1 < len(sorted_lines):
					next_line_num, next_line = sorted_lines[i + 1]
					next_stripped = next_line.strip()
					# If the next line is not { and not another control flow keyword,
					# it's likely a braceless body
					if (next_stripped and
						not next_stripped.startswith("{") and
						not next_stripped.startswith("//") and
						next_line_num == line_num + 1):
						comments.append(ReviewComment(
							file_path=file_path,
							line_number=line_num,
							message="Always use braces `{}` for control flow statements "
									"(`if`, `else`, `for`, `while`, `using`).",
							severity="warning",
							rule_id="CS-STYLE-003",
						))

			# Rule: No Entity Framework
			if self._EF_PATTERN.search(stripped):
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Entity Framework is not used in this project. Use `DataAccess.SQL` "
							"(Framework projects) or `DapperService` (.NET Core projects) with "
							"stored procedures instead.",
					severity="error",
					rule_id="CS-STYLE-004",
				))

		return comments


class CSharpAsyncRules(BaseRule):
	"""Enforces C# async/await best practices."""

	file_patterns = [".cs"]
	category = "C# Async"

	# Matches async void method declarations, capturing the method name.
	# Looks for "async void" followed by an identifier and opening paren or angle bracket.
	_ASYNC_VOID_PATTERN = re.compile(
		r'\basync\s+void\s+(\w+)\s*[(<]'
	)

	# Matches async method declarations that return Task, Task<T>, ValueTask, or ValueTask<T>.
	# Captures the method name in group 1.
	_ASYNC_METHOD_PATTERN = re.compile(
		r'\basync\s+(?:Task(?:<[^>]+>)?|ValueTask(?:<[^>]+>)?|IAsyncEnumerable<[^>]+>)\s+'
		r'(\w+)\s*[(<]'
	)

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []

		for line_num, line in changed_lines.items():
			stripped = line.strip()

			# Skip comments
			if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
				continue

			# CS-ASYNC-001: async void not allowed (event handlers starting with "On" are exempt)
			void_match = self._ASYNC_VOID_PATTERN.search(stripped)
			if void_match:
				method_name = void_match.group(1)
				if not method_name.startswith("On"):
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message="Use 'async Task' instead of 'async void'. "
								"async void methods can't be awaited and swallow exceptions.",
						severity="error",
						rule_id="CS-ASYNC-001",
						original_line=line,
						suggested_fix=re.sub(r'\basync\s+void\b', 'async Task', line, count=1),
					))

			# CS-ASYNC-002: async methods should end in "Async" or "AsyncCore"
			async_match = self._ASYNC_METHOD_PATTERN.search(stripped)
			if async_match:
				method_name = async_match.group(1)
				# Exclude event handlers (start with "On"), Main entry point
				if (not method_name.startswith("On")
						and method_name != "Main"
						and not method_name.endswith("Async")
						and not method_name.endswith("AsyncCore")):
					comments.append(ReviewComment(
						file_path=file_path,
						line_number=line_num,
						message=f"Async methods should be suffixed with 'Async' "
								f"(e.g., {method_name}Async).",
						severity="warning",
						rule_id="CS-ASYNC-002",
					))

		return comments


class CSharpMagicNumberRules(BaseRule):
	"""Detects magic numbers used directly in C# expressions."""

	file_patterns = [".cs"]
	category = "C# Quality"

	# Numbers allowed without a named constant
	_ALLOWED_NUMBERS = {0.0, 1.0, -1.0, 2.0, 100.0}

	# Matches numeric literals (integer or decimal) not adjacent to other word chars or dots.
	# Captures the full number including an optional leading minus sign.
	_NUMBER_PATTERN = re.compile(r'(?<![.\w])(-?\d+(?:\.\d+)?)(?![\w.])')

	# Matches const declarations
	_CONST_PATTERN = re.compile(r'\bconst\b')

	# Matches enum member value assignments: MemberName = N,
	_ENUM_VALUE_PATTERN = re.compile(r'^\s*\w+\s*=\s*-?\d')

	# Matches array allocation: new SomeType[
	_ARRAY_SIZE_PATTERN = re.compile(r'\bnew\s+\w[\w<>,\s]*\[')

	# Matches attribute lines: [SomeAttribute(...)]
	_ATTRIBUTE_PATTERN = re.compile(r'^\s*\[')

	def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
		comments = []

		for line_num, line in changed_lines.items():
			stripped = line.strip()

			# Skip comments
			if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
				continue

			# Skip const declarations (they define the constant, not use a magic number)
			if self._CONST_PATTERN.search(stripped):
				continue

			# Skip enum value assignments
			if self._ENUM_VALUE_PATTERN.match(stripped):
				continue

			# Skip attribute lines (e.g., [MaxLength(255)], [Range(1, 100)])
			if self._ATTRIBUTE_PATTERN.match(stripped):
				continue

			# Skip array size declarations: new X[N]
			if self._ARRAY_SIZE_PATTERN.search(stripped):
				continue

			# Remove string literals to avoid false positives inside strings
			cleaned = re.sub(r'"[^"]*"', '""', stripped)
			cleaned = re.sub(r"'[^']*'", "''", cleaned)
			# Remove inline comments
			cleaned = re.sub(r'//.*$', '', cleaned)

			for match in self._NUMBER_PATTERN.finditer(cleaned):
				raw = match.group(1)
				try:
					value = float(raw)
				except ValueError:
					continue
				# Only flag numbers whose absolute value is strictly greater than 1
				# and not in the explicit allow-list
				if abs(value) <= 1.0 or value in self._ALLOWED_NUMBERS:
					continue
				comments.append(ReviewComment(
					file_path=file_path,
					line_number=line_num,
					message="Avoid magic numbers. Consider defining a named constant.",
					severity="suggestion",
					rule_id="CS-MAGIC-001",
				))
				break  # One warning per line is sufficient

		return comments

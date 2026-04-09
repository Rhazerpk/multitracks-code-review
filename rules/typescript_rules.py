"""TypeScript and JavaScript rules for the code review bot."""

import re

from .base import BaseRule, ReviewComment


class TypeScriptConsoleLogRule(BaseRule):
    """Detect console.log/debug/info/warn/error calls in production TS/JS code."""

    file_patterns = [".ts", ".tsx", ".js", ".jsx"]
    category = "typescript"

    _pattern = re.compile(r"\bconsole\.(log|debug|info|warn|error)\s*\(")
    _test_path = re.compile(r"(test|spec|__tests__|\.test\.|\.spec\.)", re.IGNORECASE)

    def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
        # Skip test files
        if self._test_path.search(file_path):
            return []

        comments = []
        for line_num, line in changed_lines.items():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if self._pattern.search(line):
                comments.append(ReviewComment(
                    file_path=file_path,
                    line_number=line_num,
                    message="Avoid `console.log` in production code. Use a structured logger instead.",
                    severity="warning",
                    rule_id="TS-001",
                    original_line=line.rstrip(),
                ))
        return comments


class TypeScriptAsyncWithoutTryCatchRule(BaseRule):
    """
    Detect await calls in changed lines where no try/catch is visible.

    Note: This is a heuristic — rules only see added lines, not the full file.
    If the try block was not changed, this may produce false positives.
    Use severity 'suggestion' to reduce noise.
    """

    file_patterns = [".ts", ".tsx", ".js", ".jsx"]
    category = "typescript"

    _await_pattern = re.compile(r"\bawait\s+")
    _try_pattern = re.compile(r"\btry\s*\{")

    def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
        all_lines = "\n".join(changed_lines.values())
        has_try = bool(self._try_pattern.search(all_lines))

        if has_try:
            return []

        comments = []
        for line_num, line in changed_lines.items():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            if self._await_pattern.search(line):
                comments.append(ReviewComment(
                    file_path=file_path,
                    line_number=line_num,
                    message=(
                        "Async call without a visible try/catch in changed lines. "
                        "Unhandled promise rejections can crash Node.js processes."
                    ),
                    severity="suggestion",
                    rule_id="TS-002",
                    original_line=line.rstrip(),
                ))
        return comments


class TypeScriptAnyTypeRule(BaseRule):
    """Detect explicit `any` type usage in TypeScript files."""

    file_patterns = [".ts", ".tsx"]
    category = "typescript"

    _pattern = re.compile(r":\s*any\b|<any>")

    def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
        comments = []
        for line_num, line in changed_lines.items():
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if self._pattern.search(line):
                comments.append(ReviewComment(
                    file_path=file_path,
                    line_number=line_num,
                    message=(
                        "Avoid using `any` type. Prefer explicit types or `unknown` for safer TypeScript."
                    ),
                    severity="suggestion",
                    rule_id="TS-003",
                    original_line=line.rstrip(),
                ))
        return comments


class TypeScriptUnusedImportRule(BaseRule):
    """
    Detect named imports that don't appear elsewhere in the changed lines.

    Note: Partial check — only sees added lines, not the full file.
    May produce false positives when the import is used in unchanged code.
    """

    file_patterns = [".ts", ".tsx", ".js", ".jsx"]
    category = "typescript"

    _import_pattern = re.compile(r"^import\s+\{([^}]+)\}\s+from")

    def analyze(self, file_path: str, changed_lines: dict[int, str]) -> list[ReviewComment]:
        comments = []
        all_text = "\n".join(changed_lines.values())

        for line_num, line in changed_lines.items():
            match = self._import_pattern.match(line.strip())
            if not match:
                continue

            names_raw = match.group(1)
            names = [n.strip().split(" as ")[0].strip() for n in names_raw.split(",")]

            for name in names:
                if not name:
                    continue
                # Count occurrences: more than 1 means it's used outside the import line itself
                occurrences = len(re.findall(r"\b" + re.escape(name) + r"\b", all_text))
                if occurrences <= 1:
                    comments.append(ReviewComment(
                        file_path=file_path,
                        line_number=line_num,
                        message=(
                            f"Import `{name}` may be unused in the changed lines. "
                            "Verify it's used elsewhere in the file."
                        ),
                        severity="suggestion",
                        rule_id="TS-004",
                        original_line=line.rstrip(),
                    ))
        return comments

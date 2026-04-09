"""
Diff parser for extracting changed files and lines from a GitHub PR.

Parses unified diff format to identify exactly which lines were added
or modified, so the reviewer only analyzes relevant changes.
"""

from dataclasses import dataclass, field


@dataclass
class ChangedFile:
	"""Represents a file changed in a PR with its modified lines."""
	path: str
	changed_lines: dict[int, str] = field(default_factory=dict)
	# Maps line numbers in the diff to their position for GitHub review API
	diff_positions: dict[int, int] = field(default_factory=dict)


def parse_diff(diff_text: str) -> list[ChangedFile]:
	"""
	Parse a unified diff string and extract changed files with their added lines.

	Only added lines (prefixed with '+') are extracted, since those are the
	lines that need review. Removed lines are ignored.

	Args:
		diff_text: Raw unified diff output.

	Returns:
		List of ChangedFile objects with their changed lines.
	"""
	files = []
	current_file = None
	current_line_num = 0
	diff_position = 0

	for line in diff_text.split("\n"):
		# New file header
		if line.startswith("diff --git"):
			if current_file and current_file.changed_lines:
				files.append(current_file)
			current_file = None
			diff_position = 0
			continue

		# File path from +++ header
		if line.startswith("+++ b/"):
			file_path = line[6:]
			current_file = ChangedFile(path=file_path)
			diff_position = 0
			continue

		# Skip deleted files
		if line.startswith("+++ /dev/null"):
			current_file = None
			continue

		# Hunk header: @@ -old_start,old_count +new_start,new_count @@
		if line.startswith("@@") and current_file is not None:
			# Extract the new file line number
			try:
				hunk_info = line.split("+")[1].split("@@")[0].strip()
				if "," in hunk_info:
					current_line_num = int(hunk_info.split(",")[0]) - 1
				else:
					current_line_num = int(hunk_info) - 1
			except (IndexError, ValueError):
				current_line_num = 0
			diff_position += 1
			continue

		if current_file is None:
			continue

		# Added line
		if line.startswith("+"):
			current_line_num += 1
			diff_position += 1
			content = line[1:]  # Remove the '+' prefix
			current_file.changed_lines[current_line_num] = content
			current_file.diff_positions[current_line_num] = diff_position

		# Context line (unchanged)
		elif line.startswith(" "):
			current_line_num += 1
			diff_position += 1

		# Removed line (don't increment new file line number)
		elif line.startswith("-"):
			diff_position += 1

	# Don't forget the last file
	if current_file and current_file.changed_lines:
		files.append(current_file)

	return files


def filter_reviewable_files(files: list[ChangedFile]) -> list[ChangedFile]:
	"""
	Filter out files that should not be reviewed.

	Excludes generated files, binaries, configuration templates,
	and other non-reviewable content.
	"""
	excluded_extensions = {
		".dll", ".exe", ".pdb", ".bin", ".obj",
		".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
		".woff", ".woff2", ".ttf", ".eot",
		".min.js", ".min.css",
		".map",
		".dacpac", ".nupkg",
		".lock",
	}

	excluded_paths = {
		"packages/",
		"node_modules/",
		"bin/",
		"obj/",
		".nuget/",
		".vs/",
	}

	reviewable_extensions = {
		".cs", ".sql", ".config", ".json", ".xml",
		".yml", ".yaml", ".csproj", ".props",
		".js", ".ts", ".css", ".html", ".cshtml",
		".aspx", ".ascx", ".master",
	}

	filtered = []
	for f in files:
		path_lower = f.path.lower()

		# Skip excluded extensions
		if any(path_lower.endswith(ext) for ext in excluded_extensions):
			continue

		# Skip excluded directories
		if any(part in path_lower for part in excluded_paths):
			continue

		# Only include known reviewable extensions
		if any(path_lower.endswith(ext) for ext in reviewable_extensions):
			filtered.append(f)

	return filtered

"""
Code Review Orchestrator.

Coordinates the analysis pipeline: fetches the diff, parses changed files,
runs all applicable rules, deduplicates findings, and posts the review.
"""

from diff_parser import ChangedFile, filter_reviewable_files, parse_diff
from github_client import GitHubClient
from rules import ALL_RULES
from rules.base import ReviewComment


class CodeReviewer:
	"""Orchestrates the code review process for a pull request."""

	# Maximum comments to post (avoid flooding PRs)
	MAX_COMMENTS = 30

	def __init__(self, github_client: GitHubClient):
		self.github = github_client
		self.rules = ALL_RULES

	def review(self) -> int:
		"""
		Run the full review pipeline.

		Returns:
			Number of issues found (0 means clean).
		"""
		# Step 1: Fetch PR diff and metadata
		print("Fetching PR diff...")
		diff_text = self.github.get_pr_diff()
		pr_info = self.github.get_pr_info()
		commit_sha = pr_info["head"]["sha"]

		print(f"PR #{pr_info['number']}: {pr_info['title']}")
		print(f"Head commit: {commit_sha[:8]}")

		# Step 2: Create check run (marks the PR check as "in progress")
		check_run_id = None
		try:
			check_run_id = self.github.create_check_run(commit_sha)
			print(f"Check run created: {check_run_id}")
		except Exception as e:
			print(f"WARNING: Could not create check run: {e}")

		try:
			# Step 3: Parse the diff into changed files
			print("Parsing diff...")
			changed_files = parse_diff(diff_text)
			reviewable_files = filter_reviewable_files(changed_files)

			print(f"Found {len(changed_files)} changed files, "
				  f"{len(reviewable_files)} reviewable.")

			if not reviewable_files:
				print("No reviewable files found. Posting clean review.")
				self.github.post_review([], {}, commit_sha)
				if check_run_id:
					self.github.update_check_run(check_run_id, [])
				return 0

			# Step 4: Run all rules against each file
			print("Running analysis rules...")
			all_comments = []
			for file in reviewable_files:
				file_comments = self._analyze_file(file)
				all_comments.extend(file_comments)

			# Step 5: Deduplicate and prioritize
			all_comments = self._deduplicate(all_comments)
			all_comments = self._prioritize(all_comments)

			print(f"Found {len(all_comments)} issues.")

			# Step 6: Build diff position mapping for inline comments
			diff_positions = {
				f.path: f.diff_positions for f in reviewable_files
			}

			# Step 7: Post inline review comments (existing behavior)
			print("Posting review...")
			self.github.post_review(all_comments, diff_positions, commit_sha)

			# Step 8: Update the check run with final conclusion + annotations
			if check_run_id:
				self.github.update_check_run(check_run_id, all_comments)

			return len(all_comments)

		except Exception as e:
			if check_run_id:
				try:
					self.github.fail_check_run(check_run_id, str(e))
				except Exception:
					pass  # Don't shadow the original error
			raise

	def _analyze_file(self, file: ChangedFile) -> list[ReviewComment]:
		"""Run all applicable rules against a single file."""
		comments = []
		for rule in self.rules:
			if rule.applies_to(file.path):
				try:
					rule_comments = rule.analyze(file.path, file.changed_lines)
					comments.extend(rule_comments)
				except Exception as e:
					print(f"WARNING: Rule {rule.__class__.__name__} failed on "
						  f"{file.path}: {e}")
		return comments

	def _deduplicate(self, comments: list[ReviewComment]) -> list[ReviewComment]:
		"""Remove duplicate comments on the same line with the same rule."""
		seen = set()
		unique = []
		for comment in comments:
			key = (comment.file_path, comment.line_number, comment.rule_id)
			if key not in seen:
				seen.add(key)
				unique.append(comment)
		return unique

	def _prioritize(self, comments: list[ReviewComment]) -> list[ReviewComment]:
		"""
		Sort by severity and limit to MAX_COMMENTS.

		Priority: error > warning > suggestion
		"""
		severity_order = {"error": 0, "warning": 1, "suggestion": 2}
		comments.sort(key=lambda c: severity_order.get(c.severity, 3))
		if len(comments) > self.MAX_COMMENTS:
			print(f"Truncating from {len(comments)} to {self.MAX_COMMENTS} comments.")
		return comments[:self.MAX_COMMENTS]

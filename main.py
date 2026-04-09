#!/usr/bin/env python3
"""
MultiTracks.com Auto Code Review Bot

Entry point for the GitHub Action. Fetches a PR diff, analyzes it against
the project's coding standards, and posts review comments.

Usage:
	Triggered automatically via GitHub Actions on pull_request events.
	Can also be run locally for testing:

		export GITHUB_TOKEN="your-token"
		export GITHUB_REPOSITORY="rhazerpk/multitracks.com"
		export PR_NUMBER="123"
		python main.py

	Or test against a local diff file:
		python main.py --local path/to/diff.patch
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load .env file from the same directory as this script
load_dotenv(Path(__file__).parent / ".env")

from diff_parser import filter_reviewable_files, parse_diff
from github_client import GitHubClient
from reviewer import CodeReviewer
from rules import ALL_RULES


def run_github_review() -> int:
	"""Run the review against a GitHub PR."""
	client = GitHubClient()
	reviewer = CodeReviewer(client)
	return reviewer.review()


def run_local_review(diff_path: str) -> int:
	"""Run the review against a local diff file (for testing)."""
	print(f"Reading diff from: {diff_path}")

	with open(diff_path, "r", encoding="utf-8", errors="replace") as f:
		diff_text = f.read()

	changed_files = parse_diff(diff_text)
	reviewable_files = filter_reviewable_files(changed_files)

	print(f"Parsed {len(changed_files)} files, {len(reviewable_files)} reviewable.\n")

	total_issues = 0

	for file in reviewable_files:
		file_comments = []
		for rule in ALL_RULES:
			if rule.applies_to(file.path):
				try:
					file_comments.extend(rule.analyze(file.path, file.changed_lines))
				except Exception as e:
					print(f"  WARNING: {rule.__class__.__name__} failed: {e}")

		if file_comments:
			print(f"\n{'=' * 60}")
			print(f"FILE: {file.path} ({len(file_comments)} issues)")
			print(f"{'=' * 60}")

			for comment in file_comments:
				print(f"  Line {comment.line_number}: {comment.format_message()}")

			total_issues += len(file_comments)

	print(f"\n{'=' * 60}")
	print(f"TOTAL: {total_issues} issues found across {len(reviewable_files)} files.")
	print(f"{'=' * 60}")

	return total_issues


def main():
	parser = argparse.ArgumentParser(description="MultiTracks.com Auto Code Review Bot")
	parser.add_argument(
		"--local",
		type=str,
		help="Path to a local diff file for testing (instead of fetching from GitHub)",
	)
	parser.add_argument(
		"--exit-code",
		action="store_true",
		help="Exit with non-zero code if issues are found (useful for CI gates)",
	)

	args = parser.parse_args()

	try:
		if args.local:
			issue_count = run_local_review(args.local)
		else:
			issue_count = run_github_review()

		if args.exit_code and issue_count > 0:
			sys.exit(1)

	except Exception as e:
		print(f"FATAL: Code review failed: {e}", file=sys.stderr)
		# Don't block the PR on bot failures
		sys.exit(0)


if __name__ == "__main__":
	main()

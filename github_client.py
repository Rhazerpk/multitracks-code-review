"""
GitHub API client for posting review comments on pull requests.

Uses the GitHub REST API to create pull request reviews with
inline comments on specific lines of changed files.
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone

import requests

logger = logging.getLogger("code_review.github")

from rules.base import ReviewComment


class GitHubClient:
	"""Client for interacting with the GitHub Pull Request Review API."""

	API_BASE = "https://api.github.com"

	def __init__(self):
		self.token = os.environ.get("GITHUB_TOKEN", "")
		self.repo = os.environ.get("GITHUB_REPOSITORY", "")
		self.pr_number = os.environ.get("PR_NUMBER", "")

		if not all([self.token, self.repo, self.pr_number]):
			logger.warning(
				"Missing GitHub environment variables. "
				"Ensure GITHUB_TOKEN, GITHUB_REPOSITORY, and PR_NUMBER are set."
			)

		self.headers = {
			"Accept": "application/vnd.github.v3+json",
			"Authorization": f"Bearer {self.token}",
			"X-GitHub-Api-Version": "2022-11-28",
		}

	def get_pr_diff(self) -> str:
		"""Fetch the diff for the pull request."""
		url = f"{self.API_BASE}/repos/{self.repo}/pulls/{self.pr_number}"
		headers = {**self.headers, "Accept": "application/vnd.github.v3.diff"}

		response = requests.get(url, headers=headers, timeout=30)
		response.raise_for_status()
		return response.text

	def get_pr_info(self) -> dict:
		"""Fetch PR metadata (title, head SHA, etc.)."""
		url = f"{self.API_BASE}/repos/{self.repo}/pulls/{self.pr_number}"
		response = requests.get(url, headers=self.headers, timeout=30)
		response.raise_for_status()
		return response.json()

	def post_review(
		self,
		comments: list[ReviewComment],
		diff_positions: dict[str, dict[int, int]],
		commit_sha: str,
	) -> None:
		"""
		Post a review with inline comments on the PR.

		Args:
			comments: List of review comments to post.
			diff_positions: Mapping of file_path -> {line_number: diff_position}.
			commit_sha: The HEAD commit SHA of the PR.
		"""
		if not comments:
			self._post_clean_review(commit_sha)
			return

		# Build review comment objects for the API
		review_comments = []
		unpostable_comments = []

		for comment in comments:
			positions = diff_positions.get(comment.file_path, {})
			position = positions.get(comment.line_number)

			if position is not None:
				review_comments.append({
					"path": comment.file_path,
					"position": position,
					"body": comment.format_message(),
				})
			else:
				unpostable_comments.append(comment)

		# Determine the overall review event based on severity
		has_errors = any(c.severity == "error" for c in comments)
		event = "REQUEST_CHANGES" if has_errors else "COMMENT"

		# Build the review body with summary
		body = self._build_summary(comments, unpostable_comments)

		# Post the review
		url = f"{self.API_BASE}/repos/{self.repo}/pulls/{self.pr_number}/reviews"
		payload = {
			"commit_id": commit_sha,
			"body": body,
			"event": event,
			"comments": review_comments[:50],  # GitHub API limit
		}

		response = requests.post(
			url, headers=self.headers, json=payload, timeout=30,
		)

		if response.status_code == 422:
			logger.warning("Could not post inline review (422). Falling back to summary comment.")
			self._post_summary_comment(body, comments)
		else:
			response.raise_for_status()
			logger.info("Review posted: %s with %d inline comments.", event, len(review_comments))

	def _post_clean_review(self, commit_sha: str) -> None:
		"""Post a review indicating no issues were found."""
		url = f"{self.API_BASE}/repos/{self.repo}/pulls/{self.pr_number}/reviews"
		payload = {
			"commit_id": commit_sha,
			"body": "## Auto Code Review\n\nNo issues found. Code looks good!",
			"event": "COMMENT",
		}
		response = requests.post(
			url, headers=self.headers, json=payload, timeout=30,
		)
		response.raise_for_status()
		logger.info("Clean review posted — no issues found.")

	def _post_summary_comment(self, body: str, comments: list[ReviewComment]) -> None:
		"""Fallback: post all findings as a single PR comment."""
		# Add inline details to the body
		details = "\n\n### Details\n\n"
		for c in comments:
			details += f"- **{c.file_path}:{c.line_number}** — {c.format_message()}\n"

		url = f"{self.API_BASE}/repos/{self.repo}/issues/{self.pr_number}/comments"
		payload = {"body": body + details}
		response = requests.post(
			url, headers=self.headers, json=payload, timeout=30,
		)
		response.raise_for_status()
		logger.info("Summary comment posted as fallback.")

	def create_check_run(self, commit_sha: str) -> int:
		"""
		Create a check run in 'in_progress' state at the start of analysis.

		Returns the check run ID needed to update it later.
		"""
		url = f"{self.API_BASE}/repos/{self.repo}/check-runs"
		payload = {
			"name": "MultiTracks Code Review",
			"head_sha": commit_sha,
			"status": "in_progress",
			"started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
		}
		response = requests.post(url, headers=self.headers, json=payload, timeout=30)
		response.raise_for_status()
		check_run_id = response.json()["id"]
		logger.info("Check run created: id=%d", check_run_id)
		return check_run_id

	def update_check_run(
		self,
		check_run_id: int,
		comments: list[ReviewComment],
	) -> None:
		"""
		Update the check run with final results and inline annotations.

		Conclusion mapping:
		  errors present  → "failure"  (blocks merge when branch protection requires it)
		  warnings only   → "neutral"  (visible but does not block)
		  clean           → "success"  (green checkmark)
		"""
		has_errors = any(c.severity == "error" for c in comments)
		if not comments:
			conclusion = "success"
		elif has_errors:
			conclusion = "failure"
		else:
			conclusion = "neutral"

		level_map = {"error": "failure", "warning": "warning", "suggestion": "notice"}
		annotations = [
			{
				"path": c.file_path,
				"start_line": c.line_number,
				"end_line": c.line_number,
				"annotation_level": level_map.get(c.severity, "notice"),
				"message": c.format_message(),
				"title": c.rule_id,
			}
			for c in comments[:50]  # GitHub Checks API limit per request
		]

		url = f"{self.API_BASE}/repos/{self.repo}/check-runs/{check_run_id}"
		payload = {
			"status": "completed",
			"completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
			"conclusion": conclusion,
			"output": {
				"title": self._check_run_title(comments),
				"summary": self._build_summary(comments, []),
				"annotations": annotations,
			},
		}
		response = requests.patch(url, headers=self.headers, json=payload, timeout=30)
		response.raise_for_status()
		logger.info(
			"Check run updated: conclusion=%s, annotations=%d",
			conclusion,
			len(annotations),
		)

	def fail_check_run(self, check_run_id: int, error_message: str) -> None:
		"""Mark a check run as failed due to an unexpected bot error."""
		url = f"{self.API_BASE}/repos/{self.repo}/check-runs/{check_run_id}"
		payload = {
			"status": "completed",
			"completed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
			"conclusion": "failure",
			"output": {
				"title": "Code Review Bot encountered an error",
				"summary": f"The review bot failed unexpectedly:\n\n```\n{error_message}\n```",
			},
		}
		requests.patch(url, headers=self.headers, json=payload, timeout=30)

	def _check_run_title(self, comments: list[ReviewComment]) -> str:
		"""Build a short title line for the check run output."""
		if not comments:
			return "No issues found — code looks good"
		errors = sum(1 for c in comments if c.severity == "error")
		warnings = sum(1 for c in comments if c.severity == "warning")
		suggestions = len(comments) - errors - warnings
		parts = []
		if errors:
			parts.append(f"{errors} error{'s' if errors != 1 else ''}")
		if warnings:
			parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
		if suggestions:
			parts.append(f"{suggestions} suggestion{'s' if suggestions != 1 else ''}")
		return ", ".join(parts) + " found"

	def _build_summary(
		self,
		comments: list[ReviewComment],
		unpostable: list[ReviewComment],
	) -> str:
		"""Build a markdown summary of all findings."""
		errors = [c for c in comments if c.severity == "error"]
		warnings = [c for c in comments if c.severity == "warning"]
		suggestions = [c for c in comments if c.severity == "suggestion"]

		lines = ["## Auto Code Review\n"]
		lines.append(f"Found **{len(comments)}** issue(s) across the changed files.\n")
		lines.append("| Severity | Count |")
		lines.append("|----------|-------|")
		if errors:
			lines.append(f"| Errors | {len(errors)} |")
		if warnings:
			lines.append(f"| Warnings | {len(warnings)} |")
		if suggestions:
			lines.append(f"| Suggestions | {len(suggestions)} |")

		# Group by category
		categories = {}
		for c in comments:
			cat = c.rule_id.split("-")[0]
			categories.setdefault(cat, []).append(c)

		lines.append("\n### By Category\n")
		category_names = {
			"CS": "C# Standards",
			"SQL": "SQL Standards",
			"SEC": "Security",
			"GEN": "General Quality",
		}
		for cat, cat_comments in sorted(categories.items()):
			name = category_names.get(cat, cat)
			lines.append(f"- **{name}**: {len(cat_comments)} issue(s)")

		if unpostable:
			lines.append(f"\n*{len(unpostable)} comment(s) could not be posted inline "
						  f"(lines not part of the diff).*")

		lines.append("\n---")
		lines.append("*Automated review by MultiTracks Code Review Bot*")

		return "\n".join(lines)

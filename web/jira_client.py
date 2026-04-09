"""
Jira REST API client for fetching issue details.

Connects to the Jira instance to retrieve issue information
including title, description, type, and acceptance criteria.

Configuration via environment variables:
    JIRA_BASE_URL: Your Jira instance URL (e.g., https://multitracks.atlassian.net)
    JIRA_EMAIL: Jira account email
    JIRA_API_TOKEN: Jira API token (generate at https://id.atlassian.net/manage-profile/security/api-tokens)
"""

import logging
import os
from base64 import b64encode

import requests

logger = logging.getLogger("code_review.jira")


class JiraClient:
	"""Client for the Jira REST API."""

	def __init__(self):
		self.base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
		self.email = os.environ.get("JIRA_EMAIL", "")
		self.api_token = os.environ.get("JIRA_API_TOKEN", "")

	def is_configured(self) -> bool:
		"""Check if Jira credentials are set."""
		return bool(self.base_url and self.email and self.api_token)

	def _get_headers(self) -> dict:
		"""Build authentication headers for Jira Cloud API."""
		credentials = b64encode(f"{self.email}:{self.api_token}".encode()).decode()
		return {
			"Authorization": f"Basic {credentials}",
			"Accept": "application/json",
			"Content-Type": "application/json",
		}

	def get_issue(self, issue_key: str) -> dict | None:
		"""
		Fetch a Jira issue by key (e.g., MT-145199).

		Returns a simplified dict with title, description, type, and status.
		Returns None if the issue is not found (404).
		Raises an exception if Jira is unreachable or not configured.
		"""
		if not self.is_configured():
			logger.warning("Jira is not configured — set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN")
			raise RuntimeError(
				"Jira is not configured. Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN environment variables."
			)

		url = f"{self.base_url}/rest/api/3/issue/{issue_key}"
		params = {"fields": "summary,description,issuetype,status,assignee,priority"}

		logger.info("Fetching Jira issue %s", issue_key)
		try:
			response = requests.get(
				url,
				headers=self._get_headers(),
				params=params,
				timeout=15,
			)
			response.raise_for_status()
			data = response.json()

			fields = data.get("fields", {})
			description = self._extract_description(fields.get("description"))

			logger.info("Successfully fetched Jira issue %s: %s", issue_key, fields.get("summary", ""))
			return {
				"key": issue_key,
				"title": fields.get("summary", ""),
				"description": description,
				"type": fields.get("issuetype", {}).get("name", "Unknown"),
				"status": fields.get("status", {}).get("name", "Unknown"),
				"assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
				"priority": fields.get("priority", {}).get("name", "Medium"),
			}

		except requests.exceptions.HTTPError as e:
			if e.response.status_code == 404:
				logger.warning("Jira issue %s not found (404)", issue_key)
				return None
			if e.response.status_code == 401:
				logger.error("Jira authentication failed (401) — check JIRA_EMAIL and JIRA_API_TOKEN")
				raise RuntimeError("Jira authentication failed. Check your email and API token.") from e
			if e.response.status_code == 429:
				logger.error("Jira rate limit exceeded (429)")
				raise RuntimeError("Jira rate limit exceeded. Try again in a moment.") from e
			logger.error("Jira HTTP error for %s: %s", issue_key, e)
			raise RuntimeError(f"Jira returned an error: {e.response.status_code}") from e
		except requests.exceptions.ConnectionError as e:
			logger.error("Cannot reach Jira at %s: %s", self.base_url, e)
			raise RuntimeError(f"Cannot connect to Jira at {self.base_url}. Check the URL and network.") from e
		except requests.exceptions.Timeout:
			logger.error("Jira request timed out for %s", issue_key)
			raise RuntimeError("Jira request timed out. The server may be slow — try again.") from None

	def get_my_issues(self, statuses: list[str] | None = None) -> list[dict]:
		"""
		Fetch issues assigned to the current user filtered by status.

		Returns a list of simplified issue dicts sorted by last updated.
		Raises RuntimeError if Jira is not configured.
		"""
		if not self.is_configured():
			raise RuntimeError(
				"Jira is not configured. Set JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN."
			)

		if statuses is None:
			statuses = [
				"DOTNET DEV QA COMPLETE",
				"QA Staging Resolved",
				"QA Test Resolved",
				"DEV QA Closed",
				"In Progress",
				"Code Review",
			]

		# Build JQL — quote each status to handle spaces/slashes
		quoted = ", ".join(f'"{s}"' for s in statuses)
		jql = f"assignee = currentUser() AND status in ({quoted}) ORDER BY updated DESC"

		url = f"{self.base_url}/rest/api/3/search/jql"
		body = {
			"jql": jql,
			"maxResults": 25,
			"fields": ["summary", "issuetype", "status", "priority", "assignee"],
		}

		logger.info("Fetching active issues for current user")
		try:
			response = requests.post(
				url,
				headers=self._get_headers(),
				json=body,
				timeout=15,
			)
			response.raise_for_status()
			data = response.json()

			issues = []
			for item in data.get("issues", []):
				fields = item.get("fields", {})
				issues.append({
					"key": item["key"],
					"title": fields.get("summary", ""),
					"type": fields.get("issuetype", {}).get("name", "Task"),
					"status": fields.get("status", {}).get("name", "Unknown"),
					"priority": fields.get("priority", {}).get("name", "Medium"),
				})
			return issues

		except requests.exceptions.HTTPError as e:
			if e.response.status_code == 401:
				raise RuntimeError("Jira authentication failed. Check your email and API token.") from e
			logger.error("Jira HTTP error fetching my issues: %s", e)
			raise RuntimeError(f"Jira returned an error: {e.response.status_code}") from e
		except requests.exceptions.ConnectionError as e:
			raise RuntimeError(f"Cannot connect to Jira at {self.base_url}.") from e
		except requests.exceptions.Timeout:
			raise RuntimeError("Jira request timed out.") from None

	def _extract_description(self, description_field) -> str:
		"""
		Extract plain text from Jira's Atlassian Document Format (ADF).

		ADF is a nested JSON structure used in Jira Cloud. This recursively
		extracts all text content into a readable string.
		"""
		if not description_field:
			return ""

		if isinstance(description_field, str):
			return description_field

		def extract_text(node: dict) -> str:
			if node.get("type") == "text":
				return node.get("text", "")

			parts = []
			for child in node.get("content", []):
				parts.append(extract_text(child))

			# Add line breaks between block-level elements
			if node.get("type") in ("paragraph", "heading", "bulletList", "listItem"):
				return "\n".join(parts)

			return " ".join(parts)

		return extract_text(description_field).strip()

	def _mock_issue(self, issue_key: str) -> dict:
		"""
		Return a mock issue for demo/development when Jira is not configured.

		Parses the issue key to generate contextually relevant mock data.
		"""
		# Common issue patterns based on MT ticket naming
		mock_descriptions = {
			"sync": "Implement sync license functionality. Update the sync license settings and ensure cache invalidation works correctly when settings are changed.",
			"search": "Improve search functionality in the API. Update search indexing and ensure results are properly cached and invalidated.",
			"subscription": "Fix subscription billing issue. Ensure monthly debit processing handles edge cases correctly.",
			"playback": "Update playback features. Modify the playback service and controller to support new audio formats.",
			"chart": "ChartBuilder improvements. Update chart generation logic and add support for new chart types.",
			"cache": "Fix cache invalidation issues. Ensure Redis cache keys are properly invalidated when data changes.",
			"account": "Account management update. Fix authentication flow and update user profile handling.",
		}

		# Try to match based on issue key or provide a generic description
		description = "General feature implementation or bug fix. Review the PR changes to ensure they align with the requirements and follow coding standards."
		for keyword, desc in mock_descriptions.items():
			if keyword in issue_key.lower():
				description = desc
				break

		return {
			"key": issue_key,
			"title": f"[{issue_key}] Feature/Bug Implementation",
			"description": description,
			"type": "Task",
			"status": "In Progress",
			"assignee": "Developer",
			"priority": "Medium",
		}

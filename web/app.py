"""
MultiTracks Code Review Dashboard — Backend API.

FastAPI server that orchestrates the review pipeline:
1. Fetches Jira issue details (title, description, acceptance criteria)
2. Finds the linked GitHub PR
3. Runs static analysis rules against the diff
4. Performs scope validation (do changes match the issue?)
5. Returns structured results for the dashboard UI

New in v3.0:
- SQLite persistence (reviews survive server restarts)
- WebSocket real-time progress during review
- False positive suppression via `// review-ignore: RULE-ID`
- /api/analytics — historical statistics + chart data
- /api/export/{uuid} — self-contained HTML report
"""

import logging
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env from parent directory (code-review/)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# Add project root and web/ directory to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from db import get_analytics, get_history, get_review_by_uuid, init_db, save_review
from diff_parser import ChangedFile, filter_reviewable_files, parse_diff
from jira_client import JiraClient
from rules import ALL_RULES
from rules.base import ReviewComment

# --- Logging ---

logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("code_review.api")

# --- Initialize database on startup ---

init_db()

app = FastAPI(
	title="MultiTracks Code Review Dashboard",
	version="3.0.0",
)

app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_methods=["*"],
	allow_headers=["*"],
)

# Serve static files (HTML, CSS, JS)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# --- Clients ---

jira = JiraClient()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "Rhazerpk/multitracks.com")

# --- In-memory cache (key -> (data, expires_at)) ---

_cache: dict[str, tuple] = {}


def cache_get(key: str):
	entry = _cache.get(key)
	if entry is None:
		return None
	data, expires_at = entry
	if datetime.utcnow() > expires_at:
		del _cache[key]
		return None
	return data


def cache_set(key: str, data, ttl_minutes: int = 5):
	_cache[key] = (data, datetime.utcnow() + timedelta(minutes=ttl_minutes))


# --- Rate limiting (ip -> list of timestamps) ---

_rate_limit_store: dict[str, list] = defaultdict(list)
RATE_LIMIT_MAX = 10       # requests
RATE_LIMIT_WINDOW = 60    # seconds


def check_rate_limit(ip: str) -> bool:
	"""Return True if request is allowed, False if rate limit exceeded."""
	now = time.time()
	window_start = now - RATE_LIMIT_WINDOW
	_rate_limit_store[ip] = [t for t in _rate_limit_store[ip] if t > window_start]
	if len(_rate_limit_store[ip]) >= RATE_LIMIT_MAX:
		return False
	_rate_limit_store[ip].append(now)
	return True


# --- WebSocket progress manager ---

class ProgressManager:
	"""Manages WebSocket connections for real-time progress updates during review."""

	def __init__(self):
		self._connections: dict[str, WebSocket] = {}

	async def connect(self, session_id: str, ws: WebSocket):
		await ws.accept()
		self._connections[session_id] = ws

	def disconnect(self, session_id: str):
		self._connections.pop(session_id, None)

	async def send(self, session_id: str | None, message: str):
		if not session_id:
			return
		ws = self._connections.get(session_id)
		if ws:
			try:
				await ws.send_json({"message": message})
			except Exception:
				self.disconnect(session_id)


progress_manager = ProgressManager()


# --- Models ---

class ReviewRequest(BaseModel):
	issue_key: str
	pr_number: int | None = None
	session_id: str | None = None  # WebSocket session for progress updates


class ReviewFileResult(BaseModel):
	file_path: str
	issues: list[dict]
	error_count: int
	warning_count: int
	suggestion_count: int


class ScopeValidation(BaseModel):
	in_scope: list[str]
	out_of_scope: list[str]
	missing: list[str]
	score: int  # 0-100


class ReviewResponse(BaseModel):
	review_uuid: str
	issue_key: str
	issue_title: str
	issue_description: str
	issue_type: str
	pr_number: int
	pr_title: str
	pr_url: str
	total_issues: int
	errors: int
	warnings: int
	suggestions: int
	suppressed_count: int
	files: list[ReviewFileResult]
	scope_validation: ScopeValidation
	summary: str


class ApplyFixRequest(BaseModel):
	file_path: str
	line_number: int
	original_line: str
	suggested_fix: str
	repo_root: str


class LocalReviewRequest(BaseModel):
	repo_path: str
	session_id: str | None = None


class PostCheckRunRequest(BaseModel):
	pr_number: int
	review_uuid: str


# --- Retry helper ---

def with_retry(fn, max_attempts: int = 3, backoff_seconds: float = 1.0):
	"""Call fn(), retrying up to max_attempts times on exception with backoff."""
	last_exc = None
	for attempt in range(1, max_attempts + 1):
		try:
			return fn()
		except Exception as exc:
			last_exc = exc
			if attempt < max_attempts:
				wait = backoff_seconds * attempt
				logger.warning("Attempt %d/%d failed: %s. Retrying in %.1fs…", attempt, max_attempts, exc, wait)
				time.sleep(wait)
	raise last_exc


# --- GitHub helpers ---

def github_get(url: str) -> dict:
	"""Make an authenticated GitHub API GET request."""
	import requests

	headers = {
		"Accept": "application/vnd.github.v3+json",
		"Authorization": f"Bearer {GITHUB_TOKEN}",
	}
	logger.debug("GitHub GET %s", url)
	response = requests.get(url, headers=headers, timeout=30)
	response.raise_for_status()
	return response.json()


def github_get_diff(pr_number: int) -> str:
	"""Fetch the unified diff for a PR."""
	import requests

	url = f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{pr_number}"
	headers = {
		"Accept": "application/vnd.github.v3.diff",
		"Authorization": f"Bearer {GITHUB_TOKEN}",
	}
	logger.info("Fetching diff for PR #%d", pr_number)
	response = requests.get(url, headers=headers, timeout=30)
	response.raise_for_status()
	return response.text


def find_pr_for_issue(issue_key: str) -> dict | None:
	"""
	Search GitHub PRs for one linked to the given Jira issue.
	Checks up to 2 pages (60 PRs) to handle busy repositories.
	"""
	import requests

	issue_key_lower = issue_key.lower()
	headers = {
		"Accept": "application/vnd.github.v3+json",
		"Authorization": f"Bearer {GITHUB_TOKEN}",
	}

	for page in range(1, 3):
		url = f"https://api.github.com/repos/{GITHUB_REPO}/pulls?state=all&per_page=30&page={page}"
		logger.debug("Searching PRs page %d for issue %s", page, issue_key)
		response = requests.get(url, headers=headers, timeout=30)
		if response.status_code != 200:
			break

		prs = response.json()
		if not prs:
			break

		for pr in prs:
			title = (pr.get("title") or "").lower()
			branch = (pr.get("head", {}).get("ref") or "").lower()
			body = (pr.get("body") or "").lower()
			if issue_key_lower in title or issue_key_lower in branch or issue_key_lower in body:
				logger.info("Found PR #%d for issue %s", pr["number"], issue_key)
				return pr

	logger.warning("No PR found for issue %s after searching 2 pages", issue_key)
	return None


# --- False positive suppression ---

_suppress_pattern = re.compile(r"review-ignore:\s*([\w-]+)", re.IGNORECASE)


def apply_suppression(
	comments: list[ReviewComment],
	changed_lines_by_file: dict[str, dict[int, str]],
) -> list[ReviewComment]:
	"""
	Mark comments as suppressed when a review-ignore directive is found on
	the same line or the immediately preceding changed line.

	Syntax:
	    // review-ignore: CS-NAME-002   (C#, JS, TS)
	    # review-ignore: SQL-BP-001     (SQL, config)
	"""
	for comment in comments:
		file_lines = changed_lines_by_file.get(comment.file_path, {})
		for line_num in (comment.line_number, comment.line_number - 1):
			line_content = file_lines.get(line_num, "")
			match = _suppress_pattern.search(line_content)
			if match and match.group(1).upper() == comment.rule_id.upper():
				comment.suppressed = True
				break
	return comments


# --- Analysis helpers ---

def run_static_analysis(
	diff_text: str,
) -> tuple[list[ReviewComment], dict[str, dict[int, str]]]:
	"""
	Run all rules against the parsed diff.

	Returns:
	    (comments, changed_lines_by_file) — the second value is needed for suppression.
	"""
	changed_files = parse_diff(diff_text)
	reviewable_files = filter_reviewable_files(changed_files)

	logger.info("Analyzing %d reviewable files", len(reviewable_files))

	all_comments = []
	changed_lines_by_file: dict[str, dict[int, str]] = {}

	for file in reviewable_files:
		changed_lines_by_file[file.path] = file.changed_lines
		for rule in ALL_RULES:
			if rule.applies_to(file.path):
				try:
					comments = rule.analyze(file.path, file.changed_lines)
					all_comments.extend(comments)
				except Exception as exc:
					logger.error("Rule %s failed on %s: %s", type(rule).__name__, file.path, exc)

	return all_comments, changed_lines_by_file


def validate_scope(issue_title: str, issue_description: str, changed_files: list[str], diff_summary: str) -> ScopeValidation:
	"""
	Validate that PR changes align with the Jira issue scope using keyword heuristics.
	"""
	description_lower = (issue_title + " " + issue_description).lower()

	in_scope = []
	out_of_scope = []

	scope_map = {
		"api": ["Web/api.multitracks.com/", "Tests.Core/Api/"],
		"search": ["Search/", "SearchAccess/", "SearchProcessor/"],
		"subscription": ["Subscriptions/", "SubscriptionsProcess/", "Tests.Subscriptions"],
		"billing": ["Subscriptions/", "Transactionator", "CartBuilder"],
		"payment": ["Transactionator", "Subscriptions/"],
		"chart": ["ChartBuilder", "ChartPro"],
		"playback": ["Playback/"],
		"rehearsal": ["RehearsalMix/", "rehearsalmix"],
		"account": ["account.multitracks.com/", "Security/"],
		"dashboard": ["dashboard.multitracks.com/"],
		"partner": ["partners.multitracks.com/", "Partners/"],
		"trinity": ["trinity.multitracks.com/", "Trinity"],
		"backoffice": ["backoffice.multitracks.com/"],
		"admin": ["backoffice.multitracks.com/", "Admin/"],
		"cache": ["CacheInvalidation", "Redis"],
		"email": ["Email/", "templates/"],
		"sync": ["Sync", "License"],
		"device": ["Device/"],
		"planning center": ["PlanningCenter/"],
		"cloud": ["Cloud/"],
		"database": ["DB/", "Stored Procedures"],
		"stored procedure": ["DB/dbo/Stored Procedures/"],
	}

	relevant_areas = set()
	for keyword, dirs in scope_map.items():
		if keyword in description_lower:
			relevant_areas.update(dirs)

	for file_path in changed_files:
		if not relevant_areas:
			in_scope.append(file_path)
			continue

		matched = any(area.lower() in file_path.lower() for area in relevant_areas)
		if matched:
			in_scope.append(file_path)
		else:
			shared_patterns = [
				"Core/DataAccess/", "Core/Utilities", "WebSite.Common/",
				"DB/dbo/Stored Procedures/", ".config", ".csproj",
			]
			is_shared = any(p.lower() in file_path.lower() for p in shared_patterns)
			if is_shared:
				in_scope.append(file_path)
			else:
				out_of_scope.append(file_path)

	total = len(in_scope) + len(out_of_scope)
	score = int((len(in_scope) / total) * 100) if total > 0 else 100

	missing = []
	for keyword, dirs in scope_map.items():
		if keyword in description_lower:
			area_touched = any(
				any(d.lower() in f.lower() for d in dirs)
				for f in changed_files
			)
			if not area_touched:
				missing.append(f"No changes found in {dirs[0]} (issue mentions '{keyword}')")

	return ScopeValidation(in_scope=in_scope, out_of_scope=out_of_scope, missing=missing, score=score)


def generate_summary(issue_title: str, total_issues: int, errors: int, warnings: int, scope_score: int) -> str:
	parts = []

	if errors == 0 and warnings == 0:
		parts.append("No coding standard violations found.")
	else:
		if errors > 0:
			parts.append(f"{errors} error(s) that should be fixed before merging.")
		if warnings > 0:
			parts.append(f"{warnings} warning(s) to review.")

	if scope_score == 100:
		parts.append("All changes appear to be within the scope of the issue.")
	elif scope_score >= 75:
		parts.append("Most changes align with the issue scope, but some files may be unrelated.")
	else:
		parts.append("Several changes appear to be outside the scope of the issue — please verify.")

	return " ".join(parts)


# --- API endpoints ---

@app.get("/")
async def serve_dashboard():
	return FileResponse(str(static_dir / "index.html"))


@app.websocket("/ws/progress/{session_id}")
async def websocket_progress(websocket: WebSocket, session_id: str):
	"""WebSocket endpoint for real-time review progress updates."""
	await progress_manager.connect(session_id, websocket)
	try:
		while True:
			await websocket.receive_text()  # keep-alive ping
	except WebSocketDisconnect:
		progress_manager.disconnect(session_id)


@app.post("/api/review", response_model=ReviewResponse)
async def run_review(request: ReviewRequest, req: Request):
	"""Run a full code review for a Jira issue and its linked PR."""

	if not re.match(r"^[A-Za-z]+-\d+$", request.issue_key):
		raise HTTPException(status_code=400, detail="Invalid issue key format. Expected format: MT-12345")

	issue_key = request.issue_key.upper()
	session_id = request.session_id

	# Rate limiting
	client_ip = req.client.host if req.client else "unknown"
	if not check_rate_limit(client_ip):
		logger.warning("Rate limit exceeded for IP %s", client_ip)
		raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment before trying again.")

	logger.info("Review requested: issue=%s pr=%s ip=%s", issue_key, request.pr_number, client_ip)

	# Step 1: Fetch Jira issue (with cache)
	await progress_manager.send(session_id, "Fetching Jira issue...")
	cache_key = f"jira:{issue_key}"
	issue = cache_get(cache_key)
	if issue is None:
		try:
			issue = with_retry(lambda: jira.get_issue(issue_key))
		except Exception as exc:
			logger.error("Jira fetch failed for %s: %s", issue_key, exc)
			raise HTTPException(status_code=502, detail=f"Could not reach Jira: {exc}")

		if not issue:
			raise HTTPException(status_code=404, detail=f"Jira issue {issue_key} not found.")
		cache_set(cache_key, issue)
	else:
		logger.debug("Jira cache hit for %s", issue_key)

	# Step 2: Find the linked PR (with cache)
	await progress_manager.send(session_id, "Finding linked PR...")
	pr_cache_key = f"pr:{issue_key}:{request.pr_number}"
	pr = cache_get(pr_cache_key)

	if pr is None:
		if request.pr_number:
			try:
				pr = with_retry(lambda: github_get(
					f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{request.pr_number}"
				))
			except Exception as exc:
				raise HTTPException(status_code=404, detail=f"PR #{request.pr_number} not found: {exc}")
		else:
			pr = with_retry(lambda: find_pr_for_issue(issue_key))
			if not pr:
				raise HTTPException(
					status_code=404,
					detail=f"No PR found linked to {issue_key}. Enter the PR number manually.",
				)
		cache_set(pr_cache_key, pr)
	else:
		logger.debug("PR cache hit for %s", pr_cache_key)

	pr_number = pr["number"]
	pr_title = pr.get("title", "")
	pr_url = pr.get("html_url", "")

	# Step 3: Fetch diff
	await progress_manager.send(session_id, "Fetching PR diff...")
	diff_cache_key = f"diff:{GITHUB_REPO}:{pr_number}"
	diff_text = cache_get(diff_cache_key)
	if diff_text is None:
		try:
			diff_text = with_retry(lambda: github_get_diff(pr_number))
		except Exception as exc:
			raise HTTPException(status_code=502, detail=f"Failed to fetch PR diff: {exc}")
		cache_set(diff_cache_key, diff_text, ttl_minutes=5)
	else:
		logger.debug("Diff cache hit for PR #%d", pr_number)

	# Step 4: Run static analysis
	await progress_manager.send(session_id, "Running analysis rules...")
	all_comments, changed_lines_by_file = run_static_analysis(diff_text)

	# Step 5: Apply suppression
	await progress_manager.send(session_id, "Applying suppression filters...")
	all_comments = apply_suppression(all_comments, changed_lines_by_file)

	suppressed_count = sum(1 for c in all_comments if c.suppressed)
	visible_comments = [c for c in all_comments if not c.suppressed]

	# Group visible comments by file
	file_groups: dict[str, list[ReviewComment]] = {}
	for c in visible_comments:
		file_groups.setdefault(c.file_path, []).append(c)

	file_results = []
	for file_path, comments in sorted(file_groups.items()):
		file_results.append(ReviewFileResult(
			file_path=file_path,
			issues=[{
				"line": c.line_number,
				"message": c.message,
				"severity": c.severity,
				"rule_id": c.rule_id,
				"original_line": c.original_line,
				"suggested_fix": c.suggested_fix,
				"suppressed": c.suppressed,
			} for c in comments],
			error_count=sum(1 for c in comments if c.severity == "error"),
			warning_count=sum(1 for c in comments if c.severity == "warning"),
			suggestion_count=sum(1 for c in comments if c.severity == "suggestion"),
		))

	total_errors = sum(f.error_count for f in file_results)
	total_warnings = sum(f.warning_count for f in file_results)
	total_suggestions = sum(f.suggestion_count for f in file_results)

	# Step 6: Scope validation
	changed_files = parse_diff(diff_text)
	all_file_paths = [f.path for f in changed_files]

	scope = validate_scope(
		issue_title=issue["title"],
		issue_description=issue["description"],
		changed_files=all_file_paths,
		diff_summary=diff_text[:2000],
	)

	# Step 7: Summary
	summary = generate_summary(
		issue_title=issue["title"],
		total_issues=len(visible_comments),
		errors=total_errors,
		warnings=total_warnings,
		scope_score=scope.score,
	)

	# Step 8: Persist to SQLite
	await progress_manager.send(session_id, "Saving results...")

	# Build file data including ALL comments (including suppressed) for analytics
	all_file_groups: dict[str, list[ReviewComment]] = {}
	for c in all_comments:
		all_file_groups.setdefault(c.file_path, []).append(c)

	db_files = []
	for file_path, comments in sorted(all_file_groups.items()):
		visible = [c for c in comments if not c.suppressed]
		db_files.append({
			"file_path": file_path,
			"error_count": sum(1 for c in visible if c.severity == "error"),
			"warning_count": sum(1 for c in visible if c.severity == "warning"),
			"suggestion_count": sum(1 for c in visible if c.severity == "suggestion"),
			"issues": [{
				"line": c.line_number,
				"rule_id": c.rule_id,
				"severity": c.severity,
				"message": c.message,
				"original_line": c.original_line,
				"suggested_fix": c.suggested_fix,
				"suppressed": c.suppressed,
			} for c in comments],
		})

	review_uuid = save_review({
		"issue_key": issue_key,
		"pr_number": pr_number,
		"pr_title": pr_title,
		"pr_url": pr_url,
		"errors": total_errors,
		"warnings": total_warnings,
		"suggestions": total_suggestions,
		"scope_score": scope.score,
		"summary": summary,
		"diff_text": diff_text,
		"files": db_files,
	})

	await progress_manager.send(session_id, "Done!")

	logger.info(
		"Review complete: issue=%s pr=%d errors=%d warnings=%d suggestions=%d suppressed=%d uuid=%s",
		issue_key, pr_number, total_errors, total_warnings, total_suggestions, suppressed_count, review_uuid,
	)

	return ReviewResponse(
		review_uuid=review_uuid,
		issue_key=issue_key,
		issue_title=issue["title"],
		issue_description=issue["description"][:500],
		issue_type=issue["type"],
		pr_number=pr_number,
		pr_title=pr_title,
		pr_url=pr_url,
		total_issues=len(visible_comments),
		errors=total_errors,
		warnings=total_warnings,
		suggestions=total_suggestions,
		suppressed_count=suppressed_count,
		files=file_results,
		scope_validation=scope,
		summary=summary,
	)


@app.get("/api/my-issues")
async def get_my_issues():
	"""
	Return active Jira issues assigned to the current user.

	Statuses included: In Progress, Code Review, Ready for QA, QA,
	Code Review/QA Complete.

	Cached for 2 minutes.
	"""
	if not jira.is_configured():
		return {"issues": [], "configured": False}

	cache_key = "my_issues"
	cached = cache_get(cache_key)
	if cached is not None:
		return {"issues": cached, "configured": True}

	try:
		issues = jira.get_my_issues()
		cache_set(cache_key, issues, ttl_minutes=2)
		return {"issues": issues, "configured": True}
	except Exception as exc:
		logger.error("Failed to fetch my issues: %s", exc)
		return {"issues": [], "configured": True, "error": str(exc)}


@app.get("/api/history")
async def get_history_endpoint():
	"""Return up to 50 past reviews from the database."""
	history = get_history(limit=50)
	return {"history": history, "total": len(history)}


@app.post("/api/post-check-run")
async def post_check_run_endpoint(request: PostCheckRunRequest):
	"""
	Post the review result to GitHub.

	Strategy (in order):
	1. Commit Status API — shows a pass/fail badge on the PR (works with PAT,
	   fails for fork PRs where head commit lives in a different repo).
	2. PR Comment fallback — always works; posts a formatted summary comment.
	"""
	import requests as http

	review = get_review_by_uuid(request.review_uuid)
	if not review:
		raise HTTPException(status_code=404, detail="Review not found.")

	if not GITHUB_TOKEN:
		raise HTTPException(status_code=503, detail="GITHUB_TOKEN is not configured on the server.")

	gh_headers = {
		"Accept": "application/vnd.github.v3+json",
		"Authorization": f"Bearer {GITHUB_TOKEN}",
		"X-GitHub-Api-Version": "2022-11-28",
	}

	# Fetch PR metadata
	pr_resp = http.get(
		f"https://api.github.com/repos/{GITHUB_REPO}/pulls/{request.pr_number}",
		headers=gh_headers,
		timeout=30,
	)
	if pr_resp.status_code != 200:
		raise HTTPException(status_code=502, detail=f"Could not fetch PR #{request.pr_number} from GitHub.")
	pr_data = pr_resp.json()
	commit_sha = pr_data["head"]["sha"]
	pr_url = pr_data.get("html_url", f"https://github.com/{GITHUB_REPO}/pull/{request.pr_number}")

	# Build summary values
	errors = review.get("errors", 0)
	warnings = review.get("warnings", 0)
	suggestions = review.get("suggestions", 0)
	state = "failure" if errors > 0 else "success"
	icon = "❌" if errors > 0 else "✅"

	parts = []
	if errors:
		parts.append(f"{errors} error{'s' if errors != 1 else ''}")
	if warnings:
		parts.append(f"{warnings} warning{'s' if warnings != 1 else ''}")
	if suggestions:
		parts.append(f"{suggestions} suggestion{'s' if suggestions != 1 else ''}")
	description = (", ".join(parts) + " found") if parts else "No issues found — code looks good"

	# --- Attempt 1: Commit Status ---
	status_resp = http.post(
		f"https://api.github.com/repos/{GITHUB_REPO}/statuses/{commit_sha}",
		headers=gh_headers,
		json={
			"state": state,
			"description": description[:140],
			"context": "MultiTracks Code Review",
		},
		timeout=30,
	)

	if status_resp.status_code in (200, 201):
		logger.info("Commit status posted: state=%s pr=%d sha=%s", state, request.pr_number, commit_sha[:8])
		return {
			"method": "status",
			"state": state,
			"conclusion": state,
			"check_run_url": pr_url,
			"description": description,
		}

	# --- Fallback: PR Comment ---
	logger.warning(
		"Commit status failed (%d) — falling back to PR comment. "
		"This usually means the PR is from a fork.",
		status_resp.status_code,
	)

	sev_rows = ""
	if errors:
		sev_rows += f"| ❌ Errors | {errors} |\n"
	if warnings:
		sev_rows += f"| ⚠️ Warnings | {warnings} |\n"
	if suggestions:
		sev_rows += f"| 💡 Suggestions | {suggestions} |\n"

	comment_body = (
		f"## {icon} MultiTracks Code Review\n\n"
		f"{review.get('summary', description)}\n\n"
		f"| Severity | Count |\n|----------|-------|\n{sev_rows}"
		f"\n---\n*Posted from the [Code Review Dashboard]({pr_url})*"
	)

	comment_resp = http.post(
		f"https://api.github.com/repos/{GITHUB_REPO}/issues/{request.pr_number}/comments",
		headers=gh_headers,
		json={"body": comment_body},
		timeout=30,
	)
	if comment_resp.status_code not in (200, 201):
		raise HTTPException(
			status_code=502,
			detail=f"Both status and comment posting failed. Last error: {comment_resp.status_code} {comment_resp.text[:200]}",
		)

	comment_url = comment_resp.json().get("html_url", pr_url)
	logger.info("PR comment posted as fallback: pr=%d url=%s", request.pr_number, comment_url)

	return {
		"method": "comment",
		"state": state,
		"conclusion": state,
		"check_run_url": comment_url,
		"description": description,
	}


@app.get("/api/analytics")
async def get_analytics_endpoint():
	"""
	Return aggregate statistics across all stored reviews for dashboard charts.

	Includes:
	- top_violations: Most common rule violations
	- score_trend: Scope score over the last 30 days
	- files_with_most_issues: Files with the most accumulated issues
	- total_reviews / avg_issues_per_review
	"""
	return get_analytics()


@app.get("/api/export/{review_uuid}")
async def export_review(review_uuid: str):
	"""Return a self-contained HTML report for the given review."""
	review = get_review_by_uuid(review_uuid)
	if not review:
		raise HTTPException(status_code=404, detail="Review not found.")

	files = review.get("files", [])
	total_issues = (
		review.get("errors", 0) + review.get("warnings", 0) + review.get("suggestions", 0)
	)

	# Build file rows
	file_rows_html = ""
	for f in files:
		issues_html = ""
		for issue in f.get("issues", []):
			if issue.get("suppressed"):
				continue
			sev = issue.get("severity", "").lower()
			sev_color = {"error": "#ef4444", "warning": "#f59e0b", "suggestion": "#3b82f6"}.get(sev, "#888")
			issues_html += f"""
			<tr>
				<td style="color:{sev_color};font-weight:600;text-transform:uppercase;font-size:11px;">{sev}</td>
				<td style="color:#888;">L{issue.get('line_number','?')}</td>
				<td style="color:#aaa;font-size:11px;">{_html_escape(issue.get('rule_id',''))}</td>
				<td>{_html_escape(issue.get('message',''))}</td>
			</tr>"""

		if not issues_html:
			continue

		file_path = f.get("file_path", "")
		short_path = "/".join(file_path.split("/")[-3:]) if "/" in file_path else file_path
		file_rows_html += f"""
		<div style="margin-bottom:20px;border:1px solid #2a2d42;border-radius:8px;overflow:hidden;">
			<div style="background:#1c1e2e;padding:10px 16px;font-family:monospace;font-size:13px;color:#8b8fa8;">
				{_html_escape(short_path)}
				<span style="float:right;color:#6b7280;font-size:11px;">
					{f.get('error_count',0)}E / {f.get('warning_count',0)}W / {f.get('suggestion_count',0)}S
				</span>
			</div>
			<table style="width:100%;border-collapse:collapse;font-size:13px;">
				<tbody>{issues_html}</tbody>
			</table>
		</div>"""

	scope_score = review.get("scope_score", 100)
	scope_color = "#10b981" if scope_score >= 80 else "#f59e0b" if scope_score >= 50 else "#ef4444"

	created_at = review.get("created_at", "")[:19].replace("T", " ") + " UTC"

	html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Code Review Report — {_html_escape(review['issue_key'])}</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #0f1117; color: #e4e6f0; margin: 0; padding: 32px; line-height: 1.6; }}
  .container {{ max-width: 900px; margin: 0 auto; }}
  h1 {{ color: #6366f1; margin-bottom: 4px; }}
  .meta {{ color: #8b8fa8; font-size: 13px; margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }}
  .card {{ background: #1c1e2e; border: 1px solid #2a2d42; border-radius: 8px;
           padding: 16px; text-align: center; }}
  .num {{ font-size: 28px; font-weight: 700; }}
  .lbl {{ font-size: 12px; color: #8b8fa8; text-transform: uppercase; margin-top: 4px; }}
  .scope {{ background: #1c1e2e; border: 1px solid #2a2d42; border-radius: 8px;
            padding: 16px; margin-bottom: 24px; }}
  .summary {{ background: #1c1e2e; border: 1px solid #2a2d42; border-radius: 8px;
              padding: 16px; margin-bottom: 24px; color: #8b8fa8; font-size: 14px; }}
  table tr td {{ padding: 8px 12px; border-bottom: 1px solid #1c1e2e; vertical-align: top; }}
  @media print {{ body {{ background: white; color: black; padding: 16px; }}
    .card {{ border: 1px solid #ddd; }}
    .num {{ color: #333; }}
  }}
</style>
</head>
<body>
<div class="container">
  <h1>Code Review Report</h1>
  <div class="meta">
    {_html_escape(review['issue_key'])} &mdash;
    PR #{review['pr_number']}: {_html_escape(review.get('pr_title',''))}
    &mdash; Generated {created_at}
  </div>

  <div class="grid">
    <div class="card"><div class="num">{total_issues}</div><div class="lbl">Total Issues</div></div>
    <div class="card"><div class="num" style="color:#ef4444;">{review.get('errors',0)}</div><div class="lbl">Errors</div></div>
    <div class="card"><div class="num" style="color:#f59e0b;">{review.get('warnings',0)}</div><div class="lbl">Warnings</div></div>
    <div class="card"><div class="num" style="color:#3b82f6;">{review.get('suggestions',0)}</div><div class="lbl">Suggestions</div></div>
  </div>

  <div class="scope">
    <strong>Scope Score:</strong>
    <span style="color:{scope_color};font-size:20px;font-weight:700;margin-left:8px;">{scope_score}%</span>
  </div>

  <div class="summary">{_html_escape(review.get('summary',''))}</div>

  <h2 style="margin-bottom:16px;font-size:16px;color:#8b8fa8;text-transform:uppercase;letter-spacing:1px;">
    File Analysis
  </h2>
  {file_rows_html if file_rows_html else '<p style="color:#8b8fa8;">No issues found.</p>'}

  <p style="color:#5c6078;font-size:12px;margin-top:32px;text-align:center;">
    MultiTracks.com Auto Code Review Bot &mdash; {created_at}
  </p>
</div>
</body>
</html>"""

	return HTMLResponse(content=html, media_type="text/html")


def _html_escape(text: str) -> str:
	"""Escape HTML special characters."""
	return (
		str(text)
		.replace("&", "&amp;")
		.replace("<", "&lt;")
		.replace(">", "&gt;")
		.replace('"', "&quot;")
	)


@app.get("/api/rules")
async def get_rules():
	"""List all available analysis rules with their metadata."""
	_cs_exts = {".cs"}
	_sql_exts = {".sql"}
	_js_exts = {".js", ".ts"}

	def detect_file_types(rule) -> list[str]:
		types = []
		if any(rule.applies_to(f"file{ext}") for ext in _cs_exts):
			types.append("C#")
		if any(rule.applies_to(f"file{ext}") for ext in _sql_exts):
			types.append("SQL")
		if any(rule.applies_to(f"file{ext}") for ext in _js_exts):
			types.append("JS/TS")
		return types or ["General"]

	rules_list = []
	for rule in ALL_RULES:
		rule_info = {
			"class": type(rule).__name__,
			"file_types": detect_file_types(rule),
			"description": (type(rule).__doc__ or "").strip().split("\n")[0],
		}
		rules_list.append(rule_info)

	return {"rules": rules_list, "total": len(rules_list)}


@app.post("/api/apply-fix")
async def apply_fix(request: ApplyFixRequest):
	"""Apply a suggested fix directly to a file on disk."""
	repo_root = Path(request.repo_root).resolve()
	if not repo_root.exists():
		raise HTTPException(status_code=400, detail=f"Repo root not found: {repo_root}")

	file_rel = Path(request.file_path.replace("\\", "/"))
	target = (repo_root / file_rel).resolve()

	try:
		target.relative_to(repo_root)
	except ValueError:
		raise HTTPException(status_code=400, detail="File path must be within the repo root.")

	if not target.exists():
		raise HTTPException(status_code=404, detail=f"File not found on disk: {request.file_path}")

	lines = target.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
	line_idx = request.line_number - 1

	if line_idx < 0 or line_idx >= len(lines):
		raise HTTPException(
			status_code=400,
			detail=f"Line {request.line_number} is out of range (file has {len(lines)} lines).",
		)

	actual = lines[line_idx].rstrip("\r\n")
	expected = request.original_line.rstrip("\r\n")
	suggested = request.suggested_fix.rstrip("\r\n")

	# Detect the line ending before any modification
	raw = lines[line_idx]
	if raw.endswith("\r\n"):
		ending = "\r\n"
	elif raw.endswith("\n"):
		ending = "\n"
	elif raw.endswith("\r"):
		ending = "\r"
	else:
		ending = ""

	if actual.strip() == expected.strip():
		# Exact match — apply normally
		new_line = suggested
	else:
		# Line was already partially modified by a previous fix on the same line.
		# Try to apply this fix's transformation to the current content by computing
		# what text was removed/added and replaying it on the actual line.
		import difflib
		orig_stripped = expected.strip()
		fix_stripped = suggested.strip()
		actual_stripped = actual.strip()

		# Find what the fix removed from original
		removed = ""
		added = ""
		for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, orig_stripped, fix_stripped).get_opcodes():
			if tag == "delete":
				removed += orig_stripped[i1:i2]
			elif tag == "replace":
				removed += orig_stripped[i1:i2]
				added += fix_stripped[j1:j2]
			elif tag == "insert":
				added += fix_stripped[j1:j2]

		if removed and removed in actual_stripped:
			# Can replay the same removal/replacement on the current line
			new_stripped = actual_stripped.replace(removed, added, 1)
			leading_ws = len(actual) - len(actual.lstrip())
			new_line = actual[:leading_ws] + new_stripped
		else:
			# Cannot merge safely — report conflict without modifying the file
			logger.warning("Fix conflict: file=%s line=%d — line already changed", target, request.line_number)
			return {
				"status": "conflict",
				"file": str(target),
				"line": request.line_number,
				"message": "This line was already modified by a previous fix. Re-run the review to apply remaining fixes.",
			}

	lines[line_idx] = new_line + ending
	target.write_text("".join(lines), encoding="utf-8")

	logger.info("Applied fix: file=%s line=%d", target, request.line_number)
	return {"status": "ok", "file": str(target), "line": request.line_number}


def _get_git_diff(repo_path: Path) -> str:
	"""Run git diff HEAD in repo_path. Returns empty string if not a git repo or no changes."""
	import subprocess
	try:
		result = subprocess.run(
			["git", "diff", "HEAD"],
			cwd=str(repo_path),
			capture_output=True,
			text=True,
			timeout=15,
		)
		return result.stdout.strip()
	except Exception:
		return ""


def _scan_all_files(repo_path: Path) -> list:
	"""
	Walk repo_path and build ChangedFile objects treating every line as changed.
	Used when there's no git diff available.
	"""
	reviewable_extensions = {
		".cs", ".sql", ".config", ".json", ".xml",
		".yml", ".yaml", ".csproj", ".props",
		".js", ".ts", ".css", ".html", ".cshtml",
		".aspx", ".ascx", ".master",
	}
	excluded_dirs = {"bin", "obj", "node_modules", "packages", ".vs", ".git", "__pycache__"}

	files = []
	for file_path in repo_path.rglob("*"):
		if not file_path.is_file():
			continue
		if any(part in excluded_dirs for part in file_path.parts):
			continue
		if file_path.suffix.lower() not in reviewable_extensions:
			continue
		if file_path.name.endswith((".min.js", ".min.css", ".map")):
			continue

		try:
			content = file_path.read_text(encoding="utf-8", errors="replace")
			relative = str(file_path.relative_to(repo_path)).replace("\\", "/")
			changed_lines = {i + 1: line for i, line in enumerate(content.splitlines())}
			files.append(ChangedFile(path=relative, changed_lines=changed_lines))
		except Exception:
			continue

	return files


@app.post("/api/local-review", response_model=ReviewResponse)
async def run_local_review_endpoint(request: LocalReviewRequest, req: Request):
	"""
	Run a code review against a local repository folder.

	Strategy:
	  1. git diff HEAD  — reviews only uncommitted changes (fastest, most useful)
	  2. Full file scan — fallback when no git repo or no pending changes
	"""
	repo_path = Path(request.repo_path)
	if not repo_path.exists() or not repo_path.is_dir():
		raise HTTPException(status_code=400, detail=f"Folder not found: {request.repo_path}")

	client_ip = req.client.host if req.client else "unknown"
	if not check_rate_limit(client_ip):
		raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")

	session_id = request.session_id
	folder_name = repo_path.name

	await progress_manager.send(session_id, "Scanning local repository...")

	# Determine source: git diff or full scan
	diff_text = _get_git_diff(repo_path)
	if diff_text:
		await progress_manager.send(session_id, "Found git changes — analyzing diff...")
		changed_files = parse_diff(diff_text)
		reviewable_files = filter_reviewable_files(changed_files)
		source = "git diff HEAD"
	else:
		await progress_manager.send(session_id, "No git changes — scanning all files...")
		reviewable_files = _scan_all_files(repo_path)
		source = "full scan"

	logger.info("Local review: path=%s source=%s files=%d", repo_path, source, len(reviewable_files))

	if not reviewable_files:
		raise HTTPException(status_code=400, detail="No reviewable files found in the selected folder.")

	await progress_manager.send(session_id, f"Analyzing {len(reviewable_files)} file(s)...")

	all_comments: list[ReviewComment] = []
	changed_lines_by_file: dict[str, dict[int, str]] = {}

	for file in reviewable_files:
		changed_lines_by_file[file.path] = file.changed_lines
		for rule in ALL_RULES:
			if rule.applies_to(file.path):
				try:
					all_comments.extend(rule.analyze(file.path, file.changed_lines))
				except Exception as exc:
					logger.error("Rule %s failed on %s: %s", type(rule).__name__, file.path, exc)

	# Suppression
	all_comments = apply_suppression(all_comments, changed_lines_by_file)
	suppressed_count = sum(1 for c in all_comments if c.suppressed)
	visible = [c for c in all_comments if not c.suppressed]

	# Group by file
	file_groups: dict[str, list[ReviewComment]] = {}
	for c in visible:
		file_groups.setdefault(c.file_path, []).append(c)

	file_results = []
	for fp, comments in sorted(file_groups.items()):
		file_results.append(ReviewFileResult(
			file_path=fp,
			issues=[{
				"line": c.line_number,
				"message": c.message,
				"severity": c.severity,
				"rule_id": c.rule_id,
				"original_line": c.original_line,
				"suggested_fix": c.suggested_fix,
				"suppressed": c.suppressed,
			} for c in comments],
			error_count=sum(1 for c in comments if c.severity == "error"),
			warning_count=sum(1 for c in comments if c.severity == "warning"),
			suggestion_count=sum(1 for c in comments if c.severity == "suggestion"),
		))

	total_errors = sum(f.error_count for f in file_results)
	total_warnings = sum(f.warning_count for f in file_results)
	total_suggestions = sum(f.suggestion_count for f in file_results)
	total_issues = len(visible)

	summary = generate_summary(
		issue_title=folder_name,
		total_issues=total_issues,
		errors=total_errors,
		warnings=total_warnings,
		scope_score=100,
	)

	# Persist
	await progress_manager.send(session_id, "Saving results...")
	all_file_groups: dict[str, list[ReviewComment]] = {}
	for c in all_comments:
		all_file_groups.setdefault(c.file_path, []).append(c)

	db_files = []
	for fp, comments in sorted(all_file_groups.items()):
		vis = [c for c in comments if not c.suppressed]
		db_files.append({
			"file_path": fp,
			"error_count": sum(1 for c in vis if c.severity == "error"),
			"warning_count": sum(1 for c in vis if c.severity == "warning"),
			"suggestion_count": sum(1 for c in vis if c.severity == "suggestion"),
			"issues": [{
				"line": c.line_number, "rule_id": c.rule_id, "severity": c.severity,
				"message": c.message, "original_line": c.original_line,
				"suggested_fix": c.suggested_fix, "suppressed": c.suppressed,
			} for c in comments],
		})

	review_uuid = save_review({
		"issue_key": "LOCAL",
		"pr_number": 0,
		"pr_title": f"Local: {folder_name}",
		"pr_url": "",
		"errors": total_errors,
		"warnings": total_warnings,
		"suggestions": total_suggestions,
		"scope_score": 100,
		"summary": summary,
		"diff_text": diff_text[:500_000] if diff_text else "",
		"files": db_files,
	})

	await progress_manager.send(session_id, "Done!")

	return ReviewResponse(
		review_uuid=review_uuid,
		issue_key="LOCAL",
		issue_title=folder_name,
		issue_description=f"Source: {source} · {len(reviewable_files)} file(s) analyzed",
		issue_type="Local",
		pr_number=0,
		pr_title="",
		pr_url="",
		total_issues=total_issues,
		errors=total_errors,
		warnings=total_warnings,
		suggestions=total_suggestions,
		suppressed_count=suppressed_count,
		files=file_results,
		scope_validation=ScopeValidation(in_scope=[], out_of_scope=[], missing=[], score=100),
		summary=summary,
	)


@app.get("/api/pick-directory")
async def pick_directory():
	"""
	Open a native OS folder picker dialog and return the selected path.
	Only works when the server is running locally (not in production).
	"""
	try:
		import tkinter as tk
		from tkinter import filedialog

		root = tk.Tk()
		root.withdraw()
		root.wm_attributes("-topmost", True)
		path = filedialog.askdirectory(title="Select your local repo root")
		root.destroy()

		if not path:
			return {"path": None, "cancelled": True}
		return {"path": path, "cancelled": False}
	except Exception as exc:
		raise HTTPException(status_code=500, detail=f"Could not open folder picker: {exc}")


@app.get("/api/health")
async def health():
	"""Health check endpoint."""
	return {
		"status": "ok",
		"jira_configured": jira.is_configured(),
		"github_configured": bool(GITHUB_TOKEN),
"rules_loaded": len(ALL_RULES),
		"cache_entries": len(_cache),
	}

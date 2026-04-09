/**
 * MultiTracks Code Review Dashboard — Frontend
 *
 * Handles form submission, API communication, and result rendering.
 * v3.0: WebSocket progress, Claude AI review, analytics charts, HTML export.
 */

const API_BASE = "";

// --- DOM Elements ---
const reviewForm = document.getElementById("reviewForm");
const issueKeyInput = document.getElementById("issueKey");
const prNumberInput = document.getElementById("prNumber");
const submitBtn = document.getElementById("submitBtn");
const loadingSection = document.getElementById("loadingSection");
const loadingDetail = document.getElementById("loadingDetail");
const errorSection = document.getElementById("errorSection");
const errorTitle = document.getElementById("errorTitle");
const errorMessage = document.getElementById("errorMessage");
const resultsSection = document.getElementById("resultsSection");
const statusIndicator = document.getElementById("statusIndicator");

// --- State ---
let currentAbortController = null;
let timeoutHandle = null;
let lastReviewData = null;
let activeFilter = "all";
let _fixData = [];
let currentReviewUUID = null;
let wsConnection = null;
let analyticsCharts = {};  // holds Chart.js instances

// Progress steps used to animate the progress bar
const PROGRESS_STEPS = [
	"Fetching Jira issue...",
	"Finding linked PR...",
	"Fetching PR diff...",
	"Running analysis rules...",
	"Applying suppression filters...",
	"Saving results...",
	"Done!",
];

// --- Initialization ---

document.addEventListener("DOMContentLoaded", () => {
	checkHealth();
	loadMyIssues();
	reviewForm.addEventListener("submit", handleSubmit);
	applyStoredTheme();
	const repoRootInput = document.getElementById("repoRoot");
	if (repoRootInput) repoRootInput.value = localStorage.getItem("repoRoot") || "";
	const localRepoInput = document.getElementById("localRepoPath");
	if (localRepoInput) localRepoInput.value = "";
});

// --- Tabs ---

// Per-tab state: each tab keeps its own last review data and UUID
const _tabState = {
	github: { reviewData: null, reviewUUID: null },
	local:  { reviewData: null, reviewUUID: null },
};
let _activeTab = "github";

function switchTab(tab, btn) {
	if (_activeTab === tab) return;

	// Save current tab's state
	_tabState[_activeTab].reviewData = lastReviewData;
	_tabState[_activeTab].reviewUUID = currentReviewUUID;

	// Switch tab UI
	document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
	btn.classList.add("active");
	document.getElementById("tab-github").style.display = tab === "github" ? "block" : "none";
	document.getElementById("tab-local").style.display  = tab === "local"  ? "block" : "none";
	_activeTab = tab;

	// Restore new tab's state
	const saved = _tabState[tab];
	lastReviewData   = saved.reviewData;
	currentReviewUUID = saved.reviewUUID;

	if (saved.reviewData) {
		resultsSection.style.display = "block";
		errorSection.style.display = "none";
		loadingSection.style.display = "none";
		renderResults(saved.reviewData);
	} else {
		resultsSection.style.display = "none";
		errorSection.style.display = "none";
	}
}

// --- Theme ---

function applyStoredTheme() {
	const stored = localStorage.getItem("theme");
	if (stored === "light") {
		document.body.classList.add("light");
		document.getElementById("themeToggle").textContent = "☀️";
	}
}

function toggleTheme() {
	const btn = document.getElementById("themeToggle");
	if (document.body.classList.contains("light")) {
		document.body.classList.remove("light");
		btn.textContent = "🌙";
		localStorage.setItem("theme", "dark");
	} else {
		document.body.classList.add("light");
		btn.textContent = "☀️";
		localStorage.setItem("theme", "light");
	}
}

/**
 * Check API health and update status indicator.
 */
async function checkHealth() {
	try {
		const response = await fetch(`${API_BASE}/api/health`);
		const data = await response.json();

		const dot = statusIndicator.querySelector(".status-dot");
		const text = statusIndicator.querySelector(".status-text");

		const github = data.github_configured;
		const jira   = data.jira_configured;

		if (github && jira) {
			dot.className = "status-dot connected";
			text.textContent = "GitHub + Jira Connected";
		} else if (github) {
			dot.className = "status-dot connected";
			text.textContent = "GitHub Connected";
		} else if (jira) {
			dot.className = "status-dot partial";
			text.textContent = "Jira Connected — GitHub not configured";
		} else {
			dot.className = "status-dot partial";
			text.textContent = "Not configured — check your .env file";
		}
	} catch {
		const dot = statusIndicator.querySelector(".status-dot");
		const text = statusIndicator.querySelector(".status-text");
		dot.className = "status-dot";
		text.textContent = "API Unavailable";
	}
}

// --- WebSocket Progress ---

function openProgressSocket(sessionId) {
	const protocol = location.protocol === "https:" ? "wss:" : "ws:";
	const wsUrl = `${protocol}//${location.host}/ws/progress/${sessionId}`;
	wsConnection = new WebSocket(wsUrl);

	wsConnection.onmessage = (event) => {
		try {
			const data = JSON.parse(event.data);
			updateProgressFromWS(data.message);
		} catch {}
	};

	wsConnection.onerror = () => {
		// WebSocket failed — progress bar just won't update, review still works
	};
}

function closeProgressSocket() {
	if (wsConnection) {
		wsConnection.close();
		wsConnection = null;
	}
}

function updateProgressFromWS(message) {
	const progressText = document.getElementById("progressText");
	if (progressText) progressText.textContent = message;

	// Map message to a step index for progress bar animation
	const stepIdx = PROGRESS_STEPS.findIndex(s =>
		message.toLowerCase().includes(s.toLowerCase().split("...")[0].trim())
	);
	if (stepIdx >= 0) {
		const pct = Math.round(((stepIdx + 1) / PROGRESS_STEPS.length) * 100);
		const bar = document.getElementById("progressBar");
		if (bar) bar.style.width = pct + "%";
	}
}

/**
 * Handle form submission — trigger the review.
 */
async function handleSubmit(e) {
	e.preventDefault();

	const issueKey = issueKeyInput.value.trim().toUpperCase();
	const prNumber = prNumberInput.value.trim();

	if (!issueKey) return;

	// Reset state
	currentReviewUUID = null;
	const exportBtn = document.getElementById("exportHTMLBtn");
	if (exportBtn) exportBtn.style.display = "none";
	const aiSection = document.getElementById("aiSection");
	if (aiSection) aiSection.style.display = "none";
	const analyticsSection = document.getElementById("analyticsSection");
	if (analyticsSection) analyticsSection.style.display = "none";

	showLoading();

	// Open WebSocket for progress updates
	const sessionId = (typeof crypto !== "undefined" && crypto.randomUUID)
		? crypto.randomUUID()
		: Math.random().toString(36).slice(2);

	openProgressSocket(sessionId);

	currentAbortController = new AbortController();

	timeoutHandle = setTimeout(() => {
		const warning = document.getElementById("timeoutWarning");
		if (warning) warning.style.display = "block";
	}, 60000);

	try {
		const payload = { issue_key: issueKey, session_id: sessionId };
		if (prNumber) payload.pr_number = parseInt(prNumber, 10);

		const response = await fetch(`${API_BASE}/api/review`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify(payload),
			signal: currentAbortController.signal,
		});

		clearTimeout(timeoutHandle);
		closeProgressSocket();

		if (!response.ok) {
			const errorData = await response.json().catch(() => ({}));
			throw new Error(errorData.detail || `Server returned ${response.status}`);
		}

		const data = await response.json();
		lastReviewData = data;
		currentReviewUUID = data.review_uuid;
		_tabState["github"].reviewData = data;
		_tabState["github"].reviewUUID = data.review_uuid;
		renderResults(data);

	} catch (err) {
		clearTimeout(timeoutHandle);
		closeProgressSocket();
		if (err.name === "AbortError") {
			showError("Request Cancelled", "The review was cancelled.");
		} else {
			showError("Review Failed", err.message);
		}
	}
}

function cancelRequest() {
	if (currentAbortController) {
		currentAbortController.abort();
	}
}

// --- UI State Management ---

function showLoading() {
	loadingSection.style.display = "block";
	errorSection.style.display = "none";
	resultsSection.style.display = "none";
	submitBtn.disabled = true;
	const warning = document.getElementById("timeoutWarning");
	if (warning) warning.style.display = "none";
	const bar = document.getElementById("progressBar");
	if (bar) bar.style.width = "0%";
	const progressText = document.getElementById("progressText");
	if (progressText) progressText.textContent = "";
}

function updateLoadingStep(message) {
	loadingDetail.textContent = message;
}

function showError(title, message) {
	clearTimeout(timeoutHandle);
	loadingSection.style.display = "none";
	errorSection.style.display = "block";
	resultsSection.style.display = "none";
	submitBtn.disabled = false;
	errorTitle.textContent = title;
	errorMessage.textContent = message;
}

function resetForm() {
	clearTimeout(timeoutHandle);
	closeProgressSocket();
	if (currentAbortController) currentAbortController.abort();
	loadingSection.style.display = "none";
	errorSection.style.display = "none";
	resultsSection.style.display = "none";
	submitBtn.disabled = false;
	issueKeyInput.value = "";
	prNumberInput.value = "";
	lastReviewData = null;
	currentReviewUUID = null;
	issueKeyInput.focus();
}

// --- Filters ---

function setFilter(filter, btn) {
	activeFilter = filter;

	document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
	btn.classList.add("active");

	document.querySelectorAll(".file-card[data-severities]").forEach(card => {
		const severities = card.dataset.severities.split(",");
		if (filter === "all" || severities.includes(filter)) {
			card.classList.remove("file-card--hidden");
		} else {
			card.classList.add("file-card--hidden");
		}
	});
}

// --- Export ---

function exportJSON() {
	if (!lastReviewData) return;
	const blob = new Blob([JSON.stringify(lastReviewData, null, 2)], { type: "application/json" });
	const url = URL.createObjectURL(blob);
	const a = document.createElement("a");
	const timestamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
	a.href = url;
	a.download = `review-${lastReviewData.issue_key || "export"}-${timestamp}.json`;
	a.click();
	URL.revokeObjectURL(url);
}

function exportHTML() {
	if (!currentReviewUUID) return;
	window.open(`${API_BASE}/api/export/${currentReviewUUID}`, "_blank");
}

// --- GitHub Check Run ---

async function postCheckRun() {
	if (!currentReviewUUID || !lastReviewData) return;

	const btn = document.getElementById("postCheckBtn");
	const resultEl = document.getElementById("checkRunResult");

	btn.disabled = true;
	btn.textContent = "Posting...";
	resultEl.style.display = "none";

	try {
		const resp = await fetch(`${API_BASE}/api/post-check-run`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				pr_number: lastReviewData.pr_number,
				review_uuid: currentReviewUUID,
			}),
		});

		const data = await resp.json();

		if (!resp.ok) {
			throw new Error(data.detail || `Error ${resp.status}`);
		}

		const icon = data.conclusion === "failure" ? "❌" : "✅";
		const label = data.method === "comment" ? "Comment posted" : "Status posted";
		resultEl.innerHTML = `${icon} ${label} — <a href="${data.check_run_url}" target="_blank">View on GitHub</a>`;
		resultEl.style.display = "inline";
		btn.textContent = "Posted ✓";
	} catch (err) {
		resultEl.innerHTML = `<span style="color:var(--error)">Failed: ${err.message}</span>`;
		resultEl.style.display = "inline";
		btn.disabled = false;
		btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg> Post to GitHub`;
	}
}

// --- Settings ---

async function browseLocalRepo(btn) {
	const original = btn.innerHTML;
	btn.disabled = true;
	btn.textContent = "Opening…";
	try {
		const resp = await fetch(`${API_BASE}/api/pick-directory`);
		const data = await resp.json();
		if (!data.cancelled && data.path) {
			const input = document.getElementById("localRepoPath");
			input.value = data.path;
			// Sync as repo root for Apply Fix (persisted for GitHub Review use too)
			localStorage.setItem("repoRoot", data.path);
		}
	} catch (err) {
		console.error("Folder picker failed:", err);
	} finally {
		btn.disabled = false;
		btn.innerHTML = original;
	}
}

async function handleLocalSubmit() {
	const localInput = document.getElementById("localRepoPath");
	const repoPath = localInput.value.trim();
	if (!repoPath) {
		localInput.classList.add("input-error");
		let errEl = document.getElementById("localRepoPathError");
		if (!errEl) {
			errEl = document.createElement("span");
			errEl.id = "localRepoPathError";
			errEl.className = "input-error-msg";
			localInput.closest(".repo-root-wrap").insertAdjacentElement("afterend", errEl);
		}
		errEl.textContent = "Please select a repository folder before running the review.";
		localInput.focus();
		return;
	}
	// Clear any previous error
	localInput.classList.remove("input-error");
	document.getElementById("localRepoPathError")?.remove();

	localStorage.setItem("repoRoot", repoPath);

	const btn = document.getElementById("localSubmitBtn");
	btn.disabled = true;

	const sessionId = (typeof crypto !== "undefined" && crypto.randomUUID)
		? crypto.randomUUID()
		: Math.random().toString(36).slice(2);

	openProgressSocket(sessionId);
	showLoading();

	currentAbortController = new AbortController();
	timeoutHandle = setTimeout(() => {
		const warning = document.getElementById("timeoutWarning");
		if (warning) warning.style.display = "block";
	}, 60000);

	try {
		const response = await fetch(`${API_BASE}/api/local-review`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ repo_path: repoPath, session_id: sessionId }),
			signal: currentAbortController.signal,
		});

		clearTimeout(timeoutHandle);
		closeProgressSocket();

		if (!response.ok) {
			const err = await response.json().catch(() => ({}));
			throw new Error(err.detail || `Server error ${response.status}`);
		}

		const data = await response.json();
		lastReviewData = data;
		currentReviewUUID = data.review_uuid;
		_tabState["local"].reviewData = data;
		_tabState["local"].reviewUUID = data.review_uuid;
		renderResults(data);
	} catch (err) {
		clearTimeout(timeoutHandle);
		closeProgressSocket();
		if (err.name === "AbortError") {
			showError("Request Cancelled", "The review was cancelled.");
		} else {
			showError("Local Review Failed", err.message);
		}
	} finally {
		btn.disabled = false;
	}
}

async function browseRepoRoot(btn) {
	const original = btn.innerHTML;
	btn.disabled = true;
	btn.textContent = "Opening…";

	try {
		const resp = await fetch(`${API_BASE}/api/pick-directory`);
		const data = await resp.json();

		if (!data.cancelled && data.path) {
			const input = document.getElementById("repoRoot");
			input.value = data.path;
			autoSaveRepoRoot(input);
		}
	} catch (err) {
		console.error("Folder picker failed:", err);
	} finally {
		btn.disabled = false;
		btn.innerHTML = original;
	}
}

let _repoRootSaveTimeout = null;
function autoSaveRepoRoot(input) {
	const val = input.value.trim();
	localStorage.setItem("repoRoot", val);
	const indicator = document.getElementById("repoRootSaved");
	if (!indicator) return;
	clearTimeout(_repoRootSaveTimeout);
	if (val) {
		indicator.style.display = "inline";
		_repoRootSaveTimeout = setTimeout(() => { indicator.style.display = "none"; }, 2000);
	} else {
		indicator.style.display = "none";
	}
}

// --- Copy to Clipboard ---

function copyToClipboard(text, btn) {
	if (!text) return;
	navigator.clipboard.writeText(text).then(() => {
		const original = btn.textContent;
		btn.textContent = "✓";
		setTimeout(() => { btn.textContent = original; }, 2000);
	}).catch(() => {
		const ta = document.createElement("textarea");
		ta.value = text;
		ta.style.position = "fixed";
		ta.style.opacity = "0";
		document.body.appendChild(ta);
		ta.select();
		document.execCommand("copy");
		document.body.removeChild(ta);
		const original = btn.textContent;
		btn.textContent = "✓";
		setTimeout(() => { btn.textContent = original; }, 2000);
	});
}

// --- Apply Fix ---

async function applyFix(fixIdx, btn) {
	const fix = _fixData[fixIdx];
	if (!fix) return;

	const repoRoot = localStorage.getItem("repoRoot") || "";
	if (!repoRoot) {
		alert("Set your Local Repo Root path above and click Save Path before applying fixes.");
		return;
	}

	btn.disabled = true;
	btn.textContent = "Applying…";

	try {
		const response = await fetch(`${API_BASE}/api/apply-fix`, {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({
				file_path: fix.filePath,
				line_number: fix.line,
				original_line: fix.original,
				suggested_fix: fix.fix,
				repo_root: repoRoot,
			}),
		});

		if (!response.ok) {
			const err = await response.json().catch(() => ({}));
			throw new Error(err.detail || `Server error ${response.status}`);
		}

		const data = await response.json();

		if (data.status === "conflict") {
			btn.disabled = false;
			btn.textContent = "⚠ Re-run review";
			btn.title = data.message;
			btn.classList.add("conflict");
		} else {
			btn.textContent = "✓ Applied";
			btn.classList.add("applied");
			btn.closest(".issue-row").classList.add("issue-fixed");
		}
	} catch (err) {
		btn.disabled = false;
		btn.textContent = "⚡ Apply Fix";
		btn.closest(".issue-row").querySelector(".fix-error")?.remove();
		const errEl = document.createElement("span");
		errEl.className = "fix-error";
		errEl.textContent = err.message;
		btn.insertAdjacentElement("afterend", errEl);
	}
}

// --- Analytics Charts ---

async function loadAnalytics() {
	try {
		const res = await fetch(`${API_BASE}/api/analytics`);
		if (!res.ok) return;
		const data = await res.json();

		if (data.total_reviews === 0) return;

		document.getElementById("analyticsSection").style.display = "block";
		renderCharts(data);
	} catch {
		// Analytics are non-critical — silently fail
	}
}

function renderCharts(data) {
	// Destroy existing instances to prevent memory leaks on re-render
	Object.values(analyticsCharts).forEach(c => { try { c.destroy(); } catch {} });
	analyticsCharts = {};

	const isDark = !document.body.classList.contains("light");
	const textColor = isDark ? "#8b8fa8" : "#4b5563";
	const gridColor = isDark ? "#2a2d42" : "#e5e7eb";
	const accentColor = "#6366f1";

	Chart.defaults.color = textColor;
	Chart.defaults.borderColor = gridColor;

	// 1. Most Common Violations (horizontal bar)
	const violCtx = document.getElementById("violationsChart");
	if (violCtx && data.top_violations?.length) {
		analyticsCharts.violations = new Chart(violCtx, {
			type: "bar",
			data: {
				labels: data.top_violations.map(v => v.rule_id),
				datasets: [{
					label: "Violations",
					data: data.top_violations.map(v => v.count),
					backgroundColor: "#6366f1cc",
					borderColor: accentColor,
					borderWidth: 1,
					borderRadius: 4,
				}],
			},
			options: {
				indexAxis: "y",
				responsive: true,
				plugins: { legend: { display: false } },
				scales: {
					x: { grid: { color: gridColor }, ticks: { color: textColor } },
					y: { grid: { display: false }, ticks: { color: textColor, font: { size: 11 } } },
				},
			},
		});
	}

	// 2. Score Trend (line chart)
	const trendCtx = document.getElementById("scoreTrendChart");
	if (trendCtx && data.score_trend?.length) {
		analyticsCharts.trend = new Chart(trendCtx, {
			type: "line",
			data: {
				labels: data.score_trend.map(d => d.day),
				datasets: [{
					label: "Avg Scope Score",
					data: data.score_trend.map(d => d.avg_score),
					borderColor: "#10b981",
					backgroundColor: "#10b98122",
					tension: 0.3,
					fill: true,
					pointBackgroundColor: "#10b981",
				}],
			},
			options: {
				responsive: true,
				plugins: { legend: { display: false } },
				scales: {
					x: { grid: { color: gridColor }, ticks: { color: textColor } },
					y: { min: 0, max: 100, grid: { color: gridColor }, ticks: { color: textColor } },
				},
			},
		});
	}

	// 3. Files With Most Issues (horizontal bar)
	const filesCtx = document.getElementById("filesChart");
	if (filesCtx && data.files_with_most_issues?.length) {
		const labels = data.files_with_most_issues.map(f => {
			const parts = f.file_path.split("/");
			return parts.length > 2 ? ".../" + parts.slice(-2).join("/") : f.file_path;
		});
		analyticsCharts.files = new Chart(filesCtx, {
			type: "bar",
			data: {
				labels,
				datasets: [{
					label: "Total Issues",
					data: data.files_with_most_issues.map(f => f.total_issues),
					backgroundColor: "#f59e0bcc",
					borderColor: "#f59e0b",
					borderWidth: 1,
					borderRadius: 4,
				}],
			},
			options: {
				indexAxis: "y",
				responsive: true,
				plugins: { legend: { display: false } },
				scales: {
					x: { grid: { color: gridColor }, ticks: { color: textColor } },
					y: { grid: { display: false }, ticks: { color: textColor, font: { size: 10 } } },
				},
			},
		});
	}
}

// --- Result Rendering ---

function renderResults(data) {
	loadingSection.style.display = "none";
	errorSection.style.display = "none";
	resultsSection.style.display = "block";
	submitBtn.disabled = false;

	// Reset filter
	activeFilter = "all";
	document.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
	const allBtn = document.querySelector(".filter-btn[data-filter='all']");
	if (allBtn) allBtn.classList.add("active");

	const isLocal = data.issue_key === "LOCAL";

	// Header
	document.getElementById("issueType").textContent = data.issue_type;
	document.getElementById("resultIssueKey").textContent = isLocal ? data.issue_title : data.issue_key;
	document.getElementById("resultIssueTitle").textContent = isLocal ? data.issue_description : data.issue_title;

	// PR info row — hide entirely for local reviews
	const prInfoRow = document.querySelector(".pr-info");
	if (prInfoRow) prInfoRow.style.display = isLocal ? "none" : "flex";

	if (!isLocal) {
		document.getElementById("prNum").textContent = data.pr_number;
		document.getElementById("prTitle").textContent = data.pr_title;
		const prLink = document.getElementById("prLink");
		prLink.href = data.pr_url || "#";
	}

	// Summary numbers
	animateNumber("totalIssues", data.total_issues);
	animateNumber("totalErrors", data.errors);
	animateNumber("totalWarnings", data.warnings);
	animateNumber("totalSuggestions", data.suggestions);

	// Show suppression badge if any
	if (data.suppressed_count > 0) {
		document.getElementById("summaryText").innerHTML =
			`${escapeHtml(data.summary)} <span class="suppressed-badge">${data.suppressed_count} suppressed</span>`;
	} else {
		document.getElementById("summaryText").textContent = data.summary;
	}

	// Scope validation — hide for local reviews
	const scopeSection = document.querySelector(".scope-section");
	if (scopeSection) scopeSection.style.display = isLocal ? "none" : "block";
	if (!isLocal) renderScope(data.scope_validation);

	// Top violated rules
	renderTopRules(data.files);

	// Show HTML export button
	const exportBtn = document.getElementById("exportHTMLBtn");
	if (exportBtn) exportBtn.style.display = "inline-flex";

	// Post to GitHub button — only for GitHub reviews
	const postCheckBtn = document.getElementById("postCheckBtn");
	const checkRunResult = document.getElementById("checkRunResult");
	if (postCheckBtn) {
		if (isLocal) {
			postCheckBtn.style.display = "none";
		} else {
			postCheckBtn.style.display = "inline-flex";
			postCheckBtn.disabled = false;
			postCheckBtn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="9 11 12 14 22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/></svg> Post to GitHub`;
		}
	}
	if (checkRunResult) checkRunResult.style.display = "none";

	// File results
	renderFiles(data.files);

	// Load analytics charts
	loadAnalytics();

	// Scroll to results
	resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderTopRules(files) {
	const section = document.getElementById("topRulesSection");
	const list = document.getElementById("topRulesList");

	const ruleCounts = {};
	files.forEach(file => {
		file.issues.forEach(issue => {
			ruleCounts[issue.rule_id] = (ruleCounts[issue.rule_id] || 0) + 1;
		});
	});

	const sorted = Object.entries(ruleCounts).sort((a, b) => b[1] - a[1]).slice(0, 5);

	if (sorted.length === 0) {
		section.style.display = "none";
		return;
	}

	section.style.display = "block";
	list.innerHTML = sorted.map(([ruleId, count]) => `
		<div class="top-rule-item">
			<span class="top-rule-id">${escapeHtml(ruleId)}</span>
			<span class="top-rule-bar-wrap">
				<span class="top-rule-bar" style="width: ${Math.min(100, count * 20)}%"></span>
			</span>
			<span class="top-rule-count">${count}×</span>
		</div>
	`).join("");
}

function renderScope(scope) {
	const fill = document.getElementById("scopeFill");
	const score = document.getElementById("scopeScore");

	let color;
	if (scope.score >= 80) color = "var(--success)";
	else if (scope.score >= 50) color = "var(--warning)";
	else color = "var(--error)";

	setTimeout(() => {
		fill.style.width = scope.score + "%";
		fill.style.background = color;
	}, 100);

	score.textContent = scope.score + "%";
	score.style.color = color;

	const inScopeList = document.getElementById("inScopeList");
	inScopeList.innerHTML = "<h4>In Scope</h4>";
	if (scope.in_scope.length === 0) {
		inScopeList.innerHTML += '<div class="scope-item none">No files</div>';
	} else {
		scope.in_scope.forEach(f => {
			inScopeList.innerHTML += `<div class="scope-item">${escapeHtml(shortenPath(f))}</div>`;
		});
	}

	const outList = document.getElementById("outOfScopeList");
	outList.innerHTML = '<h4>Out of Scope</h4>';
	if (scope.out_of_scope.length === 0) {
		outList.innerHTML += '<div class="scope-item none">None detected</div>';
	} else {
		scope.out_of_scope.forEach(f => {
			outList.innerHTML += `<div class="scope-item">${escapeHtml(shortenPath(f))}</div>`;
		});
	}

	const missingList = document.getElementById("missingList");
	missingList.innerHTML = '<h4>Potentially Missing</h4>';
	if (scope.missing.length === 0) {
		missingList.innerHTML += '<div class="scope-item none">Nothing missing</div>';
	} else {
		scope.missing.forEach(m => {
			missingList.innerHTML += `<div class="scope-item">${escapeHtml(m)}</div>`;
		});
	}
}

function renderFiles(files) {
	const container = document.getElementById("fileResults");
	container.innerHTML = "";
	_fixData = [];

	if (files.length === 0) {
		container.innerHTML = `
			<div class="file-card">
				<div class="file-header">
					<span class="file-name" style="color: var(--success);">No issues found in any files.</span>
				</div>
			</div>
		`;
		return;
	}

	files.forEach((file, index) => {
		const card = document.createElement("div");
		card.className = "file-card";

		const severitiesPresent = [...new Set(file.issues.map(i => i.severity.toLowerCase()))];
		card.dataset.severities = severitiesPresent.join(",");

		let badges = "";
		if (file.error_count > 0) badges += `<span class="badge error">${file.error_count} error${file.error_count > 1 ? "s" : ""}</span>`;
		if (file.warning_count > 0) badges += `<span class="badge warning">${file.warning_count} warning${file.warning_count > 1 ? "s" : ""}</span>`;
		if (file.suggestion_count > 0) badges += `<span class="badge suggestion">${file.suggestion_count}</span>`;

		let issueRows = "";
		file.issues.forEach(issue => {
			const messageHtml = formatMessage(issue.message);
			let fixHtml = "";

			if (issue.suggested_fix) {
				const fixIdx = _fixData.length;
				_fixData.push({
					filePath: file.file_path,
					line: issue.line,
					original: issue.original_line || "",
					fix: issue.suggested_fix,
				});

				const oldLine = escapeHtml((issue.original_line || "").trimEnd());
				const newLine = escapeHtml(issue.suggested_fix.trimEnd());

				fixHtml = `
					<div class="issue-fix">
						<div class="diff-view">
							<div class="diff-line diff-old"><span class="diff-marker">−</span>${oldLine}</div>
							<div class="diff-line diff-new"><span class="diff-marker">+</span>${newLine}</div>
						</div>
						<button class="apply-fix-btn" onclick="applyFix(${fixIdx}, this)">⚡ Apply Fix</button>
					</div>
				`;
			}

			issueRows += `
				<div class="issue-row">
					<div class="issue-severity ${issue.severity.toLowerCase()}"></div>
					<div class="issue-line">L${issue.line}</div>
					<div class="issue-content">
						<div class="issue-message">${messageHtml}</div>
						${fixHtml}
					</div>
					<div class="issue-rule">${escapeHtml(issue.rule_id)}</div>
				</div>
			`;
		});

		card.innerHTML = `
			<div class="file-header" onclick="toggleFile(this)">
				<span class="file-name">${escapeHtml(shortenPath(file.file_path))}</span>
				<div class="file-badges">${badges}</div>
			</div>
			<div class="file-issues${index === 0 ? " open" : ""}">
				${issueRows}
			</div>
		`;

		container.appendChild(card);
	});
}

// --- Utility Functions ---

function toggleFile(header) {
	const issues = header.nextElementSibling;
	issues.classList.toggle("open");
}

function animateNumber(elementId, target) {
	const el = document.getElementById(elementId);
	const duration = 600;
	const startTime = performance.now();

	function update(currentTime) {
		const elapsed = currentTime - startTime;
		const progress = Math.min(elapsed / duration, 1);
		const eased = 1 - Math.pow(1 - progress, 3);
		el.textContent = Math.round(target * eased);
		if (progress < 1) requestAnimationFrame(update);
	}

	requestAnimationFrame(update);
}

function shortenPath(path) {
	const parts = path.split("/");
	if (parts.length <= 3) return path;
	return ".../" + parts.slice(-3).join("/");
}

function formatMessage(message) {
	return escapeHtml(message).replace(/`([^`]+)`/g, "<code>$1</code>");
}

function escapeHtml(text) {
	const div = document.createElement("div");
	div.textContent = text;
	return div.innerHTML;
}

// --- My Active Issues ---

const STATUS_COLORS = {
	"in progress":              { bg: "rgba(245,158,11,0.15)",  color: "#f59e0b" },
	"code review":              { bg: "rgba(99,102,241,0.15)",  color: "#818cf8" },
	"ready for qa":             { bg: "rgba(59,130,246,0.15)",  color: "#60a5fa" },
	"qa":                       { bg: "rgba(59,130,246,0.15)",  color: "#60a5fa" },
	"code review/qa complete":  { bg: "rgba(16,185,129,0.15)", color: "#34d399" },
};

function statusStyle(status) {
	return STATUS_COLORS[status.toLowerCase()] || { bg: "rgba(107,114,128,0.15)", color: "#9ca3af" };
}

async function loadMyIssues(forceRefresh = false) {
	const section = document.getElementById("activeIssuesSection");
	const list = document.getElementById("activeIssuesList");

	if (forceRefresh) {
		list.innerHTML = '<div class="issues-loading">Refreshing...</div>';
	}

	try {
		const url = forceRefresh
			? `${API_BASE}/api/my-issues?t=${Date.now()}`
			: `${API_BASE}/api/my-issues`;

		const res = await fetch(url);
		if (!res.ok) return;

		const data = await res.json();

		if (!data.configured) {
			// Jira not configured — hide the panel silently
			section.style.display = "none";
			return;
		}

		if (!data.issues || data.issues.length === 0) {
			section.style.display = "none";
			return;
		}

		section.style.display = "block";
		list.innerHTML = data.issues.map(issue => {
			const style = statusStyle(issue.status);
			const typeIcon = issueTypeIcon(issue.type);
			return `
				<div class="issue-card" onclick="selectIssue('${escapeHtml(issue.key)}')" title="${escapeHtml(issue.title)}">
					<div class="issue-card-top">
						<span class="issue-card-key">${escapeHtml(issue.key)}</span>
						<span class="issue-card-status" style="background:${style.bg};color:${style.color};">
							${escapeHtml(issue.status)}
						</span>
					</div>
					<div class="issue-card-title">${escapeHtml(issue.title)}</div>
					<div class="issue-card-meta">
						<span class="issue-card-type">${typeIcon} ${escapeHtml(issue.type)}</span>
					</div>
				</div>
			`;
		}).join("");

	} catch {
		// Network error — hide panel silently
		section.style.display = "none";
	}
}

function selectIssue(issueKey) {
	issueKeyInput.value = issueKey;
	prNumberInput.value = "";

	// Highlight selected card
	document.querySelectorAll(".issue-card").forEach(c => c.classList.remove("issue-card--selected"));
	event.currentTarget.classList.add("issue-card--selected");

	// Scroll to form and focus submit button
	document.querySelector(".input-section").scrollIntoView({ behavior: "smooth", block: "nearest" });
	submitBtn.focus();
}

function issueTypeIcon(type) {
	const icons = {
		"Bug":   "🐛",
		"Task":  "✅",
		"Story": "📖",
		"Epic":  "⚡",
	};
	return icons[type] || "🎯";
}

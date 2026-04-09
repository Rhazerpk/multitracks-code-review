# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a **MultiTracks.com Auto Code Review Bot** — a Python tool that analyzes pull requests against company coding standards. It operates in two modes:
- **GitHub Action**: Automatically reviews PRs and posts inline comments
- **Web Dashboard**: FastAPI app for interactive review with Jira integration

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
python test_rules.py
python -m pytest test_rules.py -v

# Local review against a diff file
python main.py --local test.patch

# Local review with non-zero exit code if issues found
python main.py --local test.patch --exit-code

# Live GitHub PR review (requires env vars)
python main.py

# Web dashboard
cd web && uvicorn app:app --reload
```

## Required Environment Variables

```
GITHUB_TOKEN          # GitHub Personal Access Token
GITHUB_REPOSITORY     # e.g. "owner/repo"
PR_NUMBER             # Pull request number (injected by GitHub Actions)
JIRA_BASE_URL         # https://multitracks.atlassian.net
JIRA_EMAIL            # Jira account email
JIRA_API_TOKEN        # Jira API token
```

## Architecture

### Core Pipeline (GitHub Action mode)

```
main.py → reviewer.py → diff_parser.py → rules/* → github_client.py
```

1. **`diff_parser.py`**: Parses unified diff format into `ChangedFile` dataclasses; filters out binaries/generated files
2. **`reviewer.py`**: Orchestrates the pipeline — applies all rules, deduplicates by `(file, line, rule_id)`, prioritizes by severity, caps at 30 comments
3. **`github_client.py`**: Fetches PR diffs from GitHub API, posts inline review comments; handles API 422 errors gracefully
4. **`rules/`**: Plugin-style rule system — all rules inherit `BaseRule` and are registered in `ALL_RULES` in `rules/__init__.py`

### Rule System

Rules live in `rules/` and follow a strict pattern:
- Inherit from `BaseRule` (`rules/base.py`)
- Implement `applies_to(file: ChangedFile) -> bool` (file extension check)
- Implement `analyze(file: ChangedFile) -> list[ReviewComment]`
- Return `ReviewComment(file, line, rule_id, message, severity)` objects

**Rule categories and IDs**:
- `CS-NAME-*` — C# naming conventions (fields, properties, Hungarian notation)
- `CS-STYLE-*` — C# style (line length, braces, no `this.`, no Entity Framework)
- `SQL-FMT-*` — SQL formatting (keywords uppercase, line length, no `LEFT OUTER JOIN`)
- `SQL-BP-*` — SQL best practices (no `@@IDENTITY`, bracket notation)
- `SEC-*` — Security (no hardcoded credentials, no SQL string concatenation, no hardcoded IPs)
- `GEN-*` — General quality (logging, dead code, comment quality)

To add a new rule: create a class in the appropriate `rules/*.py` file, inherit `BaseRule`, and add the instance to `ALL_RULES` in `rules/__init__.py`.

### Web Dashboard mode

```
web/app.py (FastAPI) → jira_client.py + github_client.py + reviewer.py
```

The `/api/review` endpoint accepts `{issue_key, pr_number?}`, fetches the Jira issue and linked PR, runs the static analysis, and performs **scope validation** — it maps issue keywords to expected directories and scores how well the PR's changed files align with the issue scope.

### Severity levels

`ERROR` → marks PR as "REQUEST_CHANGES" | `WARNING` → informational | `SUGGESTION` → optional improvement

### File filtering

Reviewable extensions: `.cs, .sql, .config, .json, .xml, .yml, .csproj, .props, .js, .ts, .css, .html, .cshtml, .aspx, .ascx, .master`

Excluded: binaries, images, `*.min.js`, `*.min.css`, `*.map`, `packages/`, `node_modules/`, `bin/`, `obj/`, `.vs/`

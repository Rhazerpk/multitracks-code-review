"""
SQLite persistence layer for the Code Review Dashboard.

All database access goes through this module. Uses sqlite3 from the standard
library. Connections are opened and closed explicitly so that on Windows the
file lock is released immediately after each operation.
"""

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_DB_PATH = Path(__file__).parent / "reviews.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path: str | None = None) -> None:
    """Create tables if they don't exist. Call once at startup."""
    global _DB_PATH
    if db_path:
        _DB_PATH = Path(db_path)

    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS reviews (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                review_uuid  TEXT UNIQUE NOT NULL,
                issue_key    TEXT NOT NULL,
                pr_number    INTEGER NOT NULL,
                pr_title     TEXT,
                pr_url       TEXT,
                errors       INTEGER DEFAULT 0,
                warnings     INTEGER DEFAULT 0,
                suggestions  INTEGER DEFAULT 0,
                total        INTEGER DEFAULT 0,
                scope_score  INTEGER DEFAULT 100,
                summary      TEXT,
                diff_text    TEXT,
                created_at   TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS review_files (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id        INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
                file_path        TEXT NOT NULL,
                error_count      INTEGER DEFAULT 0,
                warning_count    INTEGER DEFAULT 0,
                suggestion_count INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS review_issues (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                file_id       INTEGER NOT NULL REFERENCES review_files(id) ON DELETE CASCADE,
                line_number   INTEGER,
                rule_id       TEXT,
                severity      TEXT,
                message       TEXT,
                original_line TEXT,
                suggested_fix TEXT,
                suppressed    INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_reviews_created ON reviews(created_at);
            CREATE INDEX IF NOT EXISTS idx_issues_rule ON review_issues(rule_id);
        """)
    finally:
        conn.close()


def save_review(review_data: dict) -> str:
    """
    Persist a completed review to SQLite.

    Expected keys in review_data:
        issue_key, pr_number, pr_title, pr_url,
        errors, warnings, suggestions, scope_score, summary, diff_text,
        files: list of {file_path, error_count, warning_count, suggestion_count,
                        issues: list of {line, rule_id, severity, message,
                                         original_line, suggested_fix, suppressed}}

    Returns the review_uuid string.
    """
    review_uuid = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    total = review_data.get("errors", 0) + review_data.get("warnings", 0) + review_data.get("suggestions", 0)

    # Truncate diff at 500 KB to keep DB size reasonable
    diff_text = review_data.get("diff_text", "") or ""
    if len(diff_text) > 500_000:
        diff_text = diff_text[:500_000] + "\n[diff truncated]"

    conn = _connect()
    try:
        cur = conn.execute(
            """
            INSERT INTO reviews
                (review_uuid, issue_key, pr_number, pr_title, pr_url,
                 errors, warnings, suggestions, total, scope_score, summary, diff_text, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review_uuid,
                review_data["issue_key"],
                review_data["pr_number"],
                review_data.get("pr_title", ""),
                review_data.get("pr_url", ""),
                review_data.get("errors", 0),
                review_data.get("warnings", 0),
                review_data.get("suggestions", 0),
                total,
                review_data.get("scope_score", 100),
                review_data.get("summary", ""),
                diff_text,
                created_at,
            ),
        )
        review_id = cur.lastrowid

        for file_info in review_data.get("files", []):
            cur2 = conn.execute(
                """
                INSERT INTO review_files
                    (review_id, file_path, error_count, warning_count, suggestion_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    file_info["file_path"],
                    file_info.get("error_count", 0),
                    file_info.get("warning_count", 0),
                    file_info.get("suggestion_count", 0),
                ),
            )
            file_id = cur2.lastrowid

            for issue in file_info.get("issues", []):
                conn.execute(
                    """
                    INSERT INTO review_issues
                        (file_id, line_number, rule_id, severity, message,
                         original_line, suggested_fix, suppressed)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        issue.get("line"),
                        issue.get("rule_id", ""),
                        issue.get("severity", ""),
                        issue.get("message", ""),
                        issue.get("original_line"),
                        issue.get("suggested_fix"),
                        1 if issue.get("suppressed") else 0,
                    ),
                )

        conn.commit()
    finally:
        conn.close()

    return review_uuid


def get_history(limit: int = 50) -> list[dict]:
    """Return the most recent reviews, newest first."""
    conn = _connect()
    try:
        rows = conn.execute(
            """
            SELECT review_uuid, issue_key, pr_number, pr_title, pr_url,
                   errors, warnings, suggestions, total, scope_score, summary, created_at
            FROM reviews
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_review_by_uuid(review_uuid: str) -> dict | None:
    """
    Return a full review with files and issues for export/AI.
    Returns None if not found.
    """
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM reviews WHERE review_uuid = ?", (review_uuid,)
        ).fetchone()
        if not row:
            return None

        review = dict(row)
        review_id = review["id"]

        files = conn.execute(
            "SELECT * FROM review_files WHERE review_id = ? ORDER BY id",
            (review_id,),
        ).fetchall()

        review["files"] = []
        for f in files:
            file_dict = dict(f)
            issues = conn.execute(
                "SELECT * FROM review_issues WHERE file_id = ? ORDER BY line_number",
                (f["id"],),
            ).fetchall()
            file_dict["issues"] = [dict(i) for i in issues]
            review["files"].append(file_dict)

        return review
    finally:
        conn.close()


def get_analytics() -> dict:
    """
    Return aggregate statistics across all stored reviews:
    - top_violations: [{rule_id, count}] top 10 non-suppressed
    - score_trend: [{date, avg_score}] last 30 days
    - files_with_most_issues: [{file_path, total_issues}] top 10
    - total_reviews: int
    - avg_issues_per_review: float
    """
    conn = _connect()
    try:
        top_violations = conn.execute(
            """
            SELECT rule_id, COUNT(*) as count
            FROM review_issues
            WHERE suppressed = 0 AND rule_id != ''
            GROUP BY rule_id
            ORDER BY count DESC
            LIMIT 10
            """
        ).fetchall()

        score_trend = conn.execute(
            """
            SELECT DATE(created_at) as day, ROUND(AVG(scope_score), 1) as avg_score
            FROM reviews
            WHERE created_at >= DATE('now', '-30 days')
            GROUP BY day
            ORDER BY day ASC
            """
        ).fetchall()

        files_most_issues = conn.execute(
            """
            SELECT file_path,
                   SUM(error_count + warning_count + suggestion_count) as total_issues
            FROM review_files
            GROUP BY file_path
            ORDER BY total_issues DESC
            LIMIT 10
            """
        ).fetchall()

        totals = conn.execute(
            "SELECT COUNT(*) as total_reviews, AVG(total) as avg_issues FROM reviews"
        ).fetchone()

        return {
            "top_violations": [dict(r) for r in top_violations],
            "score_trend": [dict(r) for r in score_trend],
            "files_with_most_issues": [dict(r) for r in files_most_issues],
            "total_reviews": totals["total_reviews"] if totals else 0,
            "avg_issues_per_review": round(totals["avg_issues"] or 0, 1) if totals else 0,
        }
    finally:
        conn.close()

"""SQLite persistence layer - the backbone of coherent cross-agent state.

Everything an agent needs to run is recoverable from this database plus the
per-job artifact directory, so a browser refresh, a Streamlit rerun, or an app
restart never loses pipeline context. Each agent writes a structured
``payload`` on success; downstream agents read their upstream payload rather
than re-deriving anything, which is what keeps the five agents coherent.
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from . import config, security

SCHEMA_VERSION = 1

_DDL = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT PRIMARY KEY,
    asset_name       TEXT NOT NULL,
    entity_name      TEXT,
    entity_type      TEXT,
    audience         TEXT,
    duration_hours   REAL NOT NULL DEFAULT 0,
    content_brief    TEXT,
    coverage_brief   TEXT,
    palette_override TEXT,
    palette_json     TEXT,
    status           TEXT NOT NULL DEFAULT 'draft',
    current_agent    INTEGER NOT NULL DEFAULT 1,
    key_fingerprint  TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS token_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT,
    job_name      TEXT NOT NULL,
    agent_name    TEXT NOT NULL,
    agent_no      INTEGER,
    model         TEXT,
    call_kind     TEXT,
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    thought_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    key_fingerprint TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_token_log_job ON token_log(job_id);

CREATE TABLE IF NOT EXISTS agent_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id       TEXT NOT NULL,
    agent_no     INTEGER NOT NULL,
    run_index    INTEGER NOT NULL DEFAULT 1,
    status       TEXT NOT NULL DEFAULT 'pending',
    progress     REAL NOT NULL DEFAULT 0,
    message      TEXT,
    payload_json TEXT,
    error        TEXT,
    started_at   TEXT,
    finished_at  TEXT,
    UNIQUE(job_id, agent_no, run_index)
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_job ON agent_runs(job_id, agent_no);

CREATE TABLE IF NOT EXISTS reviews (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id        TEXT NOT NULL,
    agent_no      INTEGER NOT NULL,
    reviewer_name TEXT NOT NULL,
    comments      TEXT,
    decision      TEXT NOT NULL,
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reviews_job ON reviews(job_id, agent_no);

CREATE TABLE IF NOT EXISTS artifacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL,
    agent_no   INTEGER NOT NULL,
    kind       TEXT NOT NULL,
    label      TEXT,
    filename   TEXT NOT NULL,
    path       TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_artifacts_job ON artifacts(job_id, agent_no);

CREATE TABLE IF NOT EXISTS uploads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id         TEXT NOT NULL,
    filename       TEXT NOT NULL,
    path           TEXT NOT NULL,
    size_bytes     INTEGER NOT NULL DEFAULT 0,
    extracted_text TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_uploads_job ON uploads(job_id);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT,
    agent_no   INTEGER,
    level      TEXT NOT NULL DEFAULT 'info',
    message    TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Short-lived connection; WAL keeps Streamlit reruns from blocking."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


_initialised = False


def init_db(force: bool = False) -> None:
    global _initialised
    if _initialised and not force:
        return
    with connect() as conn:
        conn.executescript(_DDL)
        conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO NOTHING",
            ("schema_version", str(SCHEMA_VERSION), now_iso()),
        )
    _initialised = True


# --------------------------------------------------------------------------
# Settings (incl. the encrypted API key)
# --------------------------------------------------------------------------
def set_setting(key: str, value: str | None) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO settings(key, value, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
            "updated_at=excluded.updated_at",
            (key, value, now_iso()),
        )


def get_setting(key: str, default: str | None = None) -> str | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else default


def delete_setting(key: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM settings WHERE key=?", (key,))


API_KEY_SETTING = "gemini_api_key_enc"
API_KEY_STATUS = "gemini_api_key_status"
API_KEY_CHECKED = "gemini_api_key_checked_at"
API_KEY_MODEL = "gemini_api_key_model"
API_KEY_FINGERPRINT = "gemini_api_key_fp"
PALETTE_SETTING = "default_palette_override"


def save_api_key(plaintext: str) -> str:
    """Persist the key encrypted; returns its fingerprint."""
    token = security.encrypt_secret(plaintext.strip())
    fp = security.key_fingerprint(plaintext.strip())
    set_setting(API_KEY_SETTING, token)
    set_setting(API_KEY_FINGERPRINT, fp)
    return fp


def load_api_key() -> str | None:
    token = get_setting(API_KEY_SETTING)
    if not token:
        return None
    return security.decrypt_secret(token)


def clear_api_key() -> None:
    for key in (API_KEY_SETTING, API_KEY_STATUS, API_KEY_CHECKED,
                API_KEY_MODEL, API_KEY_FINGERPRINT):
        delete_setting(key)


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------
@dataclass
class Job:
    id: str
    asset_name: str
    entity_name: str
    entity_type: str
    audience: str
    duration_hours: float
    content_brief: str
    coverage_brief: str
    palette_override: str
    palette: dict[str, str]
    status: str
    current_agent: int
    created_at: str
    updated_at: str

    @property
    def dir(self) -> Path:
        return config.JOBS_DIR / self.id

    @property
    def upload_dir(self) -> Path:
        return self.dir / config.UPLOADS_DIRNAME

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Job":
        try:
            palette = json.loads(row["palette_json"] or "{}") or {}
        except json.JSONDecodeError:
            palette = {}
        if not palette:
            palette = config.resolve_palette(row["palette_override"])
        return cls(
            id=row["id"],
            asset_name=row["asset_name"],
            entity_name=row["entity_name"] or "",
            entity_type=row["entity_type"] or "",
            audience=row["audience"] or "",
            duration_hours=float(row["duration_hours"] or 0),
            content_brief=row["content_brief"] or "",
            coverage_brief=row["coverage_brief"] or "",
            palette_override=row["palette_override"] or "",
            palette=palette,
            status=row["status"],
            current_agent=int(row["current_agent"] or 1),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def create_job(
    asset_name: str,
    *,
    entity_name: str = "",
    entity_type: str = "",
    audience: str = "",
    duration_hours: float = 0.0,
    content_brief: str = "",
    coverage_brief: str = "",
    palette_override: str = "",
    key_fingerprint: str | None = None,
) -> Job:
    init_db()
    job_id = f"job-{time.strftime('%Y%m%d-%H%M%S')}-{security.new_token()[:6]}"
    palette = config.resolve_palette(palette_override)
    stamp = now_iso()
    with connect() as conn:
        conn.execute(
            """INSERT INTO jobs(id, asset_name, entity_name, entity_type, audience,
                                duration_hours, content_brief, coverage_brief,
                                palette_override, palette_json, status, current_agent,
                                key_fingerprint, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, asset_name.strip(), entity_name.strip(), entity_type.strip(),
             audience.strip(), float(duration_hours), content_brief, coverage_brief,
             palette_override, json.dumps(palette), "in_progress", 1,
             key_fingerprint, stamp, stamp),
        )
    (config.JOBS_DIR / job_id / config.UPLOADS_DIRNAME).mkdir(parents=True, exist_ok=True)
    job = get_job(job_id)
    assert job is not None
    log_event(job_id, None, "info", f"Job created for asset '{asset_name}'.")
    return job


def get_job(job_id: str) -> Job | None:
    init_db()
    with connect() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return Job.from_row(row) if row else None


def list_jobs(limit: int = 100) -> list[Job]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [Job.from_row(r) for r in rows]


def update_job(job_id: str, **fields: Any) -> None:
    if not fields:
        return
    allowed = {"asset_name", "entity_name", "entity_type", "audience",
               "duration_hours", "content_brief", "coverage_brief",
               "palette_override", "palette_json", "status", "current_agent"}
    sets, values = [], []
    for key, value in fields.items():
        if key in allowed:
            sets.append(f"{key}=?")
            values.append(value)
    if not sets:
        return
    sets.append("updated_at=?")
    values.extend([now_iso(), job_id])
    with connect() as conn:
        conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id=?", values)


def delete_job(job_id: str, remove_files: bool = True) -> None:
    init_db()
    with connect() as conn:
        for table in ("token_log", "agent_runs", "reviews", "artifacts",
                      "uploads", "events"):
            conn.execute(f"DELETE FROM {table} WHERE job_id=?", (job_id,))
        conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
    if remove_files:
        import shutil
        target = config.JOBS_DIR / job_id
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)


# --------------------------------------------------------------------------
# Agent runs - progress + coherent payload hand-off
# --------------------------------------------------------------------------
def start_agent_run(job_id: str, agent_no: int) -> int:
    """Open a new run row for an agent (rerun-safe) and return its run_index."""
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(run_index), 0) AS m FROM agent_runs "
            "WHERE job_id=? AND agent_no=?",
            (job_id, agent_no),
        ).fetchone()
        run_index = int(row["m"]) + 1
        conn.execute(
            """INSERT INTO agent_runs(job_id, agent_no, run_index, status, progress,
                                      message, started_at)
               VALUES(?,?,?,'running',0,'Starting...',?)""",
            (job_id, agent_no, run_index, now_iso()),
        )
    return run_index


def update_agent_run(
    job_id: str,
    agent_no: int,
    run_index: int,
    *,
    status: str | None = None,
    progress: float | None = None,
    message: str | None = None,
    payload: dict | None = None,
    error: str | None = None,
    finished: bool = False,
) -> None:
    sets: list[str] = []
    values: list[Any] = []
    if status is not None:
        sets.append("status=?"); values.append(status)
    if progress is not None:
        sets.append("progress=?"); values.append(max(0.0, min(1.0, float(progress))))
    if message is not None:
        sets.append("message=?"); values.append(message[:2000])
    if payload is not None:
        sets.append("payload_json=?"); values.append(json.dumps(payload, default=str))
    if error is not None:
        sets.append("error=?"); values.append(security.redact(error)[:4000])
    if finished:
        sets.append("finished_at=?"); values.append(now_iso())
    if not sets:
        return
    values.extend([job_id, agent_no, run_index])
    with connect() as conn:
        conn.execute(
            f"UPDATE agent_runs SET {', '.join(sets)} "
            "WHERE job_id=? AND agent_no=? AND run_index=?",
            values,
        )


def latest_run(job_id: str, agent_no: int) -> dict | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_runs WHERE job_id=? AND agent_no=? "
            "ORDER BY run_index DESC LIMIT 1",
            (job_id, agent_no),
        ).fetchone()
    return dict(row) if row else None


def latest_successful_run(job_id: str, agent_no: int) -> dict | None:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM agent_runs WHERE job_id=? AND agent_no=? AND status='completed' "
            "ORDER BY run_index DESC LIMIT 1",
            (job_id, agent_no),
        ).fetchone()
    return dict(row) if row else None


def agent_payload(job_id: str, agent_no: int) -> dict | None:
    """The structured hand-off payload from an agent's last successful run."""
    run = latest_successful_run(job_id, agent_no)
    if not run or not run.get("payload_json"):
        return None
    try:
        return json.loads(run["payload_json"])
    except json.JSONDecodeError:
        return None


def all_runs(job_id: str) -> dict[int, dict]:
    """Latest run per agent, for the sidebar progression display."""
    init_db()
    out: dict[int, dict] = {}
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE job_id=? ORDER BY agent_no, run_index",
            (job_id,),
        ).fetchall()
    for row in rows:
        out[int(row["agent_no"])] = dict(row)
    return out


def run_count(job_id: str, agent_no: int) -> int:
    init_db()
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM agent_runs WHERE job_id=? AND agent_no=?",
            (job_id, agent_no),
        ).fetchone()
    return int(row["c"] or 0)


# --------------------------------------------------------------------------
# Token log
# --------------------------------------------------------------------------
def log_tokens(
    *,
    job_id: str | None,
    job_name: str,
    agent_name: str,
    agent_no: int | None,
    model: str | None,
    call_kind: str,
    prompt_tokens: int,
    output_tokens: int,
    thought_tokens: int,
    total_tokens: int,
    key_fingerprint: str | None = None,
) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """INSERT INTO token_log(job_id, job_name, agent_name, agent_no, model,
                                     call_kind, prompt_tokens, output_tokens,
                                     thought_tokens, total_tokens, key_fingerprint,
                                     created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, job_name, agent_name, agent_no, model, call_kind,
             int(prompt_tokens), int(output_tokens), int(thought_tokens),
             int(total_tokens), key_fingerprint, now_iso()),
        )


def token_rows(job_id: str | None = None, limit: int = 5000) -> list[dict]:
    init_db()
    sql = "SELECT * FROM token_log"
    params: list[Any] = []
    if job_id:
        sql += " WHERE job_id=?"
        params.append(job_id)
    sql += " ORDER BY id ASC LIMIT ?"
    params.append(limit)
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def token_totals(job_id: str | None = None) -> dict[str, int]:
    init_db()
    sql = ("SELECT COALESCE(SUM(prompt_tokens),0) p, COALESCE(SUM(output_tokens),0) o, "
           "COALESCE(SUM(thought_tokens),0) th, COALESCE(SUM(total_tokens),0) t, "
           "COUNT(*) c FROM token_log")
    params: list[Any] = []
    if job_id:
        sql += " WHERE job_id=?"
        params.append(job_id)
    with connect() as conn:
        row = conn.execute(sql, params).fetchone()
    return {"prompt": int(row["p"]), "output": int(row["o"]),
            "thoughts": int(row["th"]), "total": int(row["t"]),
            "calls": int(row["c"])}


def tokens_by_agent(job_id: str | None = None) -> dict[int, int]:
    init_db()
    sql = ("SELECT agent_no, COALESCE(SUM(total_tokens),0) t FROM token_log "
           "WHERE agent_no IS NOT NULL")
    params: list[Any] = []
    if job_id:
        sql += " AND job_id=?"
        params.append(job_id)
    sql += " GROUP BY agent_no"
    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {int(r["agent_no"]): int(r["t"]) for r in rows}


# --------------------------------------------------------------------------
# Reviews
# --------------------------------------------------------------------------
def add_review(job_id: str, agent_no: int, reviewer_name: str,
               comments: str, decision: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            """INSERT INTO reviews(job_id, agent_no, reviewer_name, comments,
                                   decision, created_at)
               VALUES(?,?,?,?,?,?)""",
            (job_id, agent_no, reviewer_name.strip(), comments.strip(),
             decision, now_iso()),
        )


def reviews_for(job_id: str, agent_no: int | None = None) -> list[dict]:
    init_db()
    sql = "SELECT * FROM reviews WHERE job_id=?"
    params: list[Any] = [job_id]
    if agent_no is not None:
        sql += " AND agent_no=?"
        params.append(agent_no)
    sql += " ORDER BY id DESC"
    with connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def latest_review(job_id: str, agent_no: int) -> dict | None:
    rows = reviews_for(job_id, agent_no)
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# Artifacts & uploads
# --------------------------------------------------------------------------
def add_artifact(job_id: str, agent_no: int, kind: str, path: Path,
                 label: str = "") -> None:
    init_db()
    path = Path(path)
    size = path.stat().st_size if path.exists() else 0
    with connect() as conn:
        conn.execute("DELETE FROM artifacts WHERE job_id=? AND agent_no=? AND path=?",
                     (job_id, agent_no, str(path)))
        conn.execute(
            """INSERT INTO artifacts(job_id, agent_no, kind, label, filename, path,
                                     size_bytes, created_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (job_id, agent_no, kind, label or path.name, path.name, str(path),
             size, now_iso()),
        )


def artifacts_for(job_id: str, agent_no: int | None = None,
                  kind: str | None = None) -> list[dict]:
    init_db()
    sql = "SELECT * FROM artifacts WHERE job_id=?"
    params: list[Any] = [job_id]
    if agent_no is not None:
        sql += " AND agent_no=?"
        params.append(agent_no)
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY id ASC"
    with connect() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    return [r for r in rows if Path(r["path"]).exists()]


def clear_artifacts(job_id: str, agent_no: int) -> None:
    init_db()
    with connect() as conn:
        conn.execute("DELETE FROM artifacts WHERE job_id=? AND agent_no=?",
                     (job_id, agent_no))


def add_upload(job_id: str, filename: str, path: Path, extracted_text: str = "") -> None:
    init_db()
    path = Path(path)
    with connect() as conn:
        conn.execute("DELETE FROM uploads WHERE job_id=? AND filename=?",
                     (job_id, filename))
        conn.execute(
            """INSERT INTO uploads(job_id, filename, path, size_bytes,
                                   extracted_text, created_at)
               VALUES(?,?,?,?,?,?)""",
            (job_id, filename, str(path),
             path.stat().st_size if path.exists() else 0,
             extracted_text, now_iso()),
        )


def uploads_for(job_id: str) -> list[dict]:
    init_db()
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM uploads WHERE job_id=? ORDER BY id", (job_id,)).fetchall()]


def upload_context(job_id: str, max_chars: int = 24_000) -> str:
    """Concatenated text extracted from the uploaded guide sheets/syllabi."""
    chunks: list[str] = []
    budget = max_chars
    for row in uploads_for(job_id):
        text = (row.get("extracted_text") or "").strip()
        if not text:
            continue
        header = f"\n--- Source document: {row['filename']} ---\n"
        slice_ = text[: max(0, budget - len(header))]
        if not slice_:
            break
        chunks.append(header + slice_)
        budget -= len(header) + len(slice_)
        if budget <= 0:
            break
    return "".join(chunks)


# --------------------------------------------------------------------------
# Events (run log)
# --------------------------------------------------------------------------
def log_event(job_id: str | None, agent_no: int | None, level: str,
              message: str) -> None:
    init_db()
    with connect() as conn:
        conn.execute(
            "INSERT INTO events(job_id, agent_no, level, message, created_at) "
            "VALUES(?,?,?,?,?)",
            (job_id, agent_no, level, security.redact(message)[:4000], now_iso()),
        )


def events_for(job_id: str, limit: int = 400) -> list[dict]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE job_id=? ORDER BY id DESC LIMIT ?",
            (job_id, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]

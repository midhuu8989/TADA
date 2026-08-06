"""Agentic orchestrator: gates, context hand-off and progress reporting.

The orchestrator owns the *pipeline contract*:

* an agent may only run when its upstream dependency has a successful payload
  **and** (from Agent 1 onward) a human review decision exists for the previous
  stage - the specification calls for a manual decision before advancing;
* each run gets a fresh :class:`AgentContext` carrying the job, a token-accounted
  client, the resolved palette, the upstream payloads and any reviewer comments
  from an earlier attempt, so a rerun can actually act on the feedback;
* progress is written to SQLite as it happens, so the sidebar, the token
  dashboard and a browser refresh all observe the same state.
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config, llm, security, store

ProgressFn = Callable[[float, str], None]


@dataclass
class AgentContext:
    """Everything one agent run needs, and nothing it does not."""

    job: store.Job
    client: llm.GeminiClient
    agent_no: int
    run_index: int
    palette: dict[str, str]
    out_dir: Path
    upstream: dict[int, dict] = field(default_factory=dict)
    review_comments: str = ""
    reviewer_name: str = ""
    options: dict = field(default_factory=dict)
    _progress: ProgressFn | None = None

    # ---------------------------------------------------------------- info
    @property
    def spec(self) -> config.AgentSpec:
        return config.AGENT_BY_NUMBER[self.agent_no]

    @property
    def is_rerun(self) -> bool:
        return self.run_index > 1

    def payload(self, agent_no: int) -> dict:
        """Upstream hand-off payload, or an empty dict."""
        return self.upstream.get(agent_no) or {}

    # ------------------------------------------------------------ progress
    def progress(self, fraction: float, message: str = "") -> None:
        fraction = max(0.0, min(1.0, float(fraction)))
        store.update_agent_run(self.job.id, self.agent_no, self.run_index,
                               progress=fraction, message=message or None)
        if self._progress:
            try:
                self._progress(fraction, message)
            except Exception:
                pass  # UI callback must never break a run

    def log(self, message: str, level: str = "info") -> None:
        store.log_event(self.job.id, self.agent_no, level, message)

    def step(self, fraction: float, message: str) -> None:
        """Progress + log in one call - the common case inside agents."""
        self.progress(fraction, message)
        self.log(message)

    # ----------------------------------------------------------- artifacts
    def artifact_path(self, filename: str) -> Path:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        return self.out_dir / filename

    def register(self, path: Path, kind: str, label: str = "") -> None:
        store.add_artifact(self.job.id, self.agent_no, kind, Path(path), label)

    def review_directive(self) -> str:
        """Prompt fragment instructing the model to act on reviewer feedback."""
        if not self.review_comments.strip():
            return ""
        return (
            "\n\n### REVIEWER FEEDBACK FROM THE PREVIOUS ATTEMPT (must be addressed)\n"
            f"Reviewer: {self.reviewer_name or 'Reviewer'}\n"
            f"{self.review_comments.strip()}\n"
            "Revise your output so every point above is visibly resolved."
        )


class GateError(RuntimeError):
    """Raised when an agent is asked to run before its gate is satisfied."""


def gate_status(job_id: str, agent_no: int) -> tuple[bool, str]:
    """Can ``agent_no`` run right now? Returns ``(allowed, reason)``."""
    if agent_no == 1:
        return True, "Ready to generate the guide sheet."

    previous = agent_no - 1
    prev_spec = config.AGENT_BY_NUMBER[previous]
    if not store.agent_payload(job_id, previous):
        return False, (f"Agent {previous} ({prev_spec.short}) has not completed "
                       "successfully yet.")

    review = store.latest_review(job_id, previous)
    if not review:
        return False, (f"Capture a reviewer name and review comment for Agent "
                       f"{previous} ({prev_spec.short}), then choose to proceed.")
    if review["decision"] == "rerun":
        return False, (f"Agent {previous} is marked for rerun by "
                       f"{review['reviewer_name']}. Rerun it, then review again.")
    return True, f"Approved by {review['reviewer_name']}."


def unlocked_agents(job_id: str) -> set[int]:
    return {a.number for a in config.AGENTS if gate_status(job_id, a.number)[0]}


def overall_progress(job_id: str) -> tuple[float, int, int]:
    """``(fraction, completed_count, in_flight_agent)`` for the sidebar."""
    runs = store.all_runs(job_id)
    completed = sum(1 for n in range(1, config.TOTAL_AGENTS + 1)
                    if (runs.get(n) or {}).get("status") == "completed")
    active = 0
    partial = 0.0
    for number in range(1, config.TOTAL_AGENTS + 1):
        run = runs.get(number) or {}
        if run.get("status") == "running":
            active = number
            partial = float(run.get("progress") or 0.0)
            break
    fraction = (completed + partial) / config.TOTAL_AGENTS
    return min(1.0, fraction), completed, active


def build_context(
    job: store.Job,
    agent_no: int,
    run_index: int,
    *,
    progress_fn: ProgressFn | None = None,
    options: dict | None = None,
) -> AgentContext:
    spec = config.AGENT_BY_NUMBER[agent_no]
    call_ctx = llm.CallContext(job_id=job.id, job_name=job.asset_name,
                              agent_no=agent_no, agent_name=spec.name)
    client = llm.get_client(call_ctx)

    upstream = {n: (store.agent_payload(job.id, n) or {})
                for n in range(1, agent_no)}

    review = store.latest_review(job.id, agent_no)
    comments, reviewer = "", ""
    if review and review["decision"] == "rerun":
        comments, reviewer = review["comments"] or "", review["reviewer_name"]

    out_dir = job.dir / f"agent{agent_no}"
    return AgentContext(
        job=job, client=client, agent_no=agent_no, run_index=run_index,
        palette=job.palette, out_dir=out_dir, upstream=upstream,
        review_comments=comments, reviewer_name=reviewer,
        options=options or {}, _progress=progress_fn,
    )


def run_agent(
    job: store.Job,
    agent_no: int,
    *,
    progress_fn: ProgressFn | None = None,
    options: dict | None = None,
    enforce_gate: bool = True,
) -> dict:
    """Execute one agent end to end, persisting status, payload and errors."""
    from .agents import RUNNERS

    if enforce_gate:
        allowed, reason = gate_status(job.id, agent_no)
        if not allowed:
            raise GateError(reason)

    spec = config.AGENT_BY_NUMBER[agent_no]
    run_index = store.start_agent_run(job.id, agent_no)
    store.update_job(job.id, current_agent=agent_no, status="in_progress")
    store.log_event(job.id, agent_no,
                    "info", f"{spec.name} run #{run_index} started.")

    try:
        ctx = build_context(job, agent_no, run_index,
                           progress_fn=progress_fn, options=options)
    except llm.LLMError as exc:
        message = security.redact(exc)
        store.update_agent_run(job.id, agent_no, run_index, status="failed",
                               message=message, error=message, finished=True)
        store.log_event(job.id, agent_no, "error", message)
        raise

    # A rerun replaces the previous attempt's downloadable output.
    store.clear_artifacts(job.id, agent_no)

    try:
        ctx.progress(0.02, "Preparing context...")
        payload = RUNNERS[agent_no](ctx) or {}
        if ctx.client.notices:
            payload.setdefault("notices", []).extend(ctx.client.notices)
        payload["run_index"] = run_index
        payload["tokens_used"] = store.tokens_by_agent(job.id).get(agent_no, 0)

        store.update_agent_run(
            job.id, agent_no, run_index, status="completed", progress=1.0,
            message=payload.get("summary", "Completed."), payload=payload,
            finished=True,
        )
        store.log_event(job.id, agent_no, "success",
                        f"{spec.name} completed: {payload.get('summary', 'done')}")
        if agent_no == config.TOTAL_AGENTS:
            store.update_job(job.id, status="completed")
        return payload

    except Exception as exc:
        message = security.redact(exc) or exc.__class__.__name__
        detail = security.redact(traceback.format_exc())
        store.update_agent_run(job.id, agent_no, run_index, status="failed",
                               message=f"Failed: {message[:300]}",
                               error=detail, finished=True)
        store.log_event(job.id, agent_no, "error", f"{spec.name} failed: {message}")
        raise

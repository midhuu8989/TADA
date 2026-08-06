"""App shell: session state, navigation and the sidebar agent-progression rail."""

from __future__ import annotations

import html
from pathlib import Path

import streamlit as st

from .. import config, graphics, llm, orchestrator, store, theme

PAGE_LANDING = "landing"
PAGE_ADMIN = "admin"
PAGE_JOBS = "jobs"


def bootstrap() -> None:
    """One-time process setup: database, assets, key adoption from the env."""
    if st.session_state.get("_bootstrapped"):
        return
    store.init_db()
    graphics.build_logo()
    adopted = llm.bootstrap_key_from_env()
    if adopted:
        st.session_state["_key_from_env"] = adopted
    st.session_state.setdefault("page", PAGE_LANDING)
    st.session_state.setdefault("admin_unlocked", False)
    st.session_state.setdefault("job_id", None)
    st.session_state["_bootstrapped"] = True


def current_job() -> store.Job | None:
    job_id = st.session_state.get("job_id")
    if not job_id:
        return None
    job = store.get_job(job_id)
    if job is None:
        st.session_state["job_id"] = None
    return job


def goto(page: str) -> None:
    st.session_state["page"] = page
    st.rerun()


def palette() -> dict[str, str]:
    job = current_job()
    if job and job.palette:
        return job.palette
    return config.resolve_palette(store.get_setting(store.PALETTE_SETTING))


# --------------------------------------------------------------------------
# Sidebar
# --------------------------------------------------------------------------
_STATE_LABEL = {
    "completed": "Completed",
    "running": "Running",
    "failed": "Failed",
    "pending": "Pending",
}


def _agent_row(spec: config.AgentSpec, run: dict | None, *, unlocked: bool,
               is_current: bool, tokens: int, reviewed: dict | None) -> str:
    status = (run or {}).get("status") or ("pending" if unlocked else "locked")
    progress = float((run or {}).get("progress") or 0.0)

    css = "lada-agent"
    if status == "completed":
        css += " done"
    elif status == "running":
        css += " current"
    elif status == "failed":
        css += " failed"
    elif not unlocked:
        css += " locked"
    if is_current and status not in ("completed", "failed"):
        css += " current"

    if status == "completed":
        detail = "Completed"
        if reviewed:
            detail += (f" &middot; reviewed by {html.escape(reviewed['reviewer_name'])}"
                       if reviewed["decision"] == "proceed"
                       else " &middot; marked for rerun")
        else:
            detail += " &middot; awaiting review"
    elif status == "running":
        detail = f"{progress * 100:.0f}% &middot; " + html.escape(
            str((run or {}).get("message") or "working"))[:44]
    elif status == "failed":
        detail = "Failed &middot; see the run log"
    elif not unlocked:
        detail = "Locked until the previous stage is approved"
    else:
        detail = "Ready to run"

    bar = ""
    if status in ("running", "completed"):
        width = 100.0 if status == "completed" else progress * 100
        bar = (f'<div class="lada-agent-bar"><div style="width:{width:.0f}%">'
               "</div></div>")

    chip = (f'<span class="lada-tokchip">{tokens:,} tok</span>' if tokens else "")
    return (
        f'<div class="{css}">'
        f'<div class="lada-agent-badge">{spec.icon}</div>'
        f'<div class="lada-agent-body">'
        f'<div class="lada-agent-name">{html.escape(spec.short)} {chip}</div>'
        f'<div class="lada-agent-state">{detail}</div>{bar}'
        f"</div></div>"
    )


def sidebar() -> None:
    job = current_job()
    key_state = llm.read_key_state()

    with st.sidebar:
        logo = graphics.logo_for(palette())
        if Path(logo).exists():
            st.image(str(logo), width="stretch")

        st.markdown('<div class="lada-rail-head">Generative AI key</div>',
                    unsafe_allow_html=True)
        st.markdown(theme.key_pill(key_state.pill_state, key_state.masked),
                    unsafe_allow_html=True)

        st.markdown('<div class="lada-rail-head">Agent progression</div>',
                    unsafe_allow_html=True)

        if job is None:
            st.markdown(
                '<div class="lada-overall"><div class="lada-overall-top">'
                "<b>0 / 5</b><span>no job yet</span></div>"
                '<div class="lada-bar-outer"><div class="lada-bar-inner" '
                'style="width:0%"></div></div></div>',
                unsafe_allow_html=True)
            for spec in config.AGENTS:
                st.markdown(
                    _agent_row(spec, None, unlocked=spec.number == 1,
                               is_current=False, tokens=0, reviewed=None),
                    unsafe_allow_html=True)
        else:
            fraction, completed, active = orchestrator.overall_progress(job.id)
            runs = store.all_runs(job.id)
            tokens = store.tokens_by_agent(job.id)
            unlocked = orchestrator.unlocked_agents(job.id)
            current = active or job.current_agent

            st.markdown(
                f'<div class="lada-overall"><div class="lada-overall-top">'
                f"<b>{completed} / {config.TOTAL_AGENTS}</b>"
                f"<span>{fraction * 100:.0f}% complete</span></div>"
                f'<div class="lada-bar-outer"><div class="lada-bar-inner" '
                f'style="width:{fraction * 100:.1f}%"></div></div></div>',
                unsafe_allow_html=True)

            for spec in config.AGENTS:
                st.markdown(
                    _agent_row(spec, runs.get(spec.number),
                               unlocked=spec.number in unlocked,
                               is_current=spec.number == current,
                               tokens=tokens.get(spec.number, 0),
                               reviewed=store.latest_review(job.id, spec.number)),
                    unsafe_allow_html=True)

            totals = store.token_totals(job.id)
            st.markdown(
                f'<div class="lada-rail-head" style="margin-top:12px">'
                f"Tokens this job</div>"
                f'<div class="lada-overall"><div class="lada-overall-top">'
                f"<b>{totals['total']:,}</b><span>{totals['calls']} calls</span>"
                "</div></div>",
                unsafe_allow_html=True)

        st.divider()
        page = st.session_state.get("page", PAGE_LANDING)
        if job is not None:
            options = [PAGE_LANDING] + [f"agent{a.number}" for a in config.AGENTS]
            labels = {PAGE_LANDING: "Job setup"}
            labels.update({f"agent{a.number}": f"{a.icon}  {a.short}"
                           for a in config.AGENTS})
            if page in options:
                index = options.index(page)
            else:
                index = 0
            chosen = st.radio("Workspace", options, index=index,
                              format_func=lambda o: labels[o],
                              label_visibility="collapsed")
            if chosen != page:
                goto(chosen)

        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Jobs", width="stretch"):
                goto(PAGE_JOBS)
        with col_b:
            if st.button("Admin", width="stretch"):
                goto(PAGE_ADMIN)
        st.caption(f"LADA v{__import__('lada').__version__} · Career Shaper")


# --------------------------------------------------------------------------
# Shared widgets
# --------------------------------------------------------------------------
def key_banner(key_state: llm.KeyState) -> bool:
    """Show the key requirement message. Returns True when generation can run."""
    if key_state.present and key_state.active:
        st.markdown(
            theme.key_pill("active", key_state.masked)
            + f'&nbsp;&nbsp;<span style="font-size:.83rem;color:#5A6B85">'
              f"{html.escape(key_state.message)}</span>",
            unsafe_allow_html=True)
        return True

    st.markdown(theme.key_pill(key_state.pill_state, key_state.masked),
                unsafe_allow_html=True)
    st.warning(key_state.message + "  \nOpen **Admin** from the sidebar to add "
               "or re-validate the key.", icon=":material/key_off:")
    return False


def job_picker(label: str = "Open an existing job") -> None:
    jobs = store.list_jobs(40)
    if not jobs:
        st.info("No jobs yet. Capture the inputs below and start the orchestrator.")
        return
    labels = {
        job.id: (f"{job.asset_name[:52]}  ·  {job.entity_name or 'no entity'}  ·  "
                 f"{job.duration_hours:g}h  ·  {job.created_at[:16].replace('T', ' ')}")
        for job in jobs
    }
    options = list(labels)
    current = st.session_state.get("job_id")
    index = options.index(current) if current in options else 0
    chosen = st.selectbox(label, options, index=index,
                          format_func=lambda j: labels[j])
    left, right = st.columns([1, 4])
    with left:
        if st.button("Open job", type="primary", width="stretch"):
            st.session_state["job_id"] = chosen
            job = store.get_job(chosen)
            goto(f"agent{job.current_agent}" if job else PAGE_LANDING)
    with right:
        st.caption("Opening a job restores its full pipeline state - artifacts, "
                   "reviews, token history and the next unlocked agent.")

"""Per-agent workspace: run, inspect, download, review, rerun or advance."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd
import streamlit as st

from .. import config, llm, orchestrator, security, store, theme
from . import shell

_KIND_ICON = {
    "xlsx": ":material/table_view:",
    "pptx": ":material/slideshow:",
    "wav": ":material/graphic_eq:",
    "md": ":material/description:",
    "png": ":material/image:",
}

_MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "wav": "audio/wav",
    "md": "text/markdown",
    "png": "image/png",
}


# --------------------------------------------------------------------------
# Running
# --------------------------------------------------------------------------
def _run_agent(job: store.Job, agent_no: int, options: dict | None = None) -> None:
    spec = config.AGENT_BY_NUMBER[agent_no]
    bar = st.progress(0.0, text=f"Starting {spec.name}...")
    log_box = st.empty()

    def progress(fraction: float, message: str) -> None:
        bar.progress(min(1.0, max(0.0, fraction)),
                     text=f"{fraction * 100:.0f}%  ·  {message}"[:180])
        if message:
            log_box.caption(message)

    try:
        payload = orchestrator.run_agent(job, agent_no, progress_fn=progress,
                                         options=options)
    except orchestrator.GateError as exc:
        bar.empty()
        st.warning(str(exc), icon=":material/lock:")
        return
    except llm.QuotaError as exc:
        bar.empty()
        st.error("The API key ran out of quota part-way through this stage. "
                 "Anything already produced is saved - rerun the agent when "
                 f"quota resets.\n\n{security.redact(exc)[:400]}",
                 icon=":material/hourglass_disabled:")
        return
    except Exception as exc:
        bar.empty()
        st.error(f"{spec.name} failed: {security.redact(exc)[:600]}",
                 icon=":material/error:")
        with st.expander("Run log"):
            for event in store.events_for(job.id, 40)[-18:]:
                st.text(f"[{event['level']}] {event['message'][:300]}")
        return

    bar.progress(1.0, text=payload.get("summary", "Completed"))
    st.success(f"{spec.name} completed. {payload.get('summary', '')}",
               icon=":material/check_circle:")
    for notice in payload.get("notices") or []:
        st.info(notice, icon=":material/info:")
    st.rerun()


# --------------------------------------------------------------------------
# Artifacts
# --------------------------------------------------------------------------
def _zip_bytes(paths: list[Path], arc_prefix: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in paths:
            if path.exists():
                archive.write(path, arcname=f"{arc_prefix}{path.name}")
    return buffer.getvalue()


def _artifacts_panel(job: store.Job, agent_no: int) -> None:
    artifacts = store.artifacts_for(job.id, agent_no)
    if not artifacts:
        st.caption("No downloadable output yet.")
        return

    theme.section("", "Download the output",
                  "Every file this stage produced, ready to review offline.")
    for index, artifact in enumerate(artifacts):
        path = Path(artifact["path"])
        kind = artifact["kind"]
        col_a, col_b, col_c = st.columns([5, 2, 2])
        with col_a:
            st.markdown(f"{_KIND_ICON.get(kind, ':material/draft:')} "
                        f"**{artifact['label'] or path.name}**  \n"
                        f"<span style='font-size:.78rem;color:#5A6B85'>"
                        f"{path.name} · {artifact['size_bytes'] / 1024:,.0f} KB"
                        f"</span>", unsafe_allow_html=True)
        with col_b:
            st.caption(kind.upper())
        with col_c:
            st.download_button(
                "Download", path.read_bytes(), file_name=path.name,
                mime=_MIME.get(kind, "application/octet-stream"),
                key=f"dl-{agent_no}-{index}", use_container_width=True)

    if len(artifacts) > 1:
        paths = [Path(a["path"]) for a in artifacts]
        st.download_button(
            f"Download all {len(paths)} files as a ZIP",
            _zip_bytes(paths), type="primary",
            file_name=f"{job.asset_name[:40].strip()} - "
                      f"{config.AGENT_BY_NUMBER[agent_no].short}.zip",
            mime="application/zip", key=f"zipall-{agent_no}")


# --------------------------------------------------------------------------
# Payload summaries
# --------------------------------------------------------------------------
def _summary_agent1(payload: dict) -> None:
    guide = payload.get("guide_sheet") or {}
    modules = guide.get("modules") or []
    theme.metric_row([
        ("Modules", str(len(modules)), "one deck each", "purple"),
        ("Topics", str(sum(len(m.get("topics") or []) for m in modules)),
         "hierarchically numbered", ""),
        ("Coverage", f"{payload.get('total_minutes', 0) / 60:.1f} h",
         "total duration", "teal"),
        ("Objectives", str(len(guide.get("learning_objectives") or [])),
         "programme level", "dark"),
    ])
    st.write("")
    if modules:
        st.markdown("**Section 6 - detailed training coverage**")
        st.dataframe(pd.DataFrame([{
            "Sr.": m.get("sr_no"),
            "Module": m.get("module_name"),
            "Minutes": m.get("duration_minutes"),
            "Topics": len(m.get("topics") or []),
            "Sub-topics": sum(len(t.get("subtopics") or [])
                              for t in m.get("topics") or []),
            "Competency alignment": (m.get("competency_alignment") or "")[:90],
        } for m in modules]), use_container_width=True, hide_index=True)

    with st.expander("Sections 1-5, 7 and 8"):
        st.markdown("**Programme overview**")
        st.write(guide.get("program_overview", "-"))
        st.markdown("**Learning objectives**")
        for index, item in enumerate(guide.get("learning_objectives") or [], 1):
            st.markdown(f"{index}. {item}")
        cols = st.columns(2)
        with cols[0]:
            st.markdown("**Outcomes - understand**")
            for item in guide.get("outcomes_understand") or []:
                st.markdown(f"- {item}")
        with cols[1]:
            st.markdown("**Outcomes - do**")
            for item in guide.get("outcomes_do") or []:
                st.markdown(f"- {item}")
        st.markdown("**Pre-requisites (pre-qualifier blueprint)**")
        st.dataframe(pd.DataFrame(guide.get("prerequisites") or []),
                     use_container_width=True, hide_index=True)
        st.markdown("**Licences and subscriptions**")
        st.dataframe(pd.DataFrame(guide.get("licenses") or []),
                     use_container_width=True, hide_index=True)

    if payload.get("notes"):
        st.warning("Automatic corrections applied:\n\n"
                   + "\n".join(f"- {n}" for n in payload["notes"]))


def _summary_agent2(payload: dict) -> None:
    decks = payload.get("decks") or []
    theme.metric_row([
        ("Decks", str(len(decks)), "one per 60-minute module", "purple"),
        ("Slides", str(payload.get("slides_visible", 0)),
         f"cap {config.MAX_SLIDES_PER_DECK} per deck", ""),
        ("Image slots", str(payload.get("image_slots", 0)),
         "for Agent 3", "teal"),
        ("Activities", str(sum(d.get("activities", 0) for d in decks)),
         "MCQ + fill-in-the-blank", "dark"),
    ])
    st.write("")
    st.dataframe(pd.DataFrame([{
        "Deck": d.get("module_sr_no"),
        "Module": d.get("module_name"),
        "Visible slides": d.get("slides_visible"),
        "Total (incl. feedback)": d.get("slides_total"),
        "Minutes": d.get("duration_minutes"),
        "Image slots": len(d.get("image_slots") or []),
        "Activities": d.get("activities"),
    } for d in decks]), use_container_width=True, hide_index=True)

    for deck_info in decks:
        plan = deck_info.get("plan") or {}
        with st.expander(f"Deck {deck_info.get('module_sr_no')} - slide plan and "
                         "voice-over scripts"):
            st.dataframe(pd.DataFrame([{
                "Kind": s.get("kind"),
                "Title": s.get("title"),
                "Topic": s.get("topic_ref") or "-",
                "Voice-over (words)": len((s.get("voiceover") or "").split()),
                "Voice-over script": s.get("voiceover"),
            } for s in plan.get("slides") or []]),
                use_container_width=True, hide_index=True)


def _summary_agent3(payload: dict) -> None:
    theme.metric_row([
        ("Images placed", str(payload.get("images_total", 0)), "across all decks",
         "purple"),
        ("Model-generated", str(payload.get("ai_generated", 0)),
         "via the image model", "teal"),
        ("Composed on-brand", str(payload.get("fallback_generated", 0)),
         "rendered from the palette", "dark"),
        ("Failures", str(len(payload.get("failures") or [])), "unfilled slots", ""),
    ])
    st.write("")
    st.dataframe(pd.DataFrame([{
        "Deck": d.get("module_sr_no"),
        "File": d.get("filename"),
        "Images": d.get("images"),
        "Model-generated": d.get("ai_generated"),
        "On-brand": d.get("fallback_generated"),
    } for d in payload.get("decks") or []]),
        use_container_width=True, hide_index=True)

    galleries = [d for d in payload.get("decks") or [] if d.get("image_dir")]
    if galleries:
        chosen = st.selectbox(
            "Preview the imagery for a deck",
            galleries, format_func=lambda d: f"Deck {d.get('module_sr_no')}")
        images = sorted(Path(chosen["image_dir"]).glob("*.png"))
        if images:
            for row_start in range(0, min(len(images), 12), 4):
                cols = st.columns(4)
                for offset, image in enumerate(images[row_start:row_start + 4]):
                    with cols[offset]:
                        st.image(str(image), caption=image.stem,
                                 use_container_width=True)


def _summary_agent4(payload: dict) -> None:
    seconds = payload.get("total_seconds", 0)
    minutes, remainder = divmod(int(seconds), 60)
    theme.metric_row([
        ("Narrated slides", str(payload.get("narrated_slides", 0)),
         "with embedded audio", "purple"),
        ("Runtime", f"{minutes}m {remainder:02d}s", "total narration", "teal"),
        ("Voice", payload.get("voice", "-"),
         payload.get("pace_label", ""), ""),
        ("Autoplay", "On" if payload.get("autoplay") else "Off",
         "per-slide playback", "dark"),
    ])
    st.write("")
    for deck_info in payload.get("decks") or []:
        with st.expander(f"Deck {deck_info.get('module_sr_no')} - "
                         f"{deck_info.get('narrated_slides')} narrated slides "
                         f"({deck_info.get('total_seconds')}s)"):
            tracks = deck_info.get("tracks") or []
            if tracks:
                st.dataframe(pd.DataFrame([{
                    "Slide": t.get("slide_number"),
                    "Title": t.get("title"),
                    "Words": t.get("words"),
                    "Seconds": t.get("seconds"),
                } for t in tracks]), use_container_width=True, hide_index=True)
                first = Path(deck_info.get("audio_dir", "")) / tracks[0]["file"]
                if first.exists():
                    st.caption(f"Preview - slide {tracks[0]['slide_number']}")
                    st.audio(str(first))
                combined = deck_info.get("combined_track")
                if combined and Path(combined).exists():
                    st.caption("Continuous module narration")
                    st.audio(combined)
            else:
                st.caption("No narration was produced for this deck yet.")


def _summary_agent5(payload: dict) -> None:
    score = payload.get("overall_score")
    theme.metric_row([
        ("Overall score", f"{score} / 10" if score is not None else "not assessed",
         payload.get("overall_verdict", ""), "purple"),
        ("Decks audited", f"{payload.get('decks_assessed', 0)}"
                          f"/{len(payload.get('decks') or [])}",
         "content audit", "teal"),
        ("Mechanical faults", str(len(payload.get("mechanical_faults") or [])),
         "measured from the files", "dark"),
    ])
    st.write("")
    st.markdown("**Usability statement**")
    st.info(payload.get("usability_statement", "-"))

    if payload.get("mechanical_faults"):
        st.warning("Mechanical faults measured:\n\n"
                   + "\n".join(f"- {f}" for f in payload["mechanical_faults"]))

    for deck_info in payload.get("decks") or []:
        assessed = deck_info.get("assessed", True)
        header = (f"Deck {deck_info.get('module_sr_no')} - "
                  + (f"{deck_info.get('score')}/10 · " if assessed else "")
                  + str(deck_info.get("verdict")))
        with st.expander(header, expanded=assessed and len(payload["decks"]) <= 2):
            for entry in deck_info.get("scores") or []:
                value = float(entry.get("score") or 0)
                st.markdown(
                    f"**{entry.get('dimension')}** &nbsp; {value:g}/10 &nbsp; "
                    f"<span style='font-size:.78rem;color:#5A6B85'>"
                    f"{entry.get('verdict', '')}</span>", unsafe_allow_html=True)
                st.markdown(theme.score_bar(value), unsafe_allow_html=True)
                st.caption(entry.get("evidence", ""))
                if entry.get("risks"):
                    st.caption(f"Residual risk: {entry['risks']}")
            for label, items in (
                ("Strengths", deck_info.get("strengths")),
                ("Language defects", deck_info.get("language")),
                ("Factual concerns", deck_info.get("factual")),
                ("Activity findings", deck_info.get("activity")),
                ("Recommendations", deck_info.get("recommendations")),
            ):
                if items:
                    st.markdown(f"**{label}**")
                    for item in items:
                        st.markdown(f"- {item}")


_SUMMARIES = {
    1: _summary_agent1, 2: _summary_agent2, 3: _summary_agent3,
    4: _summary_agent4, 5: _summary_agent5,
}


# --------------------------------------------------------------------------
# Review capture
# --------------------------------------------------------------------------
def _review_panel(job: store.Job, agent_no: int) -> None:
    spec = config.AGENT_BY_NUMBER[agent_no]
    is_last = agent_no == config.TOTAL_AGENTS
    theme.section("", "Review and decision",
                  "Capture the reviewer's name and comments before this stage is "
                  "signed off. Nothing advances automatically.")

    history = store.reviews_for(job.id, agent_no)
    if history:
        latest = history[0]
        icon = ":material/task_alt:" if latest["decision"] == "proceed" \
            else ":material/replay:"
        (st.success if latest["decision"] == "proceed" else st.warning)(
            f"**{latest['reviewer_name']}** "
            f"{'approved' if latest['decision'] == 'proceed' else 'marked for rerun'}"
            f" on {latest['created_at'][:16].replace('T', ' ')}"
            + (f"  \n\n{latest['comments']}" if latest["comments"] else ""),
            icon=icon)

    with st.form(f"review-{agent_no}-{store.run_count(job.id, agent_no)}"):
        col_a, col_b = st.columns([2, 3])
        with col_a:
            reviewer = st.text_input("Reviewer name *",
                                     value=(history[0]["reviewer_name"]
                                            if history else ""),
                                     placeholder="Full name of the reviewer")
        with col_b:
            decision = st.radio(
                "Decision",
                ["proceed", "rerun"],
                format_func=lambda d: ("Approve" + ("" if is_last
                                                    else f" and unlock Agent "
                                                         f"{agent_no + 1}")
                                       if d == "proceed"
                                       else f"Send back - rerun Agent {agent_no}"),
                horizontal=True)
        comments = st.text_area(
            "Review comments",
            placeholder="What is good, what must change. On a rerun these "
                        "comments are given to the agent as explicit instructions.",
            height=110)
        submitted = st.form_submit_button("Save the review", type="primary")

    if submitted:
        if not reviewer.strip():
            st.error("The reviewer name is required.")
        elif decision == "rerun" and not comments.strip():
            st.error("Please say what needs to change - the comments are fed "
                     "back into the agent on a rerun.")
        else:
            store.add_review(job.id, agent_no, reviewer, comments, decision)
            store.log_event(job.id, agent_no, "info",
                            f"Review by {reviewer}: {decision}.")
            st.rerun()

    latest = store.latest_review(job.id, agent_no)
    st.write("")
    col_rerun, col_next, col_note = st.columns([1, 1, 3])
    with col_rerun:
        if st.button(f"Rerun {spec.short}", use_container_width=True,
                     key=f"rerun-{agent_no}"):
            st.session_state[f"run_now_{agent_no}"] = True
            st.rerun()
    with col_next:
        if not is_last:
            allowed, _ = orchestrator.gate_status(job.id, agent_no + 1)
            if st.button(f"Next: {config.AGENT_BY_NUMBER[agent_no + 1].short}",
                         type="primary", use_container_width=True,
                         disabled=not allowed, key=f"next-{agent_no}"):
                store.update_job(job.id, current_agent=agent_no + 1)
                shell.goto(f"agent{agent_no + 1}")
    with col_note:
        if latest and latest["decision"] == "rerun":
            st.caption("This stage is marked for rerun. The reviewer's comments "
                       "will be passed to the agent as instructions.")
        elif not latest:
            st.caption("Save a review to unlock the next agent.")
        elif is_last:
            st.caption("Final stage. Rerun with comments to refine the "
                       "validation, or download the report above.")


# --------------------------------------------------------------------------
# Agent-specific run options
# --------------------------------------------------------------------------
def _run_options(agent_no: int) -> dict:
    if agent_no != 4:
        return {}
    theme.card_open("Narration settings")
    col_a, col_b, col_c = st.columns(3)
    with col_a:
        voice_label = st.selectbox("Voice", list(config.VOICE_OPTIONS),
                                   help="Indian-English professional female "
                                        "narration voices.")
    with col_b:
        pace_label = st.selectbox("Narration pace", list(config.NARRATION_PACE),
                                  index=list(config.NARRATION_PACE)
                                  .index(config.DEFAULT_PACE),
                                  help="Applied at synthesis time - text to "
                                       "speech has no playback-rate control.")
    with col_c:
        autoplay = st.checkbox("Play automatically on each slide", value=True)
    theme.card_close()
    return {"voice_label": voice_label, "pace_label": pace_label,
            "autoplay": autoplay}


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------
def render(agent_no: int) -> None:
    job = shell.current_job()
    spec = config.AGENT_BY_NUMBER[agent_no]

    if job is None:
        theme.masthead(subtitle="No job is open.", palette=shell.palette())
        st.warning("Open or create a job first.", icon=":material/info:")
        if st.button("Go to job setup", type="primary"):
            shell.goto(shell.PAGE_LANDING)
        return

    theme.masthead(
        subtitle=f"Agent {spec.number} of {config.TOTAL_AGENTS} - {spec.name}",
        chips=(job.asset_name[:40], job.entity_name[:28] or "no entity",
               f"{job.duration_hours:g} hours"),
        palette=job.palette)

    theme.section(spec.icon, spec.name, spec.blurb)

    run = store.latest_run(job.id, agent_no)
    payload = store.agent_payload(job.id, agent_no)
    allowed, reason = orchestrator.gate_status(job.id, agent_no)
    key_state = llm.read_key_state()

    if not allowed:
        st.warning(reason, icon=":material/lock:")
        if agent_no > 1 and st.button(
                f"Go to Agent {agent_no - 1} "
                f"({config.AGENT_BY_NUMBER[agent_no - 1].short})"):
            shell.goto(f"agent{agent_no - 1}")
        return

    can_run = shell.key_banner(key_state)
    st.write("")

    options = _run_options(agent_no) if (can_run and not payload) or \
        st.session_state.get(f"run_now_{agent_no}") else {}

    # Trigger a run: explicit rerun, first-time autorun, or the run button.
    triggered = st.session_state.pop(f"run_now_{agent_no}", False)
    if st.session_state.get("autorun_agent") == agent_no and not run:
        st.session_state.pop("autorun_agent", None)
        triggered = True

    if not payload and not triggered:
        col_a, col_b = st.columns([1, 4])
        with col_a:
            if st.button(f"Run {spec.short}", type="primary",
                         use_container_width=True, disabled=not can_run):
                triggered = True
        with col_b:
            st.caption(f"Produces: {spec.produces}. "
                       + ("Runs against the reviewed output of the previous stage."
                          if agent_no > 1 else
                          "Uses the job inputs you captured."))
        if run and run.get("status") == "failed":
            st.error(f"The last attempt failed: {run.get('message', '')}",
                     icon=":material/error:")
            with st.expander("Error detail"):
                st.code((run.get("error") or "")[:4000])

    if triggered and can_run:
        _run_agent(job, agent_no, options or _run_options(agent_no))
        return

    if payload:
        st.markdown(f"**{payload.get('summary', 'Completed')}**")
        for notice in payload.get("notices") or []:
            st.info(notice, icon=":material/info:")
        st.write("")
        summary = _SUMMARIES.get(agent_no)
        if summary:
            summary(payload)
        st.write("")
        _artifacts_panel(job, agent_no)
        st.write("")
        _review_panel(job, agent_no)

    with st.expander("Run log for this stage"):
        events = [e for e in store.events_for(job.id, 200)
                  if e["agent_no"] == agent_no]
        if events:
            for event in events[-30:]:
                st.text(f"[{event['created_at'][11:19]}] [{event['level']}] "
                        f"{event['message'][:280]}")
        else:
            st.caption("No events recorded for this stage yet.")

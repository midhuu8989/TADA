"""Landing page: input capture, sample-input guidance and the token dashboard."""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from .. import config, extract, llm, orchestrator, store, theme
from . import shell


def _sample_inputs() -> None:
    st.markdown(
        '<div class="lada-card"><div class="lada-card-title">'
        "What this agent can process</div>", unsafe_allow_html=True)
    left, right = st.columns(2)
    for index, sample in enumerate(config.SAMPLE_INPUTS):
        target = left if index % 2 == 0 else right
        with target:
            st.markdown(
                f'<div class="lada-sample"><b>{html.escape(sample["title"])}</b>'
                f'<p>{html.escape(sample["example"])}</p></div>',
                unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def _token_dashboard(job: store.Job | None) -> None:
    theme.section("04", "Token dashboard",
                  "Consumption for this job, broken down by agent.")
    totals = store.token_totals(job.id if job else None)
    theme.metric_row([
        ("Total tokens", f"{totals['total']:,}", f"{totals['calls']} API calls", ""),
        ("Prompt", f"{totals['prompt']:,}", "input tokens", "purple"),
        ("Output", f"{totals['output']:,}", "generated tokens", "teal"),
        ("Reasoning", f"{totals['thoughts']:,}", "thinking tokens", "dark"),
    ])
    if not job:
        return

    by_agent = store.tokens_by_agent(job.id)
    runs = store.all_runs(job.id)
    rows = []
    for spec in config.AGENTS:
        run = runs.get(spec.number) or {}
        rows.append({
            "Agent": f"{spec.icon} {spec.short}",
            "Status": (run.get("status") or "pending").title(),
            "Progress": f"{float(run.get('progress') or 0) * 100:.0f}%",
            "Tokens": by_agent.get(spec.number, 0),
            "Produces": spec.produces,
            "Last message": (run.get("message") or "-")[:70],
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
                 column_config={
                     "Tokens": st.column_config.NumberColumn(format="%d"),
                 })

    if any(by_agent.values()):
        chart = pd.DataFrame(
            {"Agent": [f"{s.icon} {s.short}" for s in config.AGENTS],
             "Tokens": [by_agent.get(s.number, 0) for s in config.AGENTS]}
        ).set_index("Agent")
        st.bar_chart(chart, color=config.HCLTECH_PALETTE["primary_purple"],
                     height=220)


def _persist_uploads(job: store.Job, uploads) -> list[str]:
    notes: list[str] = []
    job.upload_dir.mkdir(parents=True, exist_ok=True)
    for upload in uploads or []:
        target = job.upload_dir / upload.name
        target.write_bytes(upload.getbuffer())
        text, note = extract.extract_text(target)
        store.add_upload(job.id, upload.name, target, text)
        notes.append(note)
    return notes


def render() -> None:
    job = shell.current_job()
    key_state = llm.read_key_state()
    palette = shell.palette()

    theme.masthead(
        subtitle="Multi-agent content development for training assets - "
                 "guide sheet, decks, imagery, narration and validation.",
        chips=("HCLTech brand system", "5 agents", "Coherent persistence",
               "Career Shaper"),
        palette=palette)

    can_run = shell.key_banner(key_state)
    st.write("")

    if job is not None:
        allowed, reason = orchestrator.gate_status(job.id, 1)
        theme.metric_row([
            ("Active job", job.asset_name[:34], job.id, "purple"),
            ("Entity", job.entity_name or "-", job.entity_type or "not specified", ""),
            ("Duration", f"{job.duration_hours:g} h",
             f"{max(1, round(job.duration_hours))} module(s) / deck(s)", "teal"),
            ("Stage", f"Agent {job.current_agent}",
             config.AGENT_BY_NUMBER[job.current_agent].short, "dark"),
        ])
        st.write("")

    tab_new, tab_open, tab_dash = st.tabs(
        ["Capture a new asset", "Open an existing job", "Token dashboard"])

    # ------------------------------------------------------------ new job
    with tab_new:
        _sample_inputs()

        theme.section("01", "Asset definition",
                      "Name the asset and tell the orchestrator who it is for.")
        col1, col2 = st.columns([3, 2])
        with col1:
            asset_name = st.text_input(
                "Name of the asset to be developed *",
                placeholder="Cloud-Native Application Engineering Foundation",
                help="Used as the job name, the workbook title and the deck titles.")
        with col2:
            duration = st.number_input(
                "Duration of the training (hours) *", min_value=0.5,
                max_value=40.0, value=4.0, step=0.5,
                help="Split into 60-minute modules; one deck per module.")

        col3, col4 = st.columns([3, 2])
        with col3:
            entity_name = st.text_input(
                "HEI / enterprise / entity name",
                placeholder="Sunrise Institute of Technology")
        with col4:
            entity_type = st.selectbox(
                "Entity type",
                ["HEI", "Enterprise", "Government / PSU", "Training partner",
                 "Not specified"], index=0)

        audience = st.text_input(
            "Target audience",
            placeholder="3rd-year B.Tech CSE students with Java and basic Linux "
                        "exposure")

        theme.section("02", "Content and coverage",
                      "Paste the brief and the curriculum. These two boxes drive "
                      "every downstream agent, so more detail means better output.")
        col5, col6 = st.columns(2)
        with col5:
            content_brief = st.text_area(
                "Content brief - subject, intent, audience *", height=260,
                placeholder=config.SAMPLE_CONTENT_BRIEF)
        with col6:
            coverage_brief = st.text_area(
                "Coverage - curriculum, topics and sub-topics", height=260,
                placeholder="Module 2 Containerisation\n"
                            "  2.1 Docker images and layer caching\n"
                            "  2.2 Multi-stage builds\n"
                            "  2.3 Registries and tagging strategy\n"
                            "Module 3 Orchestration\n"
                            "  3.1 Pods, deployments, services\n"
                            "  3.2 Config and secrets")
        if st.checkbox("Use the worked sample brief instead"):
            content_brief = config.SAMPLE_CONTENT_BRIEF
            st.caption("The sample cloud-native brief will be used.")

        theme.section("03", "Documentation and brand palette",
                      "Optional: upload the programme guide sheet and override the "
                      "default HCLTech palette.")
        col7, col8 = st.columns([3, 2])
        with col7:
            uploads = st.file_uploader(
                "Upload course / programme documentation or guide sheet",
                type=[s.lstrip(".") for s in extract.SUPPORTED],
                accept_multiple_files=True,
                help="Text is extracted and fed to Agent 1 verbatim. "
                     f"Supported: {', '.join(extract.SUPPORTED)}")
        with col8:
            palette_override = st.text_area(
                "Colour palette guideline (optional)", height=150,
                placeholder="Primary purple: #5F1EBE\nPrimary blue: #3C91FF\n"
                            "Secondary teal: #00A4A6\nDark neutral: #00112B",
                help="Leave empty to use the default HCLTech and Career Shaper "
                     "palette. Accepts 'name: #hex' lines or a bare list of hex "
                     "codes.")

        if palette_override.strip():
            overrides, notes = config.parse_palette_override(palette_override)
            if overrides:
                swatches = "".join(
                    f'<span style="display:inline-block;width:64px;height:26px;'
                    f"background:{value};border-radius:6px;margin-right:6px;"
                    f'border:1px solid #dbe6f7" title="{key}: {value}"></span>'
                    for key, value in overrides.items())
                st.markdown(f"Palette preview: {swatches}", unsafe_allow_html=True)
                for note in notes:
                    st.caption(note)
            else:
                st.warning("No hex colours were recognised - the default HCLTech "
                           "palette will be used.")

        st.write("")
        start_col, note_col = st.columns([1, 3])
        with start_col:
            start = st.button("Start the orchestrator", type="primary",
                              width="stretch", disabled=not can_run)
        with note_col:
            if can_run:
                st.caption("Creates the job, then runs Agent 1. Every stage stops "
                           "for your review before the next one unlocks.")
            else:
                st.caption("Add a valid Generative AI API key in Admin to enable "
                           "the orchestrator.")

        if start:
            problems = []
            if not asset_name.strip():
                problems.append("the asset name")
            if not content_brief.strip():
                problems.append("the content brief")
            if problems:
                st.error("Please provide " + " and ".join(problems) + ".")
            else:
                new_job = store.create_job(
                    asset_name, entity_name=entity_name,
                    entity_type=("" if entity_type == "Not specified"
                                 else entity_type),
                    audience=audience, duration_hours=float(duration),
                    content_brief=content_brief, coverage_brief=coverage_brief,
                    palette_override=palette_override,
                    key_fingerprint=key_state.fingerprint)
                notes = _persist_uploads(new_job, uploads)
                for note in notes:
                    store.log_event(new_job.id, None, "info", note)
                if palette_override.strip():
                    store.set_setting(store.PALETTE_SETTING, palette_override)
                st.session_state["job_id"] = new_job.id
                st.session_state["autorun_agent"] = 1
                shell.goto("agent1")

    # --------------------------------------------------------- open a job
    with tab_open:
        theme.section("", "Saved jobs",
                      "Coherent persistence: every job restores its artifacts, "
                      "reviews and token history exactly as it was left.")
        shell.job_picker()

        if job is not None:
            with st.expander("Job inputs and uploaded documentation", expanded=False):
                st.markdown(f"**Asset:** {job.asset_name}")
                st.markdown(f"**Entity:** {job.entity_name or '-'} "
                            f"({job.entity_type or 'not specified'})")
                st.markdown(f"**Audience:** {job.audience or '-'}")
                st.markdown(f"**Duration:** {job.duration_hours:g} hours")
                st.text_area("Content brief", job.content_brief, height=140,
                             disabled=True)
                if job.coverage_brief:
                    st.text_area("Coverage", job.coverage_brief, height=120,
                                 disabled=True)
                uploads_saved = store.uploads_for(job.id)
                if uploads_saved:
                    st.dataframe(
                        pd.DataFrame([{
                            "File": u["filename"],
                            "Size (KB)": round(u["size_bytes"] / 1024, 1),
                            "Extracted characters": len(u["extracted_text"] or ""),
                        } for u in uploads_saved]),
                        width="stretch", hide_index=True)
                else:
                    st.caption("No documentation was uploaded for this job.")

    # ------------------------------------------------------------- tokens
    with tab_dash:
        _token_dashboard(job)
        if job is not None:
            with st.expander("Run log", expanded=False):
                events = store.events_for(job.id, 200)
                if events:
                    st.dataframe(
                        pd.DataFrame([{
                            "When": e["created_at"][11:19],
                            "Agent": e["agent_no"] or "-",
                            "Level": e["level"],
                            "Message": e["message"],
                        } for e in reversed(events)]),
                        width="stretch", hide_index=True, height=320)
                else:
                    st.caption("No events recorded yet.")

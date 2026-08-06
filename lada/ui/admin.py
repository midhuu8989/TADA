"""Admin module: password gate, API-key lifecycle and the token-usage log."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import config, llm, security, store, theme
from . import shell


def _gate() -> bool:
    if st.session_state.get("admin_unlocked"):
        return True

    theme.masthead(subtitle="Admin module - restricted access.",
                   chips=("Password protected",), palette=shell.palette())
    st.write("")
    left, right = st.columns([2, 3])
    with left:
        theme.card_open("Administrator sign-in")
        with st.form("admin_login", clear_on_submit=True):
            password = st.text_input("Admin password", type="password")
            submitted = st.form_submit_button("Unlock", type="primary",
                                              width="stretch")
        theme.card_close()
        if submitted:
            if security.verify_admin_password(password):
                st.session_state["admin_unlocked"] = True
                store.log_event(None, None, "info", "Admin module unlocked.")
                st.rerun()
            else:
                st.error("Incorrect password.")
                store.log_event(None, None, "warning",
                                "Failed admin sign-in attempt.")
    with right:
        theme.note(
            "The admin module holds the shared Generative AI API key and the "
            "token-usage ledger. The password is compared as a PBKDF2-SHA256 "
            "hash, and the key itself is encrypted at rest with a machine-local "
            "Fernet key - it is never stored or displayed in plaintext."
        )
    return False


def _key_panel() -> None:
    key_state = llm.read_key_state()
    theme.section("01", "Generative AI API key",
                  "One key, shared by every agent and every job. Stored "
                  "encrypted; never displayed in full.")

    left, right = st.columns([3, 2])
    with left:
        theme.card_open("Key status")
        st.markdown(theme.key_pill(key_state.pill_state, key_state.masked),
                    unsafe_allow_html=True)
        st.write("")
        if key_state.present and key_state.active:
            st.success(key_state.message, icon=":material/verified:")
        elif key_state.present:
            st.warning(key_state.message, icon=":material/error:")
        else:
            st.error(key_state.message, icon=":material/key_off:")

        details = {
            "Fingerprint": key_state.fingerprint,
            "Last validated": (key_state.checked_at or "never")[:19].replace("T", " "),
            "Validated against": key_state.model or "-",
        }
        for label, value in details.items():
            st.markdown(f"<span style='font-size:.82rem;color:#5A6B85'>{label}:"
                        f"</span> <b style='font-size:.82rem'>{value}</b>",
                        unsafe_allow_html=True)
        theme.card_close()

        with st.form("api_key_form"):
            candidate = st.text_input(
                "Paste the Generative AI API key", type="password",
                placeholder="AIza... or AQ....",
                help="Validated with a live call before it is saved. Nothing is "
                     "stored unless the key works.")
            col_a, col_b = st.columns(2)
            with col_a:
                save = st.form_submit_button("Validate and save", type="primary",
                                             width="stretch")
            with col_b:
                revalidate = st.form_submit_button("Re-validate stored key",
                                                   width="stretch")

        if save:
            with st.spinner("Testing the key against the Gemini API..."):
                ok, message, _ = llm.validate_and_store_key(candidate)
            if ok:
                st.success(f"Key is active. {message}")
                store.log_event(None, None, "success",
                                "API key validated and stored (encrypted).")
                st.rerun()
            else:
                st.error(message)
                store.log_event(None, None, "warning",
                                f"API key rejected: {message[:180]}")

        if revalidate:
            with st.spinner("Re-validating the stored key..."):
                ok, message, _ = llm.revalidate_stored_key()
            (st.success if ok else st.error)(message)
            if ok:
                st.rerun()

        if key_state.present:
            with st.expander("Remove the stored key"):
                st.caption("Clears the encrypted key and its status. Jobs and "
                           "artifacts are untouched; agents cannot run until a "
                           "new key is added.")
                if st.button("Remove key", type="secondary"):
                    store.clear_api_key()
                    store.log_event(None, None, "warning",
                                    "API key removed from the store.")
                    st.rerun()

    with right:
        theme.card_open("How the key is protected")
        st.markdown(
            "- Encrypted at rest with Fernet; the keyring file is owner-only.\n"
            "- Held in the server-side session only, never in a URL or cookie.\n"
            "- Masked everywhere in the interface - only the first and last four "
            "characters are ever shown.\n"
            "- Scrubbed from every error message and log line before it is "
            "written, so an SDK error cannot leak it.\n"
            "- Validated with a minimal call before being saved, so an unusable "
            "key never reaches a job."
        )
        theme.card_close()

        if st.session_state.get("_key_from_env"):
            theme.note(
                f"This key was adopted from the <code>"
                f"{st.session_state['_key_from_env']}</code> environment "
                "variable on startup and re-saved encrypted.")

        if key_state.present and st.button("Show models available to this key"):
            with st.spinner("Querying the API..."):
                try:
                    client = llm.get_client()
                    available = client.available_models()
                except llm.LLMError as exc:
                    st.error(str(exc))
                    available = {}
            for modality in ("text", "image", "tts"):
                names = available.get(modality) or []
                st.markdown(f"**{modality}** ({len(names)})")
                st.caption(", ".join(names[:14]) or "none visible")


def _token_log() -> None:
    theme.section("02", "Token usage log",
                  "Every billed call made with this API key, in order.")

    jobs = store.list_jobs(60)
    options = ["(all jobs)"] + [j.id for j in jobs]
    labels = {"(all jobs)": "All jobs"}
    labels.update({j.id: f"{j.asset_name[:44]} · {j.created_at[:10]}" for j in jobs})
    chosen = st.selectbox("Filter", options, format_func=lambda o: labels[o])
    job_filter = None if chosen == "(all jobs)" else chosen

    rows = store.token_rows(job_filter)
    totals = store.token_totals(job_filter)
    theme.metric_row([
        ("Total tokens", f"{totals['total']:,}", "against this key", "purple"),
        ("Calls", f"{totals['calls']:,}", "billed requests", ""),
        ("Prompt", f"{totals['prompt']:,}", "input", "teal"),
        ("Output + reasoning", f"{totals['output'] + totals['thoughts']:,}",
         "generated", "dark"),
    ])
    st.write("")

    if not rows:
        st.info("No token usage recorded yet.")
        return

    table = pd.DataFrame([{
        "Sr. No": index,
        "Job Name": row["job_name"],
        "Agent Name": row["agent_name"],
        "Token used by Agent": row["total_tokens"],
        "Model": row["model"] or "-",
        "Call": row["call_kind"] or "-",
        "Prompt": row["prompt_tokens"],
        "Output": row["output_tokens"],
        "Reasoning": row["thought_tokens"],
        "When": (row["created_at"] or "")[:19].replace("T", " "),
    } for index, row in enumerate(rows, 1)])

    st.dataframe(
        table, width="stretch", hide_index=True, height=420,
        column_config={
            "Sr. No": st.column_config.NumberColumn(width="small"),
            "Token used by Agent": st.column_config.NumberColumn(format="%d"),
            "Prompt": st.column_config.NumberColumn(format="%d", width="small"),
            "Output": st.column_config.NumberColumn(format="%d", width="small"),
            "Reasoning": st.column_config.NumberColumn(format="%d", width="small"),
        })

    st.download_button(
        "Download the log as CSV",
        table.to_csv(index=False).encode("utf-8"),
        file_name="lada-token-usage.csv", mime="text/csv")

    by_agent = (table.groupby("Agent Name")["Token used by Agent"]
                .sum().sort_values(ascending=False))
    if len(by_agent) > 1:
        st.markdown("**Consumption by agent**")
        st.bar_chart(by_agent, color=config.HCLTECH_PALETTE["primary_blue"],
                     height=240)


def _job_admin() -> None:
    theme.section("03", "Jobs and stored data",
                  "Housekeeping for saved jobs and their artifacts.")
    jobs = store.list_jobs(80)
    if not jobs:
        st.info("No jobs have been created yet.")
        return

    rows = []
    for job in jobs:
        runs = store.all_runs(job.id)
        completed = sum(1 for r in runs.values() if r.get("status") == "completed")
        rows.append({
            "Job": job.asset_name[:46],
            "Entity": job.entity_name or "-",
            "Hours": job.duration_hours,
            "Agents done": f"{completed}/{config.TOTAL_AGENTS}",
            "Status": job.status,
            "Artifacts": len(store.artifacts_for(job.id)),
            "Tokens": store.token_totals(job.id)["total"],
            "Created": job.created_at[:16].replace("T", " "),
            "Id": job.id,
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    with st.expander("Delete a job and its artifacts"):
        labels = {j.id: f"{j.asset_name[:44]} · {j.created_at[:16]}" for j in jobs}
        target = st.selectbox("Job to delete", list(labels),
                              format_func=lambda j: labels[j])
        st.caption("Removes the database records and the job's artifact folder. "
                   "This cannot be undone.")
        confirm = st.text_input("Type DELETE to confirm")
        if st.button("Delete job", type="secondary",
                     disabled=confirm.strip().upper() != "DELETE"):
            store.delete_job(target)
            if st.session_state.get("job_id") == target:
                st.session_state["job_id"] = None
            store.log_event(None, None, "warning", f"Job {target} deleted.")
            st.success("Job deleted.")
            st.rerun()


def render() -> None:
    if not _gate():
        return

    theme.masthead(
        subtitle="Admin module - API key, token ledger and job administration.",
        chips=("Encrypted key store", "Token ledger", "Job administration"),
        palette=shell.palette())

    top_left, top_right = st.columns([4, 1])
    with top_right:
        if st.button("Lock admin", width="stretch"):
            st.session_state["admin_unlocked"] = False
            st.rerun()
    st.write("")

    tab_key, tab_tokens, tab_jobs = st.tabs(
        ["API key", "Token usage log", "Jobs and data"])
    with tab_key:
        _key_panel()
    with tab_tokens:
        _token_log()
    with tab_jobs:
        _job_admin()

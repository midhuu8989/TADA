"""LADA - Learning Asset Development Agent.

Streamlit entry point. Run with:

    streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from lada import config, theme
from lada.ui import admin, agent_pages, landing, shell


def _page_config() -> None:
    st.set_page_config(
        page_title="Learning Asset Development Agent",
        page_icon=str(config.LOGO_MARK_PATH) if config.LOGO_MARK_PATH.exists()
        else ":material/school:",
        layout="wide",
        initial_sidebar_state="expanded",
    )


def _jobs_page() -> None:
    theme.masthead(
        subtitle="Saved jobs - every pipeline restores exactly as it was left.",
        chips=("Coherent persistence",), palette=shell.palette())
    st.write("")
    theme.section("", "Open a job", "Artifacts, reviews and token history are "
                                    "restored with the job.")
    shell.job_picker()


def main() -> None:
    _page_config()
    shell.bootstrap()
    theme.inject_css(shell.palette())
    shell.sidebar()

    page = st.session_state.get("page", shell.PAGE_LANDING)
    if page == shell.PAGE_ADMIN:
        admin.render()
    elif page == shell.PAGE_JOBS:
        _jobs_page()
    elif page.startswith("agent"):
        try:
            number = int(page.removeprefix("agent"))
        except ValueError:
            number = 1
        if number not in config.AGENT_BY_NUMBER:
            number = 1
        agent_pages.render(number)
    else:
        landing.render()


if __name__ == "__main__":
    main()

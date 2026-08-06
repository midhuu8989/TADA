# LADA — Learning Asset Development Agent

A Streamlit multi-agent application that turns a training brief into a complete,
brand-formatted learning asset package: a programme guide sheet, presentation
decks, generated imagery, narration and a validation report.

Five agents run as a gated pipeline. Each stage stops for a human review — with
the reviewer's name and comments captured — before the next stage unlocks, and a
rerun feeds those comments back into the model so the revision actually acts on
the feedback.

## Pipeline

| # | Agent | Produces |
|---|-------|----------|
| 01 | Guide-Sheet Generator | 8-section programme guide sheet as a brand-formatted Excel workbook |
| 02 | PowerPoint Presentation | 60-minute decks of up to 35 branded slides, each with a voice-over script in the presenter notes |
| 03 | Image Generator | Parses each deck and fills every image placeholder with on-brand artwork |
| 04 | Audio Enabling | Narrates every slide from its presenter-notes script in an Indian professional female voice |
| 05 | Deck Validator | Scores authenticity, originality, correctness, imagery, activities and feasibility |

## Quick start

```bash
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501.

Provide a Generative AI API key in one of two ways:

- **Admin module** (recommended) — open **Admin** in the sidebar, password
  `EdTech@123`, and paste the key. It is validated with a live test call and
  stored encrypted at rest.
- **`.env` file** — copy `.env.example` to `.env` and set `GEMINI_API_KEY`.

The key is held as a single global credential shared across all agents, so it is
entered once rather than per run.

## Deploying to Streamlit Community Cloud

1. Point a new app at this repository, branch `master`, main file `app.py`.
2. Under **Advanced settings**, choose **Python 3.13** — 3.14 is not offered by
   Community Cloud, and the pinned dependencies resolve cleanly on 3.13.
3. Paste the API key into **Secrets** (see `.streamlit/secrets.toml.example`):

   ```toml
   GEMINI_API_KEY = "your-key"
   ```

   The key is adopted at startup and re-persisted encrypted; it is never written
   to disk in plaintext by LADA.

Two things behave differently on Community Cloud. The container filesystem is
ephemeral, so `data/` — the SQLite database and all generated job artifacts — is
cleared whenever the app reboots or sleeps; jobs persist within a session but not
across restarts, and a key set via Secrets is re-adopted automatically while one
entered in the Admin module is not. The free tier's memory ceiling is also modest
relative to composing a 35-slide deck with generated imagery, so expect the
heavier agents to run slower than they do locally.

## Architecture

```
app.py                  Streamlit entry point and page routing
lada/
  config.py             Brand palette, model routing, agent registry, paths
  orchestrator.py       Pipeline gates, context hand-off, progress reporting
  store.py              SQLite persistence — jobs, runs, reviews, artifacts, tokens
  security.py           At-rest key encryption, admin auth, log redaction
  llm.py                Gemini client with token accounting and model fallbacks
  schemas.py            Validated structures for every agent hand-off
  deck.py               PowerPoint composition on the corporate template
  excelfmt.py           Branded Excel section formatting
  graphics.py           Palette-driven logo, bar and placeholder generation
  audio.py              WAV assembly and slide-embedded narration
  extract.py            Text extraction from uploaded documentation
  agents/               The five agent implementations
  ui/                   Landing page, agent pages, admin module, shell
```

### Coherent persistence

Every job's state lives in SQLite (`data/lada.db`) and on disk under
`data/jobs/<job-id>/`. Reopening a job restores its artifacts, review history,
token ledger and the next unlocked agent exactly as they were left — a browser
refresh, a restart and the sidebar all observe the same state.

### Security

- The API key is never written to disk in plaintext and never rendered beyond a
  masked tail. It is encrypted with Fernet under a keyring in `data/.keyring`.
- The admin password is compared as a PBKDF2-HMAC-SHA256 digest, so no plaintext
  password sits in source.
- Every exception surfaced to the UI or the run log passes through a redactor, so
  a key echoed back by an SDK error cannot leak into logs or artifacts.

### Model routing

Each modality carries an ordered fallback list, because Gemini deprecates models
per-key and free tiers carry no image or TTS quota. When a preferred model is
unavailable the client walks down the list rather than failing the run; Agent 3
falls back to composing placeholder artwork locally, so the pipeline always
completes.

## Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `GEMINI_API_KEY` | — | Generative AI key, if not set via the Admin module |
| `LADA_ADMIN_PASSWORD` | `EdTech@123` | Overrides the admin module password |
| `LADA_OFFLINE` | `0` | Skips live model calls |

Brand colours default to the HCLTech palette (`#5F1EBE`, `#3C91FF`, `#00A4A6`,
`#00112B`) and can be overridden per job by pasting a palette guideline on the
landing page.

## Requirements

Python 3.11+. `assets/ppt_template.pptx` is the corporate template the decks are
built on; `lada/deck.py` derives a slimmed ~2 MB working copy from it on first
run and caches the result.

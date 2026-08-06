"""Agent 5 - Deck Validator Agent.

Validates the finished asset and issues a scored verdict per deck plus an
overall one. Two kinds of check run and are reported side by side:

**Deterministic checks** computed from the files themselves - slide counts
against the 35-slide cap, voice-over word budgets, presence of imagery and
narration, MCQ shape (five options, exactly one key), fill-in-the-blank
integrity, coverage of the guide sheet's topics, and whether the answer key
cites slides that actually exist. These are facts rather than opinions, so they
anchor the score.

**Model judgement** over the deck's real text for what only reading can settle:
authenticity, originality and plagiarism risk, factual correctness, image
relevance, activity accuracy, instructional feasibility and brand compliance.

The report is written as a branded Excel workbook plus a Markdown summary.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import config, deck, excelfmt, graphics, llm, schemas, store
from ..orchestrator import AgentContext

SYSTEM = (
    "You are a meticulous learning-content quality auditor. You judge training "
    "material against the brief it was built from, you cite the slide you are "
    "talking about, and you never invent defects. You are candid about weakness "
    "but you do not manufacture concerns to look thorough. If the content is "
    "sound, you say so and explain why it is fit for learner use."
)

DIMENSIONS = (
    "Authenticity",
    "Originality / plagiarism risk",
    "Content correctness",
    "Image relevance and quality",
    "Activity accuracy",
    "Instructional feasibility",
    "Brand and format compliance",
)


# --------------------------------------------------------------------------
# Deterministic checks
# --------------------------------------------------------------------------
def _mechanical_checks(deck_info: dict, presentation, guide: dict,
                       plan: dict) -> dict:
    """Facts computed from the artefact, independent of any model opinion."""
    visible = [s for s in presentation.slides if not deck.is_hidden(s)]
    hidden = [s for s in presentation.slides if deck.is_hidden(s)]

    long_scripts: list[str] = []
    missing_scripts: list[int] = []
    narrated = 0
    with_images = 0
    total_words = 0

    for index, slide in enumerate(presentation.slides, 1):
        if deck.is_hidden(slide):
            continue
        notes = deck.slide_notes(slide)
        words = len(notes.split())
        total_words += words
        if words == 0:
            missing_scripts.append(index)
        elif words > config.MAX_VOICEOVER_WORDS:
            long_scripts.append(f"slide {index} ({words} words)")
        if any((s.name or "").startswith("LADA_AUDIO::") for s in slide.shapes):
            narrated += 1
        if any((s.name or "").startswith("LADA_IMAGE") for s in slide.shapes):
            with_images += 1

    # Activity integrity, checked against the plan Agent 2 recorded.
    mcq_total = 0
    mcq_bad = 0
    blank_total = 0
    blank_bad = 0
    activity_faults: list[str] = []
    for plan_slide in plan.get("slides") or []:
        label = plan_slide.get("title") or "activity"
        for mcq in plan_slide.get("mcqs") or []:
            mcq_total += 1
            options = [o for o in (mcq.get("options") or [])]
            if len(options) != 5:
                mcq_bad += 1
                activity_faults.append(
                    f"'{label}': an MCQ has {len(options)} options, the "
                    "specification requires 5")
            if len({o.strip().lower() for o in options}) != len(options):
                mcq_bad += 1
                activity_faults.append(f"'{label}': an MCQ has duplicate options")
            index = mcq.get("correct_index")
            if not isinstance(index, int) or not 0 <= index < len(options):
                mcq_bad += 1
                activity_faults.append(
                    f"'{label}': an MCQ has no valid single correct option")
        for blank in plan_slide.get("blanks") or []:
            blank_total += 1
            distractors = [d for d in (blank.get("distractors") or [])]
            if "___" not in (blank.get("sentence") or ""):
                blank_bad += 1
                activity_faults.append(
                    f"'{label}': a fill-in-the-blank has no visible gap")
            if len(distractors) != 3:
                blank_bad += 1
                activity_faults.append(
                    f"'{label}': a fill-in-the-blank has {len(distractors)} "
                    "distractors, the specification requires 3")
            answer = (blank.get("answer") or "").strip().lower()
            if answer and answer in [d.strip().lower() for d in distractors]:
                blank_bad += 1
                activity_faults.append(
                    f"'{label}': a distractor duplicates the answer")

    # Do the answer-key citations point at slides that exist?
    dangling: list[str] = []
    citation = re.compile(r"see slide (\d+)")
    for slide in visible:
        for match in citation.finditer(deck.slide_text(slide)):
            number = int(match.group(1))
            if number < 1 or number > len(visible):
                dangling.append(
                    f"cites slide {number} but the deck has {len(visible)}")

    # Topic coverage: every guide-sheet topic should be evidenced in the text.
    module = next((m for m in guide.get("modules") or []
                   if m.get("sr_no") == deck_info.get("module_sr_no")), {})
    deck_blob = " ".join(deck.slide_text(s) for s in visible).lower()
    uncovered: list[str] = []
    for topic in module.get("topics") or []:
        keywords = re.findall(r"[a-z]{5,}", (topic.get("title") or "").lower())[:4]
        if keywords and not any(k in deck_blob for k in keywords):
            uncovered.append(str(topic.get("number", "?")))

    return {
        "slides_visible": len(visible),
        "slides_hidden": len(hidden),
        "over_slide_cap": len(visible) > config.MAX_SLIDES_PER_DECK,
        "slides_with_images": with_images,
        "slides_narrated": narrated,
        "voiceover_words_total": total_words,
        "scripts_over_budget": long_scripts,
        "scripts_missing": missing_scripts,
        "mcq_total": mcq_total,
        "mcq_faults": mcq_bad,
        "blank_total": blank_total,
        "blank_faults": blank_bad,
        "activity_faults": activity_faults,
        "dangling_citations": dangling,
        "uncovered_topics": uncovered,
    }


def _mechanical_penalty(checks: dict) -> tuple[float, list[str]]:
    """Convert hard findings into a score deduction with stated reasons."""
    penalty = 0.0
    reasons: list[str] = []
    if checks["over_slide_cap"]:
        penalty += 1.0
        reasons.append(f"{checks['slides_visible']} visible slides exceeds the "
                       f"{config.MAX_SLIDES_PER_DECK}-slide cap")
    if checks["scripts_missing"]:
        penalty += 0.8
        reasons.append(f"{len(checks['scripts_missing'])} slide(s) carry no "
                       "voice-over script")
    if checks["scripts_over_budget"]:
        penalty += 0.5
        reasons.append(f"{len(checks['scripts_over_budget'])} script(s) exceed the "
                       f"{config.MAX_VOICEOVER_WORDS}-word budget: "
                       + ", ".join(checks["scripts_over_budget"][:4]))
    if checks["mcq_faults"] or checks["blank_faults"]:
        penalty += 1.2
        reasons.append(f"{checks['mcq_faults'] + checks['blank_faults']} activity "
                       "integrity fault(s)")
    if checks["dangling_citations"]:
        penalty += 0.6
        reasons.append(f"{len(checks['dangling_citations'])} answer-key citation(s) "
                       "point outside the deck")
    if checks["uncovered_topics"]:
        penalty += 0.7
        reasons.append("guide-sheet topic(s) not evidenced in the deck: "
                       + ", ".join(checks["uncovered_topics"]))
    if checks["slides_with_images"] == 0:
        penalty += 0.5
        reasons.append("no slide carries imagery")
    return min(penalty, 4.0), reasons


# --------------------------------------------------------------------------
# Model judgement
# --------------------------------------------------------------------------
def _judge_deck(ctx: AgentContext, deck_info: dict, presentation, guide: dict,
                checks: dict) -> dict:
    digest = deck.deck_digest(presentation, include_notes=True, max_chars=46_000)
    module = next((m for m in guide.get("modules") or []
                   if m.get("sr_no") == deck_info.get("module_sr_no")), {})
    topics = "\n".join(
        f"  {t.get('number')} {t.get('title')} ({t.get('duration_minutes')} min)"
        for t in module.get("topics") or [])

    prompt = f"""Audit this training deck against the brief it was built from.

PROGRAMME: {guide.get('program_title')}
ENTITY: {ctx.job.entity_name or 'Not specified'}
AUDIENCE: {guide.get('target_audience') or 'Not specified'}

MODULE {module.get('sr_no')}: {module.get('module_name')}
MODULE OBJECTIVE: {module.get('module_objective')}
TOPICS THAT MUST BE COVERED:
{topics or '  (none recorded)'}

MEASURED FACTS (already verified mechanically - treat these as ground truth):
  visible slides: {checks['slides_visible']} (cap {config.MAX_SLIDES_PER_DECK})
  slides carrying imagery: {checks['slides_with_images']}
  slides carrying narration audio: {checks['slides_narrated']}
  MCQ items: {checks['mcq_total']} with {checks['mcq_faults']} integrity fault(s)
  fill-in-the-blank items: {checks['blank_total']} with {checks['blank_faults']} fault(s)
  scripts over the {config.MAX_VOICEOVER_WORDS}-word budget: {len(checks['scripts_over_budget'])}

=== DECK CONTENT ===
{digest}

=== WHAT TO ASSESS ===
Score each of these dimensions out of 10, one entry per dimension, in this order:
{chr(10).join('  - ' + d for d in DIMENSIONS)}

For each: the score, a verdict, the evidence from the deck (cite slide numbers or
titles), and any residual risk.

Then provide:
  - overall_score and overall_verdict.
  - usability_statement: 3-5 sentences on why this content can or cannot be
    considered fit for learner use. This is the statement a sponsor reads.
  - originality_notes: assess plagiarism risk specifically. Is the phrasing
    original rather than lifted from a well-known source? Are the worked examples
    genuinely fictitious? Flag any passage that reads like copied documentation.
  - spelling_and_language: actual spelling, grammar or ambiguity defects, each
    naming its slide. Empty list if clean - do not invent any.
  - factual_concerns: statements that are wrong, outdated or unverifiable, each
    naming its slide. Empty list if none.
  - activity_findings: problems with the MCQ or fill-in-the-blank items - wrong
    keys, more than one defensible answer, giveaway distractors.
  - recommendations: prioritised, actionable fixes.
  - strengths: what is genuinely good.

Be accurate rather than harsh. A deck that covers its topics, is internally
consistent and is technically correct should score well.{ctx.review_directive()}"""

    return ctx.client.generate_json(
        prompt, schemas.VALIDATION_SCHEMA, system=SYSTEM, deep=True,
        temperature=0.35, thinking="high",
        call_kind=f"validate-m{deck_info.get('module_sr_no')}")


def _blend(model_score: float, penalty: float) -> float:
    return round(max(0.0, min(10.0, float(model_score or 0) - penalty)), 1)


def _verdict_for(score: float, hard_faults: bool) -> str:
    if hard_faults or score < 6.0:
        return "Needs rework"
    if score < 8.0:
        return "Tested OK with observations"
    return "Tested OK"


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def _write_report(ctx: AgentContext, results: list[dict], overall: dict,
                  guide: dict) -> Path:
    style = excelfmt.SheetStyle(ctx.palette)
    workbook = excelfmt.new_workbook()
    stamp = store.now_iso()[:16].replace("T", " ")

    summary_sheet = excelfmt.add_sheet(
        workbook, "Validation Summary", style, content_cols=6,
        col_widths=[9, 32, 12, 24, 20, 34])
    summary_sheet.banner(
        f"VALIDATION REPORT  |  {guide.get('program_title', ctx.job.asset_name)}",
        f"Deck Validator Agent  |  {ctx.job.entity_name or 'Programme'}  |  {stamp}",
        logo_path=graphics.logo_on_dark(ctx.palette))
    summary_sheet.meta_grid([
        ("Asset", ctx.job.asset_name),
        ("Entity", ctx.job.entity_name or "-"),
        ("Decks", str(len(results))),
        ("Score", f"{overall['score']} / 10" if overall["score"] is not None
                   else "not assessed"),
        ("Verdict", overall["verdict"]),
        ("Generated", stamp),
    ])

    start = summary_sheet.section_header(
        "", "PER-DECK VERDICT",
        "Blended score: the model's judgement, less deductions for mechanical "
        "faults measured directly from each file.")
    summary_sheet.table(
        ["Deck", "Module", "Score /10", "Verdict",
         "Slides / images / audio", "Headline observation"],
        [[r["module_sr_no"], r["module_name"],
          r["score"] if r["assessed"] else "-", r["verdict"],
          f"{r['checks']['slides_visible']} / {r['checks']['slides_with_images']}"
          f" / {r['checks']['slides_narrated']}",
          r["headline"]] for r in results],
        align_center=[0, 2, 4],
    )
    summary_sheet.close_section(start)

    start = summary_sheet.section_header(
        "", "OVERALL USABILITY STATEMENT",
        "Why this asset can, or cannot, be considered fit for learner use.")
    summary_sheet.paragraphs(overall["statement"])
    summary_sheet.close_section(start)

    if overall["mechanical"]:
        start = summary_sheet.section_header(
            "", "MECHANICAL FAULTS MEASURED ACROSS THE ASSET",
            "Computed from the files rather than inferred.")
        summary_sheet.numbered_list(overall["mechanical"],
                                    accent=ctx.palette.get("warning", "#E8A400"))
        summary_sheet.close_section(start)

    sheets = [summary_sheet]
    for result in results:
        sheet = excelfmt.add_sheet(
            workbook, f"Deck {result['module_sr_no']}", style, content_cols=5,
            col_widths=[30, 11, 24, 42, 34])
        sheet.banner(
            f"DECK {result['module_sr_no']}  |  {result['module_name']}",
            (f"Score {result['score']} / 10" if result["assessed"]
             else "Not assessed") + f"  |  {result['verdict']}  |  {stamp}",
            logo_path=graphics.logo_on_dark(ctx.palette))

        start = sheet.section_header("", "DIMENSION SCORES",
                                    "Each dimension judged against the module brief.")
        sheet.table(
            ["Dimension", "Score /10", "Verdict", "Evidence", "Residual risk"],
            [[s.get("dimension", ""), s.get("score", ""), s.get("verdict", ""),
              s.get("evidence", ""), s.get("risks", "")]
             for s in result["scores"]],
            align_center=[1],
        )
        sheet.close_section(start)

        for title, caption, lines in (
            ("USABILITY STATEMENT", "Fitness for learner use.",
             [result["statement"]]),
            ("ORIGINALITY AND PLAGIARISM ASSESSMENT",
             "Is the phrasing original and are the examples fictitious?",
             [result["originality"]]),
            ("STRENGTHS", "What is genuinely good about this deck.",
             result["strengths"] or ["None recorded."]),
            ("SPELLING, GRAMMAR AND AMBIGUITY", "Language defects found.",
             result["language"] or ["No language defects found."]),
            ("FACTUAL CONCERNS", "Statements that are wrong or unverifiable.",
             result["factual"] or ["No factual concerns raised."]),
            ("ACTIVITY FINDINGS", "MCQ and fill-in-the-blank integrity.",
             (result["activity"] + result["checks"]["activity_faults"])
             or ["No activity defects found."]),
            ("MECHANICAL FAULTS", "Measured directly from the deck file.",
             result["mechanical"] or ["No mechanical faults measured."]),
            ("RECOMMENDATIONS", "Prioritised, actionable fixes.",
             result["recommendations"] or ["No changes required."]),
        ):
            start = sheet.section_header("", title, caption)
            if len(lines) == 1 and len(str(lines[0])) > 170:
                sheet.paragraphs(str(lines[0]))
            else:
                sheet.numbered_list([str(line) for line in lines])
            sheet.close_section(start)
        sheet.freeze_below_banner()
        sheets.append(sheet)

    path = ctx.artifact_path(
        f"{ctx.job.asset_name[:46].strip()} - Validation Report.xlsx")
    return excelfmt.save(workbook, path, sheets=sheets,
                         footer_note=f"{ctx.job.asset_name}  |  "
                                     f"{config.CONFIDENTIALITY_NOTE}")


def _write_markdown(ctx: AgentContext, results: list[dict], overall: dict) -> Path:
    lines = [
        f"# Validation report - {ctx.job.asset_name}",
        "",
        f"- **Entity:** {ctx.job.entity_name or 'Not specified'}",
        f"- **Decks validated:** {len(results)}",
        f"- **Overall score:** "
        + (f"{overall['score']} / 10" if overall["score"] is not None
           else "not assessed"),
        f"- **Overall verdict:** {overall['verdict']}",
        f"- **Generated:** {store.now_iso()[:16].replace('T', ' ')}",
        "",
        "## Usability statement",
        "",
        overall["statement"],
        "",
    ]
    if overall["mechanical"]:
        lines += ["## Mechanical faults measured across the asset", ""]
        lines += [f"- {item}" for item in overall["mechanical"]] + [""]

    for result in results:
        lines += [
            f"## Deck {result['module_sr_no']} - {result['module_name']}",
            "",
            (f"**Score {result['score']} / 10 - {result['verdict']}**"
             if result["assessed"] else f"**{result['verdict']}**"),
            "",
            "| Dimension | Score | Verdict |",
            "| --- | --- | --- |",
        ]
        for score in result["scores"]:
            lines.append(f"| {score.get('dimension', '')} | "
                         f"{score.get('score', '')} | {score.get('verdict', '')} |")
        lines += ["", result["statement"], ""]
        if result["recommendations"]:
            lines += ["**Recommendations**", ""]
            lines += [f"1. {item}" for item in result["recommendations"]] + [""]

    path = ctx.artifact_path("validation-report.md")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def run(ctx: AgentContext) -> dict:
    guide = ctx.payload(1).get("guide_sheet") or {}
    decks = ctx.payload(2).get("decks") or []
    if not decks:
        raise RuntimeError("Agent 2 produced no decks; rerun the deck generator.")

    ctx.step(0.04, f"Validating {len(decks)} deck(s) against the guide sheet.")
    results: list[dict] = []

    for index, deck_info in enumerate(decks, 1):
        path = Path(deck_info["path"])
        if not path.exists():
            ctx.log(f"Deck missing, skipped: {deck_info.get('filename')}", "warning")
            continue

        base = 0.05 + 0.88 * (index - 1) / len(decks)
        ctx.progress(base, f"Checking deck {index}/{len(decks)} mechanically")
        presentation = deck.open_deck(path)
        checks = _mechanical_checks(deck_info, presentation, guide,
                                   deck_info.get("plan") or {})
        penalty, mechanical = _mechanical_penalty(checks)

        ctx.progress(base + 0.44 / len(decks),
                     f"Auditing deck {index}/{len(decks)} content")
        try:
            judgement = _judge_deck(ctx, deck_info, presentation, guide, checks)
        except llm.LLMError as exc:
            ctx.log(f"Model audit unavailable for deck {index}: {exc}", "warning")
            judgement = {}

        # Never invent a verdict. If the content audit could not run, the deck is
        # reported as unassessed - a fabricated "7.0 / Tested OK" in a validation
        # report is worse than an honest gap.
        model_score = judgement.get("overall_score")
        assessed = model_score is not None and bool(judgement.get("scores"))
        score = _blend(model_score, penalty) if assessed else None
        hard = bool(checks["mcq_faults"] or checks["blank_faults"]
                    or checks["over_slide_cap"] or checks["dangling_citations"])
        if assessed:
            verdict = judgement.get("overall_verdict") or _verdict_for(score, hard)
        else:
            verdict = "Not assessed - content audit unavailable"

        results.append({
            "module_sr_no": deck_info.get("module_sr_no", index),
            "module_name": deck_info.get("module_name", ""),
            "filename": deck_info.get("filename", ""),
            "assessed": assessed,
            "score": score,
            "model_score": round(float(model_score), 1) if assessed else None,
            "penalty": round(penalty, 1),
            "verdict": verdict,
            "statement": (judgement.get("usability_statement") if assessed else
                          "The content audit for this deck could not be completed, "
                          "so no fitness-for-use judgement is claimed. The "
                          "mechanical checks below did run and are reported as "
                          "measured. Rerun this agent to obtain a full verdict."),
            "originality": judgement.get("originality_notes") or "Not assessed.",
            "scores": judgement.get("scores") or [],
            "language": judgement.get("spelling_and_language") or [],
            "factual": judgement.get("factual_concerns") or [],
            "activity": judgement.get("activity_findings") or [],
            "recommendations": judgement.get("recommendations") or [],
            "strengths": judgement.get("strengths") or [],
            "mechanical": mechanical,
            "checks": checks,
            "headline": (mechanical[0] if mechanical
                         else (judgement.get("strengths")
                               or ["Mechanical checks passed; content audit "
                                   "not completed."])[0]
                         if not assessed else
                         (judgement.get("strengths") or ["Clean pass."])[0]),
        })
        ctx.log(f"Deck {index}: {'scored ' + str(score) + '/10' if assessed else 'not assessed'}"
                f" ({results[-1]['verdict']}); {len(mechanical)} mechanical fault(s).")

    if not results:
        raise RuntimeError("No decks were available to validate.")

    scored = [r for r in results if r["assessed"]]
    all_mechanical = [f"Deck {r['module_sr_no']}: {item}"
                      for r in results for item in r["mechanical"]]
    unassessed = [str(r["module_sr_no"]) for r in results if not r["assessed"]]

    if scored:
        overall_score = round(sum(r["score"] for r in scored) / len(scored), 1)
        worst = min(r["score"] for r in scored)
        overall_verdict = _verdict_for(min(overall_score, worst + 0.5),
                                       bool(all_mechanical))
        head = (f"{len(scored)} of {len(results)} deck(s) completed a full content "
                f"audit and score {overall_score} out of 10 on average.")
    else:
        overall_score = None
        overall_verdict = "Not assessed - content audit unavailable"
        head = (f"None of the {len(results)} deck(s) completed a content audit, so "
                "no fitness-for-use verdict is claimed.")

    tail = ""
    if unassessed:
        tail = (f" Deck(s) {', '.join(unassessed)} were not content-audited "
                "because the model was unavailable; rerun this agent to complete "
                "them. Their mechanical checks did run and are reported as measured.")
    statement = " ".join([head] + [r["statement"] for r in scored[:2]]) + tail
    overall = {"score": overall_score, "verdict": overall_verdict,
               "statement": statement, "mechanical": all_mechanical,
               "unassessed": unassessed}

    ctx.progress(0.94, "Writing the validation report.")
    workbook = _write_report(ctx, results, overall, guide)
    ctx.register(workbook, "xlsx", "Validation report workbook")
    markdown = _write_markdown(ctx, results, overall)
    ctx.register(markdown, "md", "Validation report (Markdown)")

    summary = ((f"Overall {overall_score}/10 - {overall_verdict}. "
                if overall_score is not None
                else f"{overall_verdict}. ")
               + f"{len(scored)}/{len(results)} deck(s) content-audited, "
               + f"{len(all_mechanical)} mechanical fault(s) found.")
    ctx.step(1.0, summary)

    return {
        "summary": summary,
        "overall_score": overall_score,
        "overall_verdict": overall_verdict,
        "decks_assessed": len(scored),
        "decks_unassessed": unassessed,
        "notices": ([f"Deck(s) {', '.join(unassessed)} were not content-audited "
                     "because the model was unavailable. Rerun this agent to "
                     "complete them."] if unassessed else []),
        "usability_statement": statement,
        "decks": results,
        "mechanical_faults": all_mechanical,
        "report_workbook": str(workbook),
        "report_markdown": str(markdown),
    }

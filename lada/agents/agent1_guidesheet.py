"""Agent 1 - Guide-Sheet Generator.

Produces the eight-section programme guide sheet and renders it as a
brand-formatted Excel workbook. The structured guide sheet it returns is the
spine of the whole pipeline: Agent 2 builds one deck per module from it, and
Agent 5 validates the decks back against it.

Generation is split into two model calls so neither hits an output-token wall
and each gets a focused instruction:

1. **Framing** - overview, objectives, outcomes, prerequisites, competencies.
2. **Coverage** - modules/topics/sub-topics, assessment rubric, licences,
   constrained by the framing so competency codes line up.
"""

from __future__ import annotations

import math
from typing import Any

from .. import config, excelfmt, graphics, schemas, store
from ..orchestrator import AgentContext

SYSTEM = (
    "You are a principal instructional designer and curriculum architect who "
    "builds accredited technical training programmes for universities (HEIs) and "
    "enterprises. You write precise, assessable, standards-aligned learning "
    "documentation. You never invent accreditation claims, never pad with filler, "
    "and you always ground every objective, outcome and module in the subject "
    "matter and coverage supplied to you. Use British/Indian English spelling. "
    "Use fictitious but realistic organisation and person names in examples."
)

_FRAMING_KEYS = ("program_title", "program_overview", "target_audience",
                 "delivery_mode", "total_duration_hours", "learning_objectives",
                 "outcomes_understand", "outcomes_do", "prerequisites",
                 "competency_documentation", "competency_technology",
                 "competency_policies", "competency_events")

_COVERAGE_KEYS = ("modules", "assessment_structure", "assessment_rubric",
                  "assessment_effectiveness", "licenses")


def _subset(schema: dict, keys: tuple[str, ...]) -> dict:
    props = {k: v for k, v in schema["properties"].items() if k in keys}
    return {"type": "object", "properties": props, "required": list(props.keys())}


def _brief(ctx: AgentContext) -> str:
    """Assemble the operator's inputs plus any uploaded documentation."""
    job = ctx.job
    doc_context = store.upload_context(job.id, max_chars=26_000)
    lines = [
        f"ASSET NAME: {job.asset_name}",
        f"ENTITY (HEI / enterprise): {job.entity_name or 'Not specified'}"
        + (f" ({job.entity_type})" if job.entity_type else ""),
        f"TOTAL TRAINING DURATION: {job.duration_hours:g} hours",
    ]
    if job.audience:
        lines.append(f"AUDIENCE: {job.audience}")
    lines.append("\n=== CONTENT AND SUBJECT BRIEF ===\n" + (job.content_brief or "-"))
    if job.coverage_brief.strip():
        lines.append("\n=== COVERAGE / CURRICULUM / TOPICS AND SUB-TOPICS ===\n"
                     + job.coverage_brief)
    if doc_context:
        lines.append("\n=== UPLOADED PROGRAMME DOCUMENTATION (verbatim extract) ===\n"
                     + doc_context)
    return "\n".join(lines)


def _module_budget(hours: float) -> int:
    """One deck per 60-minute module, so module count == ceil(hours)."""
    return max(1, min(40, int(math.ceil(max(hours, 0.5)))))


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------
def _generate_framing(ctx: AgentContext, brief: str) -> dict:
    prompt = f"""Design the framing of a training programme guide sheet.

{brief}

Produce:
* program_overview - 4 to 5 substantial paragraphs (separate paragraphs with a
  blank line): programme context and business/academic need, who it is for,
  what it covers and deliberately excludes, how it is delivered, and the
  outcome a sponsor should expect.
* learning_objectives - EXACTLY 5, each a single measurable sentence starting
  with a verb, covering the breadth of the supplied coverage.
* outcomes_understand - 5 to 7 statements of what the learner will be able to
  UNDERSTAND (cognitive/knowledge outcomes).
* outcomes_do - 5 to 7 statements of what the learner will be able to DO
  (observable, tool-level or process-level performance).
* prerequisites - 6 to 9 entries forming the blueprint for a pre-qualifier
  assessment. Each needs a precise knowledge-or-skill statement, whether it is
  Knowledge or Skill, a weightage percentage, the assessment item type, and how
  many items. The weightage_pct values MUST total exactly 100.
* Four competency groups, each with 3 to 5 entries and stable codes:
  - competency_documentation (codes C-DOC-1...): competency to work WITH
    documentation - reading specs, writing runbooks, ADRs, API docs, test
    evidence - only what is genuinely critical for this subject matter.
  - competency_technology (codes C-TTP-1...): competency developed by
    implementing the technology, tools and processes taught in the programme.
  - competency_policies (codes C-PSG-1...): competency to understand and comply
    with policies, standards (name real in-house-style and international
    standards that genuinely apply, e.g. ISO/IEC, OWASP, WCAG, IEEE), best
    practices and guidelines relevant to these topics.
  - competency_events (codes C-EVT-1...): competency to participate in and
    collaborate through events and ceremonies relevant to a development track
    (stand-ups, backlog refinement, sprint reviews, retrospectives, code
    review, incident review, demo days).
Every competency needs a description of how the programme develops it and the
observable evidence of attainment.

Set total_duration_hours to {ctx.job.duration_hours:g}.{ctx.review_directive()}"""

    return ctx.client.generate_json(
        prompt, _subset(schemas.GUIDESHEET_SCHEMA, _FRAMING_KEYS),
        system=SYSTEM, deep=True, temperature=0.6, thinking="high",
        call_kind="guidesheet-framing",
    )


def _generate_coverage(ctx: AgentContext, brief: str, framing: dict) -> dict:
    modules_wanted = _module_budget(ctx.job.duration_hours)
    codes: list[str] = []
    for group in ("competency_documentation", "competency_technology",
                  "competency_policies", "competency_events"):
        for item in framing.get(group) or []:
            code = (item or {}).get("code")
            if code:
                codes.append(f"{code} = {item.get('competency', '')}")

    objectives = "\n".join(f"  {i}. {o}" for i, o in
                           enumerate(framing.get("learning_objectives") or [], 1))
    outcomes = "\n".join(
        f"  - {o}" for o in ((framing.get("outcomes_understand") or [])
                             + (framing.get("outcomes_do") or []))
    )

    prompt = f"""Build the detailed training coverage, post-training assessment and
licence schedule for this programme.

{brief}

=== ALREADY-AGREED FRAMING (do not contradict) ===
Programme title: {framing.get('program_title', ctx.job.asset_name)}
Learning objectives:
{objectives}
Learning outcomes:
{outcomes}
Competency codes available for alignment:
{chr(10).join('  ' + c for c in codes) or '  (none)'}

=== REQUIREMENTS ===
modules: produce EXACTLY {modules_wanted} modules, in teaching order, together
covering the whole supplied coverage with no gaps and no repetition.
For each module:
  - sr_no starting at 1.
  - module_name: specific and descriptive, not generic.
  - module_objective: 2-3 sentences, action-oriented.
  - duration_minutes: 60 or less. Prefer exactly 60 unless the content is thin.
  - topics: 2 to 4 topics. Each topic needs a hierarchical number
    ("<module sr_no>.<n>"), a VERBOSE meaningful title (6-14 words, not a bare
    keyword), duration_minutes from {{15, 30, 45, 60}}, and 2 to 5 sub-topics.
    Sub-topic numbers are "<module>.<topic>.<n>" with equally verbose titles.
    The topic durations of a module MUST sum to that module's duration_minutes.
  - competency_alignment: name the specific competency codes above that the
    module builds, then explain in 2-3 sentences HOW the module builds them.
  - pedagogy: a concrete pedagogy example for this module, e.g. "Instructor
    demo, then paired lab on a seeded repo, closing with a 5-minute retro".
    Vary pedagogy meaningfully across modules.

assessment_structure: prose describing the post-training assessment - sections,
item counts, duration, tooling, and how knowledge is separated from skill.
assessment_rubric: 6 to 9 criteria. Each needs the dimension (Knowledge or
Skill), which learning outcome it evidences, the modules/topics it covers, a
weightage percentage, the assessment method, and a description of the four
performance bands (Exemplary / Proficient / Developing / Not yet). The
weightage_pct values MUST total exactly 100.
assessment_effectiveness: explain how the resulting scores demonstrate training
effectiveness in knowledge and in skill against the learning outcomes.

licenses: every software licence, subscription, cloud account or dataset needed
to actually run this training, numbered from 1. State the tier, which
modules need it, quantity per learner or per cohort, and any procurement or
licensing caveat. Prefer free/community tiers where they genuinely suffice and
say so.{ctx.review_directive()}"""

    return ctx.client.generate_json(
        prompt, _subset(schemas.GUIDESHEET_SCHEMA, _COVERAGE_KEYS),
        system=SYSTEM, deep=True, temperature=0.55, thinking="high",
        call_kind="guidesheet-coverage",
    )


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _snap_minutes(value: int) -> int:
    """Clamp a topic duration onto the allowed 15/30/45/60 ladder."""
    allowed = config.ALLOWED_TOPIC_MINUTES
    if value in allowed:
        return value
    return min(allowed, key=lambda a: (abs(a - value), a))


def _normalise(guide: dict, job: store.Job) -> tuple[dict, list[str]]:
    """Repair the model's arithmetic so the workbook is internally consistent."""
    notes: list[str] = []

    guide["program_title"] = (guide.get("program_title") or job.asset_name).strip()
    guide["total_duration_hours"] = float(
        guide.get("total_duration_hours") or job.duration_hours or 0)

    objectives = [o.strip() for o in (guide.get("learning_objectives") or []) if o.strip()]
    if len(objectives) != 5:
        notes.append(f"Model returned {len(objectives)} learning objectives; "
                     "the specification requires 5.")
        objectives = objectives[:5]
    guide["learning_objectives"] = objectives

    # --- weightage totals ---
    for key, label in (("prerequisites", "Pre-requisite"),
                       ("assessment_rubric", "Assessment rubric")):
        rows = guide.get(key) or []
        for index, row in enumerate(rows, 1):
            row["sr_no"] = _as_int(row.get("sr_no"), index) or index
            row["weightage_pct"] = max(0, _as_int(row.get("weightage_pct")))
        total = sum(r["weightage_pct"] for r in rows)
        if rows and total != 100:
            notes.append(f"{label} weightages summed to {total}%; "
                         "rescaled to 100%.")
            if total > 0:
                for row in rows:
                    row["weightage_pct"] = int(round(row["weightage_pct"] * 100 / total))
            else:
                even = 100 // len(rows)
                for row in rows:
                    row["weightage_pct"] = even
            drift = 100 - sum(r["weightage_pct"] for r in rows)
            if drift and rows:
                rows[0]["weightage_pct"] += drift
        guide[key] = rows

    # --- module / topic durations ---
    modules = guide.get("modules") or []
    for index, module in enumerate(modules, 1):
        module["sr_no"] = _as_int(module.get("sr_no"), index) or index
        topics = module.get("topics") or []
        for t_index, topic in enumerate(topics, 1):
            topic["number"] = (topic.get("number") or
                               f"{module['sr_no']}.{t_index}").strip()
            topic["duration_minutes"] = _snap_minutes(
                _as_int(topic.get("duration_minutes"), 30) or 30)
            subs = topic.get("subtopics") or []
            for s_index, sub in enumerate(subs, 1):
                sub["number"] = (sub.get("number") or
                                 f"{topic['number']}.{s_index}").strip()
            topic["subtopics"] = subs
        module["topics"] = topics

        topic_total = sum(t["duration_minutes"] for t in topics)
        declared = _as_int(module.get("duration_minutes"), topic_total)
        if topic_total and topic_total != declared:
            module["duration_minutes"] = topic_total
        else:
            module["duration_minutes"] = declared or topic_total
        if module["duration_minutes"] > config.MAX_MODULE_MINUTES:
            notes.append(
                f"Module {module['sr_no']} '{module.get('module_name', '')}' came "
                f"back at {module['duration_minutes']} min; the cap is "
                f"{config.MAX_MODULE_MINUTES} min - flagged for reviewer attention."
            )
    guide["modules"] = modules

    total_minutes = sum(m.get("duration_minutes", 0) for m in modules)
    guide["computed_total_minutes"] = total_minutes
    requested = (job.duration_hours or 0) * 60
    if requested and abs(total_minutes - requested) > 60:
        notes.append(
            f"Coverage totals {total_minutes / 60:.1f} h against a requested "
            f"{job.duration_hours:g} h."
        )

    for index, licence in enumerate(guide.get("licenses") or [], 1):
        licence["sr_no"] = _as_int(licence.get("sr_no"), index) or index

    return guide, notes


# --------------------------------------------------------------------------
# Excel rendering
# --------------------------------------------------------------------------
def _topic_tree(module: dict) -> str:
    """Hierarchical, word-wrapped topic/sub-topic tree with hour durations."""
    lines: list[str] = []
    for topic in module.get("topics") or []:
        hours = topic.get("duration_minutes", 0) / 60.0
        lines.append(f"{topic['number']}  {topic.get('title', '').strip()}  "
                     f"({hours:.2f} h / {topic.get('duration_minutes', 0)} min)")
        for sub in topic.get("subtopics") or []:
            lines.append(f"      {sub['number']}  {sub.get('title', '').strip()}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _competency_rows(guide: dict, group: str) -> list[list[object]]:
    return [
        [item.get("code", ""), item.get("competency", ""),
         item.get("description", ""), item.get("evidence", "")]
        for item in guide.get(group) or []
    ]


#: Excel column widths are a property of the *sheet*, not of a table, so two
#: differently-shaped tables cannot share one worksheet without one of them
#: being wrecked. Each section therefore gets its own tab with tuned widths.
SHEET_PLAN = (
    ("Guide Sheet", "Sections 1-3"),
    ("S4 Pre-Requisites", "Section 4"),
    ("S5 Competency", "Section 5"),
    ("S6 Coverage", "Section 6"),
    ("S7 Assessment", "Section 7"),
    ("S8 Licences", "Section 8"),
)


def _open_sheet(workbook, title: str, style: excelfmt.SheetStyle, guide: dict,
                job: store.Job, palette: dict[str, str], subtitle: str,
                col_widths, content_cols: int = 6) -> excelfmt.SectionSheet:
    sheet = excelfmt.add_sheet(workbook, title, style, content_cols=content_cols,
                               col_widths=col_widths)
    sheet.banner(guide.get("program_title", job.asset_name), subtitle,
                 logo_path=graphics.logo_on_dark(palette))
    return sheet


def render_workbook(guide: dict, job: store.Job, out_path,
                    palette: dict[str, str], notes: list[str] | None = None):
    """Render the guide sheet to the branded, multi-tab workbook."""
    style = excelfmt.SheetStyle(palette)
    workbook = excelfmt.new_workbook()
    modules = guide.get("modules") or []
    prereqs = guide.get("prerequisites") or []
    rubric = guide.get("assessment_rubric") or []
    licences = guide.get("licenses") or []
    stamp = store.now_iso()[:16].replace("T", " ")
    crumb = f"Guide Sheet  |  {job.entity_name or 'Programme'}  |  {stamp}"

    # ============ Tab 1: cover, contents, Sections 1-3 ==================
    sheet = _open_sheet(workbook, "Guide Sheet", style, guide, job, palette,
                        crumb, col_widths=[14, 16, 28, 28, 26, 22])
    sheet.meta_grid([
        ("Asset", job.asset_name),
        ("Entity", job.entity_name or "-"),
        ("Type", job.entity_type or "-"),
        ("Audience", guide.get("target_audience", "-")),
        ("Duration", f"{guide.get('total_duration_hours', 0):g} hours"),
        ("Delivery", guide.get("delivery_mode", "-")),
        ("Modules", f"{len(modules)} modules / {len(modules)} decks"),
        ("Generated", stamp),
    ])

    start = sheet.section_header(
        "", "CONTENTS",
        "The eight specified sections, one worksheet per section group so every "
        "table keeps its own column widths.")
    sheet.table(
        ["Worksheet", "Section", "Content"],
        [["Guide Sheet", "1-3", "Programme overview, learning objectives, "
                                "learning outcomes"],
         ["S4 Pre-Requisites", "4", "Pre-requisite knowledge and skill with the "
                                    "pre-qualifier weightage blueprint"],
         ["S5 Competency", "5", "Documentation, technology/tools/process, "
                                "policies and standards, events and ceremonies"],
         ["S6 Coverage", "6", "Detailed training coverage: modules, hierarchical "
                              "topics and sub-topics, alignment, pedagogy"],
         ["S7 Assessment", "7", "Post-training assessment rubric, structure, "
                                "blueprint and effectiveness"],
         ["S8 Licences", "8", "Software licences and subscriptions required"]],
        align_center=[1],
        span_last=True,
    )
    sheet.close_section(start)

    start = sheet.section_header(1, "PROGRAMME OVERVIEW",
                                "Scope, audience, delivery approach and expected "
                                "outcome for the programme.")
    sheet.paragraphs(guide.get("program_overview", "-"))
    sheet.close_section(start)

    start = sheet.section_header(2, "LEARNING OBJECTIVES",
                                "Five measurable objectives for the programme.")
    sheet.numbered_list(guide.get("learning_objectives") or [], accent=style.purple)
    sheet.close_section(start)

    start = sheet.section_header(
        3, "LEARNING OUTCOMES",
        "What the learner will be able to understand, and what they will be able "
        "to do, on completion.")
    sheet.sub_header("3.A  The learner will be able to UNDERSTAND", colour=style.blue)
    sheet.numbered_list(guide.get("outcomes_understand") or [],
                        prefix="3.A.", accent=style.blue)
    sheet.spacer(1, 6)
    sheet.sub_header("3.B  The learner will be able to DO", colour=style.teal)
    sheet.numbered_list(guide.get("outcomes_do") or [],
                        prefix="3.B.", accent=style.teal)
    sheet.close_section(start)

    if notes:
        start = sheet.section_header(
            "", "GENERATION NOTES AND AUTOMATIC CORRECTIONS",
            "Recorded by the orchestrator for reviewer attention.")
        sheet.numbered_list(notes, accent=style.palette.get("warning", "#E8A400"))
        sheet.close_section(start)

    # ==================== Tab 2: Section 4 ==============================
    s4 = _open_sheet(workbook, "S4 Pre-Requisites", style, guide, job, palette,
                     f"Section 4  |  {crumb}",
                     col_widths=[8, 16, 52, 26, 9, 13])
    start = s4.section_header(
        4, "PRE-REQUISITE KNOWLEDGE AND SKILL",
        "Each statement is written so it can be assessed directly. The weightages "
        "form the blueprint for the pre-qualifier assessment and total 100%.")
    s4.table(
        ["Sr. No", "Knowledge / Skill", "Pre-requisite statement",
         "Assessment item type", "Items", "Weightage %"],
        [[p.get("sr_no"), p.get("category", ""), p.get("statement", ""),
          p.get("assessment_item_type", ""), p.get("items_count", ""),
          f"{p.get('weightage_pct', 0)}%"] for p in prereqs],
        total_row=["", "TOTAL", "", "",
                   sum(_as_int(p.get("items_count")) for p in prereqs),
                   f"{sum(_as_int(p.get('weightage_pct')) for p in prereqs)}%"],
        align_center=[0, 1, 4, 5],
    )
    s4.close_section(start)
    s4.freeze_below_banner()

    # ==================== Tab 3: Section 5 ==============================
    s5 = _open_sheet(workbook, "S5 Competency", style, guide, job, palette,
                     f"Section 5  |  {crumb}",
                     col_widths=[13, 30, 46, 36], content_cols=4)
    start = s5.section_header(
        5, "COMPETENCY FRAMEWORK",
        "Competencies developed across documentation, technology/tools/process, "
        "policies and standards, and events and ceremonies. Codes are referenced "
        "by the module alignment column in Section 6.")
    for label, group, colour in (
        ("5.A  Competency to work with DOCUMENTATION",
         "competency_documentation", style.purple),
        ("5.B  Competency developed by implementing TECHNOLOGY, TOOLS and PROCESS",
         "competency_technology", style.blue),
        ("5.C  Competency to understand and comply with POLICIES, STANDARDS "
         "(in-house and international), BEST PRACTICES and GUIDELINES",
         "competency_policies", style.teal),
        ("5.D  Competency to participate in and collaborate through EVENTS and "
         "CEREMONIES", "competency_events",
         config.mix(style.purple, style.blue, 0.5)),
    ):
        s5.sub_header(label, colour=colour)
        s5.table(
            ["Code", "Competency", "How the programme develops it",
             "Evidence of attainment"],
            _competency_rows(guide, group),
            align_center=[0],
        )
        s5.spacer(1, 7)
    s5.close_section(start)
    s5.freeze_below_banner()

    # ==================== Tab 4: Section 6 ==============================
    s6 = _open_sheet(workbook, "S6 Coverage", style, guide, job, palette,
                     f"Section 6  |  {len(modules)} modules  |  "
                     f"{guide.get('computed_total_minutes', 0) / 60:.1f} hours  |  "
                     f"{crumb}",
                     col_widths=[8, 26, 34, 54, 34, 26])
    start = s6.section_header(
        6, "DETAILED TRAINING COVERAGE",
        "Hierarchically numbered topics and sub-topics with durations, alignment "
        "to the Section 5 competencies with explanation, and the pedagogy to "
        "apply. Each module is 60 minutes or less and maps to one deck.")
    s6.table(
        ["Sr. No", "Module name", "Module objective",
         "Topics and sub-topics (hierarchical, with duration)",
         "Module alignment to competency (Section 5) with explanation",
         "Pedagogy example"],
        [[m.get("sr_no"), m.get("module_name", ""), m.get("module_objective", ""),
          _topic_tree(m), m.get("competency_alignment", ""), m.get("pedagogy", "")]
         for m in modules],
        align_center=[0],
    )
    s6.close_section(start)

    start = s6.section_header("6.A", "TOTAL DURATION ROLL-UP",
                              "Per-module totals against the requested programme "
                              "duration.")
    s6.table(
        ["Sr. No", "Module name", "Topics", "Sub-topics", "Duration (min)",
         "Duration (hours)"],
        [[m.get("sr_no"), m.get("module_name", ""), len(m.get("topics") or []),
          sum(len(t.get("subtopics") or []) for t in m.get("topics") or []),
          m.get("duration_minutes", 0),
          round(m.get("duration_minutes", 0) / 60.0, 2)] for m in modules],
        total_row=["", "TOTAL",
                   sum(len(m.get("topics") or []) for m in modules),
                   sum(len(t.get("subtopics") or [])
                       for m in modules for t in m.get("topics") or []),
                   guide.get("computed_total_minutes", 0),
                   round(guide.get("computed_total_minutes", 0) / 60.0, 2)],
        align_center=[0, 2, 3, 4, 5],
    )
    s6.close_section(start)
    s6.freeze_below_banner()

    # ==================== Tab 5: Section 7 ==============================
    s7 = _open_sheet(workbook, "S7 Assessment", style, guide, job, palette,
                     f"Section 7  |  {crumb}",
                     col_widths=[8, 30, 15, 34, 30, 13])
    start = s7.section_header(
        7, "POST-TRAINING ASSESSMENT - RUBRIC, STRUCTURE AND BLUEPRINT",
        "Coverage and weightage used to determine training effectiveness in terms "
        "of the knowledge and skill achieved against the learning outcomes.")
    s7.sub_header("7.A  Assessment structure", colour=style.purple)
    s7.paragraphs(guide.get("assessment_structure", "-"))
    s7.spacer(1, 7)
    s7.sub_header("7.B  Rubric, coverage and weightage blueprint", colour=style.blue)
    s7.table(
        ["Sr. No", "Criterion", "Knowledge / Skill", "Aligned learning outcome",
         "Coverage", "Weightage %"],
        [[r.get("sr_no"), r.get("criterion", ""), r.get("dimension", ""),
          r.get("aligned_outcome", ""), r.get("coverage", ""),
          f"{r.get('weightage_pct', 0)}%"] for r in rubric],
        total_row=["", "TOTAL", "", "", "",
                   f"{sum(_as_int(r.get('weightage_pct')) for r in rubric)}%"],
        align_center=[0, 2, 5],
    )
    s7.spacer(1, 7)
    s7.sub_header("7.C  Performance bands per criterion", colour=style.teal)
    s7.table(
        ["Sr. No", "Criterion", "Assessment method",
         "Performance bands (Exemplary / Proficient / Developing / Not yet)"],
        [[r.get("sr_no"), r.get("criterion", ""), r.get("assessment_method", ""),
          r.get("performance_levels", "")] for r in rubric],
        align_center=[0],
        span_last=True,
    )
    s7.spacer(1, 7)
    s7.sub_header("7.D  How training effectiveness is determined",
                  colour=style.purple)
    s7.paragraphs(guide.get("assessment_effectiveness", "-"))
    s7.close_section(start)
    s7.freeze_below_banner()

    # ==================== Tab 6: Section 8 ==============================
    s8 = _open_sheet(workbook, "S8 Licences", style, guide, job, palette,
                     f"Section 8  |  {crumb}",
                     col_widths=[8, 30, 18, 20, 40, 18])
    start = s8.section_header(
        8, "SOFTWARE LICENCES AND SUBSCRIPTIONS REQUIRED",
        "Numbered schedule of everything needed to actually deliver the training.")
    s8.table(
        ["Sr. No", "Software / subscription", "Type", "Edition or tier",
         "Purpose (modules that need it)", "Quantity"],
        [[l.get("sr_no"), l.get("name", ""), l.get("type", ""),
          l.get("edition_or_tier", ""), l.get("purpose", ""), l.get("quantity", "")]
         for l in licences],
        align_center=[0],
    )
    s8.close_section(start)

    start = s8.section_header("8.A", "LICENSING NOTES AND PROCUREMENT CAVEATS",
                              "Read before raising a purchase request.")
    s8.table(
        ["Sr. No", "Software / subscription", "Notes and caveats"],
        [[l.get("sr_no"), l.get("name", ""), l.get("notes", "")] for l in licences],
        align_center=[0],
        span_last=True,
    )
    s8.close_section(start)
    s8.freeze_below_banner()

    return excelfmt.save(workbook, out_path,
                         sheets=(sheet, s4, s5, s6, s7, s8),
                         footer_note=f"{job.asset_name}  |  "
                                     f"{config.CONFIDENTIALITY_NOTE}")


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def run(ctx: AgentContext) -> dict:
    ctx.step(0.06, "Assembling the programme brief and any uploaded documentation.")
    brief = _brief(ctx)

    ctx.step(0.14, "Generating programme framing: overview, objectives, outcomes, "
                   "prerequisites and competencies.")
    framing = _generate_framing(ctx, brief)

    ctx.step(0.52, "Generating detailed coverage, assessment blueprint and "
                   "licence schedule.")
    coverage = _generate_coverage(ctx, brief, framing)

    guide: dict = {**framing, **coverage}
    ctx.step(0.80, "Reconciling durations and weightages.")
    guide, notes = _normalise(guide, ctx.job)

    ctx.step(0.88, "Rendering the branded Excel guide sheet.")
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "_"
                        for c in ctx.job.asset_name).strip()[:60] or "guide-sheet"
    out_path = ctx.artifact_path(f"{safe_name} - Guide Sheet.xlsx")
    render_workbook(guide, ctx.job, out_path, ctx.palette, notes)
    ctx.register(out_path, "xlsx", "Guide Sheet workbook")

    modules = guide.get("modules") or []
    total_minutes = guide.get("computed_total_minutes", 0)
    summary = (f"{len(modules)} modules / "
               f"{sum(len(m.get('topics') or []) for m in modules)} topics / "
               f"{total_minutes / 60:.1f} h across 8 sections.")
    ctx.step(1.0, summary)

    return {
        "summary": summary,
        "guide_sheet": guide,
        "module_count": len(modules),
        "total_minutes": total_minutes,
        "notes": notes,
        "workbook": str(out_path),
    }

"""JSON schemas for structured model output.

These double as the contract between agents: Agent 1 emits ``GUIDESHEET_SCHEMA``,
Agent 2 consumes it and emits ``DECK_PLAN_SCHEMA``, Agent 3/4 walk the deck
manifest, Agent 5 emits ``VALIDATION_SCHEMA``. Keeping them here means a change
to the shape is visible to every consumer in one place.

Gemini's ``response_schema`` supports a pragmatic subset of OpenAPI: ``type``,
``properties``, ``required``, ``items``, ``enum``, ``description``. Avoid
``$ref``/``oneOf``/``additionalProperties`` - they are not reliably honoured.
"""

from __future__ import annotations

STR = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}


def _arr(items: dict, desc: str = "") -> dict:
    out = {"type": "array", "items": items}
    if desc:
        out["description"] = desc
    return out


def _obj(props: dict, required: list[str] | None = None, desc: str = "") -> dict:
    out = {"type": "object", "properties": props,
           "required": required or list(props.keys())}
    if desc:
        out["description"] = desc
    return out


# ==========================================================================
# Agent 1 - Guide sheet (8 sections)
# ==========================================================================
_SUBTOPIC = _obj({
    "number": {**STR, "description": "Hierarchical number, e.g. '2.1.3'"},
    "title": {**STR, "description": "Verbose, meaningful sub-topic title"},
})

_TOPIC = _obj({
    "number": {**STR, "description": "Hierarchical number, e.g. '2.1'"},
    "title": {**STR, "description": "Verbose, meaningful topic title"},
    "duration_minutes": {**INT, "description": "One of 15, 30, 45 or 60"},
    "subtopics": _arr(_SUBTOPIC),
})

_MODULE = _obj({
    "sr_no": INT,
    "module_name": STR,
    "module_objective": {**STR, "description": "2-3 sentences, action-oriented"},
    "topics": _arr(_TOPIC),
    "competency_alignment": {
        **STR,
        "description": "Which Section 5 competency codes this module builds "
                       "(e.g. 'C-DOC-1, C-TTP-2') plus a 2-3 sentence explanation "
                       "of how it is built.",
    },
    "pedagogy": {**STR, "description": "Concrete pedagogy example, e.g. "
                                       "'Instructor demo + paired lab + retro'"},
    "duration_minutes": {**INT, "description": "Module total, 60 or less"},
})

_PREREQ = _obj({
    "sr_no": INT,
    "category": {**STR, "enum": ["Knowledge", "Skill"]},
    "statement": {**STR, "description": "Precise 'The learner must be able to ...' "
                                        "statement suitable for an assessment item"},
    "weightage_pct": {**INT, "description": "Share of the pre-qualifier assessment"},
    "assessment_item_type": {**STR, "description": "e.g. MCQ, code trace, "
                                                   "scenario response"},
    "items_count": {**INT, "description": "Number of pre-qualifier items"},
})

_COMPETENCY = _obj({
    "code": {**STR, "description": "Stable code, e.g. C-DOC-1, C-TTP-1, "
                                   "C-PSG-1, C-EVT-1"},
    "competency": STR,
    "description": {**STR, "description": "How the programme develops it"},
    "evidence": {**STR, "description": "Observable evidence of attainment"},
})

_RUBRIC = _obj({
    "sr_no": INT,
    "criterion": STR,
    "dimension": {**STR, "enum": ["Knowledge", "Skill"]},
    "aligned_outcome": {**STR, "description": "Which Section 3 outcome it evidences"},
    "coverage": {**STR, "description": "Modules/topics covered by this criterion"},
    "weightage_pct": INT,
    "assessment_method": STR,
    "performance_levels": {**STR, "description": "Describe the 4 bands: "
                                                 "Exemplary / Proficient / "
                                                 "Developing / Not yet"},
})

_LICENCE = _obj({
    "sr_no": INT,
    "name": STR,
    "type": {**STR, "description": "e.g. IDE, cloud subscription, SaaS, dataset"},
    "edition_or_tier": {**STR, "description": "Free tier / community / enterprise"},
    "purpose": {**STR, "description": "Which modules/topics need it"},
    "quantity": {**STR, "description": "e.g. '1 per learner', '1 shared'"},
    "notes": {**STR, "description": "Licensing or procurement caveat"},
})

GUIDESHEET_SCHEMA = _obj({
    "program_title": STR,
    "program_overview": {
        **STR,
        "description": "3-5 rich paragraphs separated by blank lines: context, "
                       "audience, scope, delivery approach, outcome.",
    },
    "target_audience": STR,
    "delivery_mode": {**STR, "description": "e.g. 'Instructor-led with hands-on labs'"},
    "total_duration_hours": NUM,
    "learning_objectives": _arr(STR, "Exactly 5 objectives, each one sentence."),
    "outcomes_understand": _arr(STR, "Numbered 'the learner will be able to "
                                     "understand ...' statements."),
    "outcomes_do": _arr(STR, "Numbered 'the learner will be able to do ...' "
                             "statements, each with an observable verb."),
    "prerequisites": _arr(_PREREQ, "Pre-requisite knowledge and skill, weighted to "
                                   "serve as the pre-qualifier assessment blueprint. "
                                   "Weightages must total 100."),
    "competency_documentation": _arr(_COMPETENCY,
                                     "Competency to work with documentation."),
    "competency_technology": _arr(_COMPETENCY,
                                  "Competency developed by implementing technology, "
                                  "tools and process."),
    "competency_policies": _arr(_COMPETENCY,
                                "Competency to understand and comply with policies, "
                                "standards (in-house and international), best "
                                "practices and guidelines."),
    "competency_events": _arr(_COMPETENCY,
                              "Competency to participate/collaborate in events and "
                              "ceremonies (relevant for a development track)."),
    "modules": _arr(_MODULE, "Detailed training coverage. Each module is 60 minutes "
                             "or less and maps to one deck."),
    "assessment_structure": {**STR, "description": "Prose describing the post-training "
                                                   "assessment structure and duration."},
    "assessment_rubric": _arr(_RUBRIC, "Weightages must total 100."),
    "assessment_effectiveness": {
        **STR,
        "description": "How the scores determine training effectiveness in terms of "
                       "knowledge and skill gained against the learning outcomes.",
    },
    "licenses": _arr(_LICENCE, "Numbered software licences and subscriptions."),
})

# ==========================================================================
# Agent 2 - Deck plan
# ==========================================================================
_ACTIVITY_MCQ = _obj({
    "question": STR,
    "options": _arr(STR, "Exactly 5 options."),
    "correct_index": {**INT, "description": "0-based index of the single correct option."},
    "explanation": {**STR, "description": "Why the correct option is right and the "
                                          "distractors are not."},
    "source_hint": {**STR, "description": "Title of the content slide that teaches this."},
})

_ACTIVITY_BLANK = _obj({
    "sentence": {**STR, "description": "Sentence with the blank marked as ______."},
    "answer": {**STR, "description": "The correct term for the blank."},
    "distractors": _arr(STR, "Exactly 3 plausible wrong drag options."),
    "explanation": STR,
    "source_hint": STR,
})

_SLIDE = _obj({
    "kind": {
        **STR,
        "enum": ["title", "objectives", "agenda", "explanation", "example",
                 "activity", "activity_explanation", "recap", "next_module"],
    },
    "title": {**STR, "description": "Slide title, max 8 words."},
    "subtitle": {**STR, "description": "One supporting line, may be empty."},
    "bullets": _arr(STR, "3-5 concise bullets. For 'explanation' slides these are "
                         "the key points; keep each under 18 words."),
    "key_terms": _arr(_obj({"term": STR, "definition": STR}),
                      "2-4 key terms for explanation slides, else empty."),
    "scenario": {**STR, "description": "For 'example' slides: a concrete scenario "
                                       "using fictitious names and business entities. "
                                       "Else empty."},
    "image_prompt": {
        **STR,
        "description": "A precise art direction for the slide's visual: subject, "
                       "composition, style. No text in the image.",
    },
    "voiceover": {
        **STR,
        "description": "Presenter narration for this slide, MAX 35 words, "
                       "professional tone, includes navigation cue where relevant.",
    },
    "topic_ref": {**STR, "description": "Hierarchical topic number this slide serves, "
                                        "e.g. '2.1'. Empty for framing slides."},
    "mcqs": _arr(_ACTIVITY_MCQ, "Exactly 2 for 'activity' slides, else empty."),
    "blanks": _arr(_ACTIVITY_BLANK, "Exactly 2 for 'activity' slides, else empty."),
})

DECK_PLAN_SCHEMA = _obj({
    "deck_title": STR,
    "deck_subtitle": STR,
    "module_name": STR,
    "module_objective": STR,
    "duration_minutes": INT,
    "objectives": _arr(STR, "Exactly 5 impactful learning objectives for this deck."),
    "agenda": _arr(_obj({
        "item": STR,
        "minutes": INT,
        "topic_ref": STR,
    }), "Agenda items summing to the deck duration (max 60 minutes)."),
    "slides": _arr(_SLIDE),
    "next_module_name": {**STR, "description": "Name of the following module, "
                                               "or empty if this is the last."},
    "next_module_preview": {**STR, "description": "2-3 sentence preview of what the "
                                                  "next deck covers."},
})

# ==========================================================================
# Agent 3 - image prompt refinement
# ==========================================================================
IMAGE_PROMPTS_SCHEMA = _obj({
    "images": _arr(_obj({
        "slide_number": INT,
        "concept": {**STR, "description": "Short concept label, max 6 words."},
        "prompt": {**STR, "description": "Full art-direction prompt for the image "
                                         "model: subject, composition, palette, "
                                         "style. Must forbid text/letters."},
        "chips": _arr(STR, "1-3 very short labels for the fallback renderer."),
    })),
})

# ==========================================================================
# Agent 5 - validation
# ==========================================================================
_SCORE = _obj({
    "dimension": STR,
    "score": {**NUM, "description": "0-10, one decimal allowed."},
    "verdict": {**STR, "enum": ["Tested OK", "Tested OK with observations",
                                "Needs rework"]},
    "evidence": {**STR, "description": "What in the deck supports this score, "
                                       "citing slide titles or numbers."},
    "risks": {**STR, "description": "Residual risk or gap; empty if none."},
})

VALIDATION_SCHEMA = _obj({
    "overall_score": {**NUM, "description": "0-10 weighted overall score."},
    "overall_verdict": {**STR, "enum": ["Tested OK", "Tested OK with observations",
                                        "Needs rework"]},
    "usability_statement": {
        **STR,
        "description": "3-5 sentences stating why this content can (or cannot) be "
                       "considered fit for learner use.",
    },
    "scores": _arr(_SCORE, "One entry per dimension: Authenticity, "
                           "Originality / plagiarism risk, Content correctness, "
                           "Image relevance and quality, Activity accuracy, "
                           "Instructional feasibility, Brand and format compliance."),
    "originality_notes": {
        **STR,
        "description": "Assessment of plagiarism risk: is the phrasing original, "
                       "are examples fictitious, are any passages likely copied "
                       "from a known source?",
    },
    "spelling_and_language": _arr(STR, "Specific spelling/grammar/ambiguity defects "
                                       "found, each naming the slide. Empty if clean."),
    "factual_concerns": _arr(STR, "Statements that are wrong, outdated or "
                                  "unverifiable, each naming the slide."),
    "activity_findings": _arr(STR, "Problems with MCQ/fill-in-the-blank items: wrong "
                                   "keys, multiple correct options, weak distractors."),
    "recommendations": _arr(STR, "Prioritised, actionable fixes."),
    "strengths": _arr(STR, "What is genuinely good about the asset."),
})

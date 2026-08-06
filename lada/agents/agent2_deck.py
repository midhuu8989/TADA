"""Agent 2 - PowerPoint Presentation Agent.

Turns the guide sheet into one deck per 60-minute module, each capped at 35
visible slides, built on the HCLTech corporate template.

Deck structure per module
-------------------------
1. Fancy cover with SmartArt-style cards and a hero image slot.
2. Impactful five-objective slide (numbered SmartArt, image slot).
3. Agenda and coverage for the hour (image slot).
4. Section divider per topic, then for each topic:
   * Explanation and Key Terms (SmartArt cards, image slot)
   * Example / sample scenario with fictitious names and entities (image slot)
   * Embedded Smart Activity - 2 MCQs of 5 options with one correct answer, and
     2 drag-and-place fill-in-the-blanks (image slot)
5. Activity explanations, each citing the slide number and name that teaches it.
6. Recap (image slot).
7. Next module preview (image slot).

Every slide carries a voice-over script of at most 35 words in the presenter
notes for Agent 4, and every image slot is registered for Agent 3.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Sequence

from .. import config, deck, schemas, store
from ..orchestrator import AgentContext

SYSTEM = (
    "You are a senior learning-experience designer who writes technical training "
    "decks for universities and enterprises. Your slides are concrete and "
    "specific to the subject matter - never generic filler. Scenarios use "
    "fictitious but plausible company and person names. Voice-over scripts are "
    "written to be spoken aloud by a professional narrator. Use British/Indian "
    "English spelling. Never put text inside image prompts."
)

#: Placeholder index maps read off the corporate template's layouts.
FIVE_KEY_SLOTS = ((41, 46), (43, 49), (45, 50), (47, 51), (53, 52))


def _clean(text: Any, limit: int = 4000) -> str:
    return " ".join(str(text or "").split())[:limit]


def _shuffled_tokens(blank: dict) -> list[tuple[int, str, bool]]:
    """Deterministically shuffle drag tokens so the answer is not always first.

    ``[answer] + distractors`` would place the key in the same slot on every
    single activity, which learners spot immediately. Seeding from the sentence
    keeps the order stable across reruns of the same content.
    """
    answer = blank.get("answer", "")
    tokens = [(answer, True)] + [(d, False)
                                 for d in (blank.get("distractors") or [])[:3]]
    tokens = [t for t in tokens if t[0]]
    seed = int(hashlib.sha256((blank.get("sentence") or answer).encode()
                              ).hexdigest()[:8], 16)
    order = list(range(len(tokens)))
    # Fisher-Yates driven by a reproducible LCG - Random() would need seeding
    # state we do not want to carry, and this is short and explicit.
    for i in range(len(order) - 1, 0, -1):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        j = seed % (i + 1)
        order[i], order[j] = order[j], order[i]
    return [(position, tokens[source][0], tokens[source][1])
            for position, source in enumerate(order)]


def _cap_words(text: str, limit: int = config.MAX_VOICEOVER_WORDS) -> str:
    """Trim a voice-over script to the word budget at a sentence boundary."""
    words = _clean(text).split()
    if len(words) <= limit:
        return " ".join(words)
    trimmed = " ".join(words[:limit])
    for stop in (". ", "; ", ", "):
        cut = trimmed.rfind(stop)
        if cut > len(trimmed) * 0.55:
            return trimmed[:cut + 1].strip()
    return trimmed.rstrip(",;: ") + "."


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------
def _module_prompt(ctx: AgentContext, guide: dict, module: dict,
                   next_module: dict | None) -> str:
    topics_text: list[str] = []
    for topic in module.get("topics") or []:
        topics_text.append(
            f"  {topic.get('number')} {topic.get('title')} "
            f"({topic.get('duration_minutes')} min)")
        for sub in topic.get("subtopics") or []:
            topics_text.append(f"      {sub.get('number')} {sub.get('title')}")

    objectives = "\n".join(f"  - {o}" for o in guide.get("learning_objectives") or [])
    topic_count = len(module.get("topics") or [])

    return f"""Design one 60-minute training deck for the module below.

PROGRAMME: {guide.get('program_title', ctx.job.asset_name)}
ENTITY: {ctx.job.entity_name or 'Not specified'}
AUDIENCE: {guide.get('target_audience') or ctx.job.audience or 'Not specified'}

MODULE {module.get('sr_no')}: {module.get('module_name')}
MODULE OBJECTIVE: {module.get('module_objective')}
MODULE DURATION: {module.get('duration_minutes')} minutes
COMPETENCY ALIGNMENT: {module.get('competency_alignment')}
PEDAGOGY TO REFLECT: {module.get('pedagogy')}

TOPICS AND SUB-TOPICS TO COVER (cover all of them, and nothing else):
{chr(10).join(topics_text)}

PROGRAMME-LEVEL LEARNING OBJECTIVES for context:
{objectives}

=== WHAT TO PRODUCE ===
objectives: EXACTLY 5 impactful learning objectives for THIS module.
agenda: one item per topic, minutes matching the topic durations, topic_ref set
  to the topic number. The total must be {module.get('duration_minutes')} minutes.

slides: an ordered list following EXACTLY this pattern and nothing else:
  1. kind="title" - one slide. bullets = 3 short SmartArt card texts framing the
     module (what it is, why it matters, what you will build). subtitle = one
     framing line.
  2. kind="objectives" - one slide. bullets = the 5 objectives, each under 16 words.
  3. kind="agenda" - one slide. bullets = "<topic no> <topic title> - <n> min".
  4. Then, FOR EACH of the {topic_count} topics in order, three slides:
     a. kind="explanation" - title is the topic title shortened to 8 words or
        fewer. bullets = 4 key teaching points, each under 18 words, specific to
        the sub-topics. key_terms = 3 terms with one-sentence definitions.
        topic_ref = the topic number.
     b. kind="example" - a worked example or sample scenario for the same topic.
        scenario = 3 to 5 sentences using fictitious people and a fictitious
        company that make the concept concrete. bullets = 3 takeaways.
        topic_ref = the topic number.
     c. kind="activity" - embedded smart activity for the same topic.
        mcqs = EXACTLY 2 questions, each with EXACTLY 5 options and exactly ONE
        correct option (correct_index 0-4). Distractors must be plausible.
        blanks = EXACTLY 2 fill-in-the-blank sentences, each marking the gap as
        ______ , with the answer and EXACTLY 3 distractors.
        For every item set source_hint to the exact title of the explanation or
        example slide that teaches it. topic_ref = the topic number.
  5. kind="activity_explanation" - ONE slide after all topic slides. bullets =
     one line per activity item explaining the correct answer AND naming the
     slide that teaches it, e.g. "Q1 - multi-stage builds shrink the runtime
     image (see 'Building layered images')".
  6. kind="recap" - one slide. bullets = 5 consolidating takeaways.
  7. kind="next_module" - one slide previewing the next module.

For EVERY slide:
  - title: 8 words maximum, specific.
  - voiceover: professional narration, MAXIMUM 35 WORDS. The first slide's script
    opens the session; every other script starts with a short navigation cue
    ("Next, ...", "Now that ...", "Turning to ...").
  - image_prompt: art direction for a corporate concept illustration - subject,
    composition and mood. Explicitly state "no text, no letters, no numbers".
    Do not describe real logos or real people.

next_module_name: {(next_module or {}).get('module_name') or ''}
next_module_preview: 2-3 sentences on what that module covers. If there is no
next module, write a closing statement about consolidating the programme.
{ctx.review_directive()}"""


def _plan_module(ctx: AgentContext, guide: dict, module: dict,
                 next_module: dict | None) -> dict:
    plan = ctx.client.generate_json(
        _module_prompt(ctx, guide, module, next_module),
        schemas.DECK_PLAN_SCHEMA, system=SYSTEM, deep=True,
        temperature=0.72, thinking="high",
        call_kind=f"deck-plan-m{module.get('sr_no')}",
    )
    return _normalise_plan(plan, module, next_module)


def _normalise_plan(plan: dict, module: dict, next_module: dict | None) -> dict:
    """Enforce the structural guarantees the renderer and Agent 5 rely on."""
    plan["module_name"] = plan.get("module_name") or module.get("module_name", "")
    plan["deck_title"] = _clean(plan.get("deck_title") or plan["module_name"], 160)
    plan["duration_minutes"] = int(module.get("duration_minutes") or 60)
    plan["next_module_name"] = (plan.get("next_module_name")
                                or (next_module or {}).get("module_name") or "")
    plan["objectives"] = [_clean(o, 220)
                          for o in (plan.get("objectives") or []) if _clean(o)][:5]

    slides: list[dict] = []
    for raw in plan.get("slides") or []:
        slide = {
            "kind": (raw.get("kind") or "explanation").strip(),
            "title": _clean(raw.get("title"), 120) or "Slide",
            "subtitle": _clean(raw.get("subtitle"), 220),
            "bullets": [_clean(b, 300)
                        for b in (raw.get("bullets") or []) if _clean(b)],
            "key_terms": [
                {"term": _clean(t.get("term"), 60),
                 "definition": _clean(t.get("definition"), 260)}
                for t in (raw.get("key_terms") or []) if _clean(t.get("term"))
            ],
            "scenario": _clean(raw.get("scenario"), 1200),
            "image_prompt": _clean(raw.get("image_prompt"), 900),
            "voiceover": _cap_words(raw.get("voiceover") or ""),
            "topic_ref": _clean(raw.get("topic_ref"), 24),
            "mcqs": [],
            "blanks": [],
        }

        for mcq in raw.get("mcqs") or []:
            options = [_clean(o, 180) for o in (mcq.get("options") or []) if _clean(o)]
            if len(options) < 2:
                continue
            options = options[:5]
            index = int(mcq.get("correct_index") or 0)
            slide["mcqs"].append({
                "question": _clean(mcq.get("question"), 300),
                "options": options,
                "correct_index": max(0, min(len(options) - 1, index)),
                "explanation": _clean(mcq.get("explanation"), 500),
                "source_hint": _clean(mcq.get("source_hint"), 120),
            })

        for blank in raw.get("blanks") or []:
            sentence = _clean(blank.get("sentence"), 320)
            answer = _clean(blank.get("answer"), 90)
            if not sentence or not answer:
                continue
            if "___" not in sentence:
                sentence = re.sub(re.escape(answer), "______", sentence,
                                  count=1, flags=re.I)
                if "___" not in sentence:
                    sentence = f"{sentence} ______"
            slide["blanks"].append({
                "sentence": sentence,
                "answer": answer,
                "distractors": [_clean(d, 90)
                                for d in (blank.get("distractors") or [])
                                if _clean(d)][:3],
                "explanation": _clean(blank.get("explanation"), 500),
                "source_hint": _clean(blank.get("source_hint"), 120),
            })

        slides.append(slide)
    plan["slides"] = slides

    agenda = []
    for item in plan.get("agenda") or []:
        label = _clean(item.get("item"), 200)
        if label:
            agenda.append({"item": label,
                           "minutes": int(item.get("minutes") or 0),
                           "topic_ref": _clean(item.get("topic_ref"), 24)})
    plan["agenda"] = agenda
    return plan


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------
class DeckRenderer:
    """Renders one normalised plan into a branded .pptx."""

    def __init__(self, builder: deck.DeckBuilder, plan: dict, guide: dict,
                 job: store.Job, module_index: int, module_total: int):
        self.b = builder
        self.plan = plan
        self.guide = guide
        self.job = job
        self.module_index = module_index
        self.module_total = module_total
        #: slide number -> title, so activity explanations cite real numbers.
        self.slide_titles: dict[int, str] = {}
        self.activity_slides: list[dict] = []

    # ---------------------------------------------------------------- utils
    def _record(self, slide, title: str) -> int:
        """Register a slide under the number the audience will actually see.

        Hidden feedback slides are moved to the end of the deck at save time, so
        a visible slide's final number equals the count of visible slides up to
        and including it. Citing that number keeps the answer key correct.
        """
        number = self.b.visible_count
        self.slide_titles[number] = title
        return number

    def _image(self, slide, idx: int | None, plan_slide: dict, concept: str,
               chips: Sequence[str] = (), fallback_box=None, resize=None) -> None:
        prompt = plan_slide.get("image_prompt") or (
            f"Corporate concept illustration for '{concept}'. Flat vector style, "
            "abstract geometric composition, no text, no letters, no numbers.")
        self.b.claim_image_slot(slide, idx, prompt=prompt, concept=concept,
                                chips=[c for c in (chips or [concept]) if c],
                                fallback_box=fallback_box, resize=resize)

    # --------------------------------------------------------------- slides
    def cover(self, plan_slide: dict) -> None:
        slide = self.b.add_slide("cover_image", kind="cover")
        title = plan_slide.get("title") or self.plan["deck_title"]

        self.b.fill_text(slide, 0, self.plan["deck_title"], size=32, bold=True,
                         colour="dark_neutral")
        self.b.fill_text(
            slide, 1,
            f"Module {self.module_index} of {self.module_total}  |  "
            f"{self.plan['duration_minutes']} minutes  |  "
            f"{self.job.entity_name or 'Learning programme'}",
            size=14, colour="primary_purple")
        # Shrink the hero image so the SmartArt cards have their own band; at the
        # layout's full 5in height the cards collide with the cover subtitle.
        self._image(slide, 15, plan_slide, title,
                    chips=[self.plan["module_name"][:22]],
                    resize=(4.0, 0.0, 12.0, 3.90))
        self.b.drop_unused_placeholders(slide, keep=[0, 1, 15])

        cards = (plan_slide.get("bullets") or [])[:3]
        accents = ("primary_purple", "primary_blue", "secondary_teal")
        for index, text in enumerate(cards):
            self.b.smart_card(slide, 5.25 + index * 3.45, 4.15, 3.25, 1.18,
                              heading=_clean(text, 74),
                              accent=accents[index % 3], heading_size=11)
        if not cards:
            subtitle = plan_slide.get("subtitle") or self.plan.get(
                "module_objective", "")
            if subtitle:
                self.b.body_text(slide, 5.25, 4.20, 10.2, 1.1, [subtitle],
                                 size=12, bullet="")

        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self._record(slide, title)

    def objectives(self, plan_slide: dict) -> None:
        slide = self.b.add_slide("five_key")
        items = (plan_slide.get("bullets") or self.plan.get("objectives") or [])[:5]
        self.b.fill_text(slide, 0, plan_slide.get("title") or "Learning objectives",
                         size=30, bold=True)
        self.b.fill_text(slide, 40,
                         plan_slide.get("subtitle")
                         or "What you will be able to do by the end of this module",
                         size=13, colour="text_muted")
        self.b.fill_text(slide, 16,
                         self.plan.get("module_objective") or self.plan["module_name"],
                         size=12, colour="text_muted")

        keep = [0, 40, 16, 19]
        for index, (num_idx, text_idx) in enumerate(FIVE_KEY_SLOTS):
            if index >= len(items):
                continue
            self.b.fill_text(slide, num_idx, str(index + 1), size=22, bold=True,
                             colour="primary_purple")
            self.b.fill_text(slide, text_idx, items[index], size=11)
            keep.extend([num_idx, text_idx])

        self._image(slide, 19, plan_slide, "Learning objectives",
                    chips=["Objectives"])
        self.b.drop_unused_placeholders(slide, keep=keep)
        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self._record(slide, plan_slide.get("title") or "Learning objectives")

    def agenda(self, plan_slide: dict) -> None:
        slide = self.b.add_slide("title_content_sub")
        title = plan_slide.get("title") or "Agenda and coverage"
        rows = self.plan.get("agenda") or [
            {"item": b, "minutes": 0} for b in plan_slide.get("bullets") or []]
        total = sum(a.get("minutes", 0) for a in rows)

        self.b.fill_text(slide, 0, title, size=28, bold=True)
        self.b.fill_text(
            slide, 15,
            f"{plan_slide.get('subtitle') or self.plan['module_name']}  |  "
            f"{total or self.plan['duration_minutes']} minutes total",
            size=13, colour="primary_purple")
        self.b.drop_unused_placeholders(slide, keep=[0, 15])

        accents = ("primary_purple", "primary_blue", "secondary_teal")
        for index, row in enumerate(rows[:6]):
            self.b.smart_card(slide, 0.95, 2.35 + index * 0.92, 8.85, 0.78,
                              heading=_clean(row.get("item"), 92),
                              number=f"{index + 1:02d}",
                              accent=accents[index % 3], heading_size=13)
            if row.get("minutes"):
                self.b.chip(slide, 9.95, 2.45 + index * 0.92, 1.20, 0.56,
                            f"{row['minutes']} min", fill="dark_neutral", size=11)

        self._image(slide, None, plan_slide, "Agenda", chips=["Agenda"],
                    fallback_box=(11.45, 2.30, 4.05, 5.35))
        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self._record(slide, title)

    def divider(self, topic_ref: str, title: str, minutes: int | None) -> None:
        # The "Beam - Intense" dark decoration covers only part of the canvas, so
        # the top-left logo and bottom-left footer would sit on white. The light
        # beam is full-bleed and keeps every element legible.
        slide = self.b.add_slide("divider_light", kind="divider")
        self.b.fill_text(slide, 12, topic_ref or "", size=50, bold=True,
                         colour="primary_purple")
        self.b.fill_text(slide, 0, title, size=30, bold=True,
                         colour="dark_neutral")
        self.b.fill_text(slide, 1,
                         f"{minutes} minutes" if minutes else self.plan["module_name"],
                         size=14, colour="primary_purple")
        self.b.drop_unused_placeholders(slide, keep=[0, 1, 12])
        self.b.set_notes(slide, _cap_words(
            f"We now move to {title}. Follow the worked example and the activity "
            "that closes this topic."))
        self._record(slide, title)

    def explanation(self, plan_slide: dict) -> None:
        slide = self.b.add_slide("title_content_sub")
        title = plan_slide.get("title") or "Explanation"
        terms = plan_slide.get("key_terms") or []
        bullets = plan_slide.get("bullets") or []

        self.b.fill_text(slide, 0, title, size=27, bold=True)
        self.b.fill_text(slide, 15,
                         plan_slide.get("subtitle") or "Explanation and key terms",
                         size=13, colour="primary_purple")
        self.b.drop_unused_placeholders(slide, keep=[0, 15])

        self.b.band(slide, 2.25, 0.42, "KEY POINTS", fill="dark_neutral",
                    size=10, left=0.95, width=6.05)
        self.b.body_text(slide, 0.98, 2.85, 6.0, 2.05, bullets[:4], size=12)

        if terms:
            self.b.band(slide, 5.05, 0.42, "KEY TERMS", fill="primary_blue",
                        size=10, left=0.95, width=6.05)
            accents = ("primary_purple", "primary_blue", "secondary_teal")
            for index, term in enumerate(terms[:3]):
                self.b.smart_card(slide, 0.95, 5.62 + index * 0.90, 6.05, 0.84,
                                  heading=term.get("term", ""),
                                  body=_clean(term.get("definition"), 150),
                                  accent=accents[index], heading_size=11,
                                  body_size=9)

        self._image(slide, None, plan_slide, title,
                    chips=[t.get("term", "")[:18] for t in terms[:2]] or [title[:18]],
                    fallback_box=(7.35, 2.25, 8.15, 4.75))
        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self._record(slide, title)

    def example(self, plan_slide: dict) -> None:
        slide = self.b.add_slide("two_key")
        title = plan_slide.get("title") or "Worked example"

        self.b.fill_text(slide, 0, title, size=27, bold=True)
        self.b.fill_text(slide, 40, plan_slide.get("subtitle") or "Sample scenario",
                         size=13, colour="primary_purple")
        self._image(slide, 19, plan_slide, title, chips=["Scenario"])
        self.b.drop_unused_placeholders(slide, keep=[0, 40, 19])

        self.b.band(slide, 3.15, 0.42, "SCENARIO", fill="primary_purple",
                    size=10, left=1.15, width=9.45)
        self.b.body_text(slide, 1.18, 3.70, 9.35, 1.60,
                         [plan_slide.get("scenario") or ""], size=13, bullet="")

        accents = ("primary_blue", "secondary_teal", "primary_purple")
        for index, text in enumerate((plan_slide.get("bullets") or [])[:3]):
            self.b.smart_card(slide, 1.15 + index * 3.20, 5.50, 3.00, 2.20,
                              heading=f"Takeaway {index + 1}",
                              body=_clean(text, 190), accent=accents[index],
                              heading_size=12, body_size=11)

        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self._record(slide, title)

    def activity(self, plan_slide: dict) -> None:
        """Activity slide plus its two hidden hyperlink feedback slides."""
        slide = self.b.add_slide("blank")
        title = plan_slide.get("title") or "Smart activity"
        number = self._record(slide, title)

        self.b.band(slide, 0.30, 0.55, f"SMART ACTIVITY  |  {title}",
                    fill="primary_purple", size=14, left=2.45, width=13.25)

        correct_slide, wrong_slide = self._feedback_slides(slide, title, number)
        mcqs = plan_slide.get("mcqs") or []
        blanks = plan_slide.get("blanks") or []

        # ---- MCQs down the left column ----
        top = 1.15
        for q_index, mcq in enumerate(mcqs[:2]):
            question = _clean(mcq.get("question"), 150)
            band_height = 0.46 if len(question) <= 78 else 0.62
            self.b.band(slide, top, band_height, f"Q{q_index + 1}.  {question}",
                        fill="dark_neutral", size=11, left=0.30, width=7.55)
            top += band_height + 0.10
            for o_index, option in enumerate(mcq.get("options") or []):
                chip = self.b.chip(
                    slide, 0.42, top, 7.30, 0.44,
                    f"{chr(65 + o_index)}.  {_clean(option, 94)}",
                    outline=True, size=10, bold=False,
                    name=f"MCQ{q_index + 1}_OPT{o_index + 1}")
                self.b.link_to_slide(
                    chip,
                    correct_slide if o_index == mcq.get("correct_index")
                    else wrong_slide)
                top += 0.50
            top += 0.18

        # ---- Fill in the blanks down the right column ----
        top = 1.15
        for b_index, blank in enumerate(blanks[:2]):
            self.b.band(slide, top, 0.46, f"Fill in the blank {b_index + 1}",
                        fill="secondary_teal", size=11, left=8.15, width=7.55)
            top += 0.56
            self.b.body_text(slide, 8.25, top, 7.35, 0.74,
                             [_clean(blank.get("sentence"), 220)], size=12,
                             bullet="")
            top += 0.82
            self.b.chip(slide, 8.25, top, 2.00, 0.42, "Drop the term here",
                        fill="light_blue",
                        text_colour=self.b.palette.get("text_muted", "#5A6B85"),
                        size=9, bold=False, name=f"BLANK{b_index + 1}_TARGET")
            for t_index, token, correct in _shuffled_tokens(blank):
                chip = self.b.chip(
                    slide, 10.50 + (t_index % 2) * 2.60,
                    top + (t_index // 2) * 0.50, 2.45, 0.42,
                    _clean(token, 30), fill="primary_blue", size=10,
                    name=f"BLANK{b_index + 1}_TOKEN{t_index + 1}")
                self.b.link_to_slide(chip,
                                     correct_slide if correct else wrong_slide)
            top += 1.30

        # Image slot in whatever room the right column has left.
        if top < 7.05:
            self._image(slide, None, plan_slide, title, chips=["Activity"],
                        fallback_box=(11.15, top + 0.10, 4.55,
                                      min(2.10, 7.55 - top)))

        self.b.body_text(
            slide, 0.30, 7.80, 15.4, 0.45,
            ["Click an option to check your answer. When the deck is open in "
             "editing mode, drag a term onto its drop zone."],
            size=10, bullet="",
            colour=self.b.palette.get("text_muted", "#5A6B85"))

        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self.activity_slides.append({"slide_number": number, "title": title,
                                     "mcqs": mcqs, "blanks": blanks})

    def _feedback_slides(self, activity_slide, title: str, number: int):
        """Hidden 'Well done' / 'Try again' slides linked from every option."""
        made = []
        for label, colour, message in (
            ("Well done!", "secondary_teal",
             "That is the correct option. You have identified the right answer "
             "for this item."),
            ("Try again", "danger",
             "That option is not correct. Re-read the key points on the "
             "explanation slide, then choose again."),
        ):
            slide = self.b.add_slide("title_only", kind="divider")
            deck.set_hidden(slide)
            self.b.fill_text(slide, 0, label, size=42, bold=True,
                             colour=self.b.palette.get(colour, colour))
            self.b.drop_unused_placeholders(slide, keep=[0])
            self.b.body_text(slide, 1.15, 2.35, 9.5, 1.4, [message], size=16,
                             bullet="")
            self.b.body_text(slide, 1.15, 3.85, 9.5, 0.6,
                             [f"Activity: {title}  (slide {number})"],
                             size=12, bullet="",
                             colour=self.b.palette.get("text_muted", "#5A6B85"))
            back = self.b.chip(slide, 1.15, 5.00, 3.40, 0.55,
                               "Back to the activity", fill="primary_blue", size=12)
            self.b.link_to_slide(back, activity_slide)
            self.b.set_notes(slide, "")
            made.append(slide)
        return made[0], made[1]

    def activity_explanation(self, plan_slide: dict) -> None:
        slide = self.b.add_slide("title_content")
        title = plan_slide.get("title") or "Activity explanations"
        self.b.fill_text(slide, 0, title, size=27, bold=True)
        self.b.drop_unused_placeholders(slide, keep=[0])

        lines = plan_slide.get("bullets") or []
        derived = self._derived_explanations()
        # Prefer the derived answer key: it cites real slide numbers.
        lines = derived or lines

        self.b.band(slide, 1.85, 0.42,
                    "ANSWERS, WITH THE SLIDE THAT TEACHES EACH ITEM",
                    fill="dark_neutral", size=10, left=0.95, width=14.5)
        self.b.body_text(slide, 0.98, 2.40, 14.4, 5.25, lines[:12], size=11)
        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self._record(slide, title)

    def _derived_explanations(self) -> list[str]:
        """Answer key built from the plan, citing real slide numbers and names."""
        lines: list[str] = []
        for activity in self.activity_slides:
            for q_index, mcq in enumerate(activity["mcqs"][:2], 1):
                options = mcq.get("options") or []
                index = mcq.get("correct_index", 0)
                answer = options[index] if index < len(options) else ""
                lines.append(_clean(
                    f"Slide {activity['slide_number']} Q{q_index}: correct option "
                    f"is {chr(65 + index)} - {answer}. {mcq.get('explanation', '')} "
                    f"{self._cite(mcq.get('source_hint'))}", 420))
            for b_index, blank in enumerate(activity["blanks"][:2], 1):
                lines.append(_clean(
                    f"Slide {activity['slide_number']} Blank {b_index}: the term is "
                    f"'{blank.get('answer')}'. {blank.get('explanation', '')} "
                    f"{self._cite(blank.get('source_hint'))}", 420))
        return lines

    def _cite(self, hint: str | None) -> str:
        """Resolve a title hint to 'see slide N, "Title"'."""
        hint = _clean(hint)
        if not hint:
            return ""
        lowered = hint.lower()
        for number, title in self.slide_titles.items():
            if title and (title.lower() in lowered or lowered in title.lower()):
                return f'(see slide {number}, "{title}")'
        return f'(see "{hint}")'

    def recap(self, plan_slide: dict) -> None:
        slide = self.b.add_slide("recap")
        title = plan_slide.get("title") or "Recap"
        self.b.fill_text(slide, 0, title, size=28, bold=True)
        self.b.fill_text(slide, 40,
                         plan_slide.get("subtitle")
                         or f"Key takeaways from {self.plan['module_name']}",
                         size=13, colour="primary_purple")
        self.b.drop_unused_placeholders(slide, keep=[0, 40])

        accents = ("primary_purple", "primary_blue", "secondary_teal",
                   "primary_purple", "primary_blue")
        for index, text in enumerate((plan_slide.get("bullets") or [])[:5]):
            self.b.smart_card(slide, 0.95 + index * 3.02, 4.55, 2.82, 2.55,
                              heading=f"{index + 1:02d}", body=_clean(text, 200),
                              accent=accents[index], heading_size=17, body_size=11)

        self.b.body_text(
            slide, 0.98, 3.15, 9.6, 1.10,
            [f"Consolidating {self.plan['module_name']} before we move on. "
             "Each card below maps back to one of the module objectives."],
            size=13, bullet="", colour=self.b.palette.get("text_muted"))
        # A full-width strip would be a ~6:1 image; keep it to a usable 2:1.
        self._image(slide, None, plan_slide, title, chips=["Recap"],
                    fallback_box=(10.90, 3.05, 4.55, 1.30))
        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self._record(slide, title)

    def next_module(self, plan_slide: dict) -> None:
        slide = self.b.add_slide("divider_light", kind="divider")
        has_next = bool(self.plan.get("next_module_name"))
        title = plan_slide.get("title") or (
            "Coming next" if has_next else "Programme wrap-up")

        self.b.fill_text(slide, 12,
                         f"{self.module_index + 1:02d}" if has_next else "END",
                         size=46, bold=True, colour="primary_purple")
        self.b.fill_text(slide, 0, self.plan.get("next_module_name") or title,
                         size=28, bold=True)
        self.b.fill_text(slide, 1,
                         _clean(self.plan.get("next_module_preview")
                                or plan_slide.get("subtitle"), 320),
                         size=14, colour="text_muted")
        self.b.drop_unused_placeholders(slide, keep=[0, 1, 12])
        self._image(slide, None, plan_slide, title, chips=["Next"],
                    fallback_box=(11.20, 1.15, 4.30, 3.05))
        self.b.set_notes(slide, plan_slide.get("voiceover") or "")
        self._record(slide, title)

    # ------------------------------------------------------------------ run
    def render(self) -> None:
        topics = {t.get("number"): t for t in (self.plan.get("_topics") or [])}
        seen_topics: set[str] = set()
        handlers = {
            "title": self.cover,
            "objectives": self.objectives,
            "agenda": self.agenda,
            "explanation": self.explanation,
            "example": self.example,
            "activity": self.activity,
            "activity_explanation": self.activity_explanation,
            "recap": self.recap,
            "next_module": self.next_module,
        }
        closing = {"recap", "next_module", "activity_explanation"}

        for plan_slide in self.plan.get("slides") or []:
            kind = plan_slide.get("kind")
            handler = handlers.get(kind)
            if handler is None:
                continue
            # Budget: always leave room for the closing slides.
            if (kind not in closing
                    and self.b.visible_count >= config.MAX_SLIDES_PER_DECK - 3):
                continue

            reference = plan_slide.get("topic_ref")
            if (kind == "explanation" and reference and reference not in seen_topics
                    and self.b.visible_count < config.MAX_SLIDES_PER_DECK - 5):
                seen_topics.add(reference)
                topic = topics.get(reference) or {}
                self.divider(reference,
                             topic.get("title") or plan_slide.get("title", ""),
                             topic.get("duration_minutes"))

            handler(plan_slide)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def _safe(name: str, limit: int = 52) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)
    return " ".join(cleaned.split())[:limit].strip() or "deck"


def run(ctx: AgentContext) -> dict:
    guide = ctx.payload(1).get("guide_sheet") or {}
    modules = guide.get("modules") or []
    if not modules:
        raise RuntimeError("Agent 1 produced no modules; rerun the guide sheet.")

    ctx.step(0.04, f"Preparing {len(modules)} deck(s) - one per 60-minute module.")
    deck.working_template()

    decks: list[dict] = []
    total = len(modules)
    for index, module in enumerate(modules, 1):
        next_module = modules[index] if index < total else None
        base = 0.05 + 0.92 * (index - 1) / total
        span = 0.92 / total

        ctx.step(base + span * 0.10,
                 f"Planning deck {index}/{total}: "
                 f"{module.get('module_name', '')[:56]}")
        plan = _plan_module(ctx, guide, module, next_module)
        plan["_topics"] = module.get("topics") or []
        plan["module_objective"] = module.get("module_objective", "")

        ctx.progress(base + span * 0.62,
                     f"Composing slides for deck {index}/{total}")
        title = f"Module {module.get('sr_no', index)} - {module.get('module_name', '')}"
        builder = deck.DeckBuilder(ctx.palette, deck_title=_clean(title, 120),
                                   entity=ctx.job.entity_name,
                                   confidentiality=config.CONFIDENTIALITY_NOTE)
        renderer = DeckRenderer(builder, plan, guide, ctx.job, index, total)
        renderer.render()

        filename = (f"{module.get('sr_no', index):02d} - "
                    f"{_safe(module.get('module_name', f'Module {index}'))}.pptx")
        path = ctx.artifact_path(filename)
        builder.save(path)
        ctx.register(path, "pptx", f"Deck {index}: {module.get('module_name', '')}")

        decks.append({
            "module_sr_no": module.get("sr_no", index),
            "module_name": module.get("module_name", ""),
            "deck_title": plan["deck_title"],
            "path": str(path),
            "filename": filename,
            "slides_total": builder.slide_count,
            "slides_visible": builder.visible_count,
            "duration_minutes": plan["duration_minutes"],
            "image_slots": [slot.to_dict() for slot in builder.image_slots],
            "slide_titles": {str(k): v for k, v in renderer.slide_titles.items()},
            "activities": len(renderer.activity_slides),
            "plan": {k: v for k, v in plan.items() if k != "_topics"},
        })
        ctx.log(f"Deck {index}/{total} written: {filename} "
                f"({builder.visible_count} visible slides, "
                f"{len(builder.image_slots)} image slots)")

    visible = sum(d["slides_visible"] for d in decks)
    slots = sum(len(d["image_slots"]) for d in decks)
    activities = sum(d["activities"] for d in decks)
    summary = (f"{len(decks)} deck(s), {visible} slides, {slots} image "
               f"placeholders, {activities} activities.")
    ctx.step(1.0, summary)

    over = [d["filename"] for d in decks
            if d["slides_visible"] > config.MAX_SLIDES_PER_DECK]
    return {
        "summary": summary,
        "decks": decks,
        "deck_count": len(decks),
        "slides_visible": visible,
        "image_slots": slots,
        "notices": ([f"Decks over the {config.MAX_SLIDES_PER_DECK}-slide cap: "
                     + ", ".join(over)] if over else []),
    }

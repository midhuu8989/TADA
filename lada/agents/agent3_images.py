"""Agent 3 - Image Generator Agent.

Opens each deck Agent 2 produced, parses it, and fills every registered image
placeholder with artwork appropriate to that slide's title and content.

Two quality guarantees the specification asks for:

* **No spelling mistakes or ambiguity in the artwork.** The only reliable way to
  guarantee that from an image model is to forbid rendered text outright, so
  every prompt carries an explicit no-text constraint. Meaning is carried by the
  slide's own typography instead.
* **Background colour follows the active palette.** The palette's hex values are
  injected into every prompt, and the programmatic fallback composes strictly
  from the same palette.

Free-tier Gemini keys carry **no image quota** (HTTP 429 on every image model).
Rather than fail the pipeline, the agent detects that once and switches to the
on-brand programmatic renderer for the remainder of the run, reporting exactly
which slots were model-generated and which were composed locally.
"""

from __future__ import annotations

from pathlib import Path

from .. import config, deck, graphics, llm, schemas
from ..orchestrator import AgentContext

SYSTEM = (
    "You are an art director for corporate learning material. You write precise, "
    "concrete image prompts for a text-to-image model. You never ask for text, "
    "letters, numbers, logos, watermarks or recognisable real people in an image, "
    "because rendered text introduces spelling errors. You keep a consistent "
    "visual language across a deck."
)

#: Aspect ratios the Gemini image models accept.
SUPPORTED_RATIOS = {
    "1:1": 1.0, "2:3": 0.667, "3:2": 1.5, "3:4": 0.75, "4:3": 1.333,
    "4:5": 0.8, "5:4": 1.25, "9:16": 0.5625, "16:9": 1.778, "21:9": 2.333,
}


def _nearest_ratio(width: int, height: int) -> str:
    if not width or not height:
        return "16:9"
    target = width / height
    return min(SUPPORTED_RATIOS, key=lambda key: abs(SUPPORTED_RATIOS[key] - target))


def _palette_clause(palette: dict[str, str]) -> str:
    return (
        "Colour direction: build the image from this palette only - deep purple "
        f"{palette['primary_purple']}, bright blue {palette['primary_blue']}, "
        f"teal {palette['secondary_teal']}, and a very dark navy "
        f"{palette['dark_neutral']}. Use a clean gradient or flat background "
        "drawn from these colours. Corporate, modern, uncluttered."
    )


_HARD_CONSTRAINTS = (
    "Absolutely no text, no letters, no words, no numbers, no captions, no "
    "logos, no watermarks, no user-interface chrome, and no recognisable real "
    "people or brands. Purely visual - abstract or diagrammatic."
)


def _compose_prompt(base: str, palette: dict[str, str]) -> str:
    return f"{base.strip()}\n\n{_palette_clause(palette)}\n\n{_HARD_CONSTRAINTS}"


# --------------------------------------------------------------------------
# Art-direction pass
# --------------------------------------------------------------------------
def _direct_deck(ctx: AgentContext, deck_info: dict, slots: list[dict]) -> dict:
    """One LLM call per deck to harmonise prompts across its slides.

    Returns ``{slide_number: {"concept", "prompt", "chips"}}``. Falls back to the
    prompts Agent 2 already stored if the call fails - imagery must never block
    the pipeline.
    """
    titles = deck_info.get("slide_titles") or {}
    lines = []
    for slot in slots:
        number = slot["slide_number"]
        lines.append(
            f"  slide {number}: title={titles.get(str(number), '')!r} "
            f"aspect={_nearest_ratio(slot['width'], slot['height'])} "
            f"existing_direction={(slot.get('prompt') or '')[:200]!r}")

    prompt = f"""Art-direct the illustrations for one training deck so they read as
one coherent set.

DECK: {deck_info.get('deck_title')}
MODULE: {deck_info.get('module_name')}

Image slots to direct ({len(slots)} of them):
{chr(10).join(lines)}

For each slide number above, return:
  - concept: a 3-6 word label for what the image shows.
  - prompt: a single-paragraph art direction. Name the subject, the composition
    and the visual metaphor. Keep ONE consistent illustration style across the
    whole deck (choose one: isometric technical illustration, flat vector
    diagram, or soft 3D geometric shapes). Respect the aspect ratio given. State
    explicitly that the image contains no text, letters or numbers.
  - chips: 1 to 3 very short labels (max 2 words each) summarising the concept.

Vary the subject matter between slides so no two images look alike, but keep the
style, lighting and palette identical throughout.{ctx.review_directive()}"""

    try:
        result = ctx.client.generate_json(
            prompt, schemas.IMAGE_PROMPTS_SCHEMA, system=SYSTEM,
            temperature=0.75, thinking="low",
            call_kind=f"art-direction-m{deck_info.get('module_sr_no')}")
    except llm.LLMError as exc:
        ctx.log(f"Art-direction pass unavailable for "
                f"{deck_info.get('filename')}: {exc}", "warning")
        return {}

    directed: dict[int, dict] = {}
    for item in result.get("images") or []:
        try:
            number = int(item.get("slide_number"))
        except (TypeError, ValueError):
            continue
        directed[number] = {
            "concept": (item.get("concept") or "").strip()[:80],
            "prompt": (item.get("prompt") or "").strip()[:1400],
            "chips": [c.strip()[:22] for c in (item.get("chips") or [])
                      if (c or "").strip()][:3],
        }
    return directed


_CURATED_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def curated_image(module_no: int, slide_number: int) -> Path | None:
    """Artwork supplied by hand in ``assets/images/``.

    Naming convention ``deck<module>-slide<number>.<ext>`` - e.g.
    ``assets/images/deck2-slide05.png``. A curated file always wins over
    generation, which is how a designer pins a specific visual to a slide.
    """
    directory = config.ASSETS_IMAGE_DIR
    if not directory.exists():
        return None
    for stem in (f"deck{module_no}-slide{slide_number:02d}",
                 f"deck{module_no}-slide{slide_number}"):
        for suffix in _CURATED_SUFFIXES:
            candidate = directory / f"{stem}{suffix}"
            if candidate.exists() and candidate.stat().st_size > 0:
                return candidate
    return None


def _fallback_image(ctx: AgentContext, out_path: Path, *, concept: str,
                    subtitle: str, chips: list[str], seed: str,
                    width: int, height: int) -> Path:
    ratio = (width / height) if (width and height) else 16 / 9
    if ratio >= 1:
        size = (1280, max(360, int(1280 / ratio)))
    else:
        size = (max(360, int(1000 * ratio)), 1000)
    return graphics.render_brand_illustration(
        out_path, title=concept or "Concept", subtitle=subtitle, chips=chips,
        palette=ctx.palette, size=size, seed=seed, caption=False)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def run(ctx: AgentContext) -> dict:
    decks = ctx.payload(2).get("decks") or []
    if not decks:
        raise RuntimeError("Agent 2 produced no decks; rerun the deck generator.")

    # Count slots from the decks on disk, not from Agent 2's stored manifest: a
    # deck rerun can change the slot count and a stale denominator makes the
    # progress bar report "39/36".
    slot_counts: dict[str, list[dict]] = {}
    for deck_info in decks:
        path = Path(deck_info["path"])
        if path.exists():
            slot_counts[str(path)] = deck.image_slots(deck.open_deck(path))
    total_slots = sum(len(v) for v in slot_counts.values()) or 1
    ctx.step(0.03, f"Parsing {len(decks)} deck(s) covering {total_slots} "
                   "image placeholders.")

    ai_generated = 0
    fallback_generated = 0
    curated_used = 0
    failures: list[str] = []
    quota_blocked = False
    quota_detail = ""
    results: list[dict] = []
    done = 0

    for deck_index, deck_info in enumerate(decks, 1):
        path = Path(deck_info["path"])
        if not path.exists():
            failures.append(f"{deck_info.get('filename')}: deck file is missing")
            continue

        presentation = deck.open_deck(path)
        slots = slot_counts.get(str(path)) or deck.image_slots(presentation)
        if not slots:
            results.append({"filename": deck_info.get("filename"), "images": 0,
                            "note": "no image placeholders found"})
            continue

        ctx.progress(0.03 + 0.94 * (done / max(total_slots, 1)),
                     f"Art-directing deck {deck_index}/{len(decks)}")
        directed = _direct_deck(ctx, deck_info, slots)

        module_no = deck_info.get("module_sr_no", deck_index)
        image_dir = ctx.out_dir / "images" / f"deck{module_no}"
        image_dir.mkdir(parents=True, exist_ok=True)
        titles = deck_info.get("slide_titles") or {}
        deck_ai = deck_fallback = deck_curated = 0

        for slot in slots:
            number = slot["slide_number"]
            direction = directed.get(number) or {}
            title = titles.get(str(number), "") or f"Slide {number}"
            concept = direction.get("concept") or title
            base_prompt = (direction.get("prompt") or slot.get("prompt")
                           or f"Corporate concept illustration for {title}")
            chips = direction.get("chips") or [concept[:22]]
            out_path = image_dir / f"slide{number:02d}.png"
            ratio = _nearest_ratio(slot["width"], slot["height"])

            # Track production explicitly rather than testing for the file:
            # a rerun would otherwise see last run's PNG, skip regeneration, and
            # silently reuse stale artwork.
            produced = False

            curated = curated_image(module_no, number)
            if curated is not None:
                out_path = curated
                produced = True
                curated_used += 1
                deck_curated += 1

            if not produced and not quota_blocked:
                try:
                    ctx.client.generate_image(
                        _compose_prompt(base_prompt, ctx.palette), out_path,
                        aspect_ratio=ratio,
                        call_kind=f"image-m{module_no}-s{number}")
                    produced = True
                    ai_generated += 1
                    deck_ai += 1
                except llm.QuotaError as exc:
                    quota_blocked = True
                    quota_detail = str(exc)[:280]
                    ctx.log("Image generation quota exhausted for this API key; "
                            "switching to on-brand programmatic illustrations for "
                            "the rest of the run.", "warning")
                except llm.LLMError as exc:
                    ctx.log(f"Image generation failed for slide {number}: {exc}",
                            "warning")

            if not produced:
                try:
                    _fallback_image(
                        ctx, out_path, concept=concept,
                        subtitle=(deck_info.get("module_name") or "")[:90],
                        chips=chips, seed=f"{path.name}-{number}",
                        width=slot["width"], height=slot["height"])
                    produced = True
                    fallback_generated += 1
                    deck_fallback += 1
                except Exception as exc:
                    failures.append(f"slide {number}: {exc}")
                    done += 1
                    continue

            slide = presentation.slides[slot["slide_index"]]
            if not deck.replace_picture(slide, slot["shape_name"], out_path):
                failures.append(f"{path.name} slide {number}: could not place image")
            done += 1
            if done % 3 == 0 or done == total_slots:
                ctx.progress(0.03 + 0.94 * (done / max(total_slots, 1)),
                             f"Imagery {done}/{total_slots} placed")

        presentation.save(str(path))
        ctx.register(path, "pptx",
                     f"Deck {deck_index} with imagery: "
                     f"{deck_info.get('module_name', '')}")
        results.append({
            "filename": deck_info.get("filename"),
            "module_sr_no": module_no,
            "path": str(path),
            "images": deck_ai + deck_fallback + deck_curated,
            "ai_generated": deck_ai,
            "fallback_generated": deck_fallback,
            "curated": deck_curated,
            "image_dir": str(image_dir),
        })
        ctx.log(f"Deck {deck_index}: {deck_ai} model-generated, "
                f"{deck_fallback} composed on-brand, {deck_curated} curated.")

    notices: list[str] = []
    if quota_blocked:
        notices.append(
            "This API key has no image-generation quota, so "
            f"{fallback_generated} visual(s) were composed locally from the "
            "active palette instead of being generated by the image model. "
            "Enable billing on the Google AI Studio project to unlock "
            "gemini-3.1-flash-image, then rerun this agent to replace them. "
            f"API detail: {quota_detail}")
    if failures:
        notices.append(f"{len(failures)} placeholder(s) could not be filled: "
                       + "; ".join(failures[:6]))

    summary = (f"{ai_generated + fallback_generated + curated_used} image(s) placed "
               f"across {len(results)} deck(s) - {ai_generated} model-generated, "
               f"{fallback_generated} composed on-brand"
               + (f", {curated_used} curated from assets/images" if curated_used
                  else "") + ".")
    ctx.step(1.0, summary)

    return {
        "summary": summary,
        "decks": results,
        "images_total": ai_generated + fallback_generated + curated_used,
        "ai_generated": ai_generated,
        "fallback_generated": fallback_generated,
        "curated_used": curated_used,
        "quota_blocked": quota_blocked,
        "failures": failures,
        "notices": notices,
    }

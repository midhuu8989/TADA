"""Pillow-based brand graphics: fonts, gradients, the Career Shaper logo, and
the programmatic illustration renderer used when hosted image generation is
unavailable.

The illustration renderer is not a toy placeholder: it composes a gradient
field, geometric motif, concept chips and a caption band from the active
palette, so a deck built with the fallback path still reads as a finished,
on-brand asset.
"""

from __future__ import annotations

import hashlib
import math
import textwrap
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import config

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------
_FONT_CANDIDATES = {
    "black": ("seguibl.ttf", "arialbd.ttf", "verdanab.ttf", "DejaVuSans-Bold.ttf"),
    "bold": ("segoeuib.ttf", "arialbd.ttf", "verdanab.ttf", "DejaVuSans-Bold.ttf"),
    "semibold": ("seguisb.ttf", "segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"),
    "regular": ("segoeui.ttf", "arial.ttf", "verdana.ttf", "DejaVuSans.ttf"),
    "italic": ("segoeuii.ttf", "ariali.ttf", "verdanai.ttf", "DejaVuSans-Oblique.ttf"),
}

_FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path("/usr/share/fonts/truetype/dejavu"),
    Path("/usr/share/fonts"),
    Path("/Library/Fonts"),
    config.ASSETS_DIR / "fonts",
)

_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def font(weight: str = "regular", size: int = 24) -> ImageFont.FreeTypeFont:
    """Resolve a TrueType font by logical weight, degrading gracefully."""
    cache_key = (weight, size)
    if cache_key in _font_cache:
        return _font_cache[cache_key]
    for name in _FONT_CANDIDATES.get(weight, _FONT_CANDIDATES["regular"]):
        for directory in _FONT_DIRS:
            candidate = directory / name
            if candidate.exists():
                try:
                    loaded = ImageFont.truetype(str(candidate), size)
                    _font_cache[cache_key] = loaded
                    return loaded
                except OSError:
                    continue
    fallback = ImageFont.load_default()
    _font_cache[cache_key] = fallback  # type: ignore[assignment]
    return fallback  # type: ignore[return-value]


def text_size(draw: ImageDraw.ImageDraw, text: str,
              fnt: ImageFont.FreeTypeFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


# --------------------------------------------------------------------------
# Gradients
# --------------------------------------------------------------------------
def linear_gradient(size: tuple[int, int], start: str, end: str,
                    angle: float = 20.0) -> Image.Image:
    """RGB gradient from ``start`` to ``end`` at ``angle`` degrees.

    Vectorised with numpy - Agent 3 can render dozens of illustrations per
    deck, and a per-pixel Python loop made that unusably slow.
    """
    width, height = size
    c0 = np.array(config.hex_to_rgb(start), dtype=np.float32)
    c1 = np.array(config.hex_to_rgb(end), dtype=np.float32)
    rad = math.radians(angle)
    dx, dy = math.cos(rad), math.sin(rad)

    xs = np.arange(width, dtype=np.float32)
    ys = np.arange(height, dtype=np.float32)
    if dx < 0:
        xs = width - 1 - xs
    if dy < 0:
        ys = height - 1 - ys
    span = abs(dx) * (width - 1) + abs(dy) * (height - 1) or 1.0
    t = (abs(dx) * xs[None, :] + abs(dy) * ys[:, None]) / span  # (h, w) in [0, 1]

    arr = c0[None, None, :] + (c1 - c0)[None, None, :] * t[:, :, None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


def _gradient_text(text: str, fnt: ImageFont.FreeTypeFont, start: str, end: str,
                   shear: float = 0.0, pad: int = 6) -> Image.Image:
    """Render ``text`` filled with a gradient, optionally sheared for motion."""
    probe = Image.new("L", (10, 10))
    box = ImageDraw.Draw(probe).textbbox((0, 0), text, font=fnt)
    w, h = box[2] - box[0] + pad * 2, box[3] - box[1] + pad * 2
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((pad - box[0], pad - box[1]), text, font=fnt, fill=255)
    if shear:
        extra = int(abs(shear) * h)
        sheared = Image.new("L", (w + extra, h), 0)
        sheared.paste(mask, (extra if shear > 0 else 0, 0))
        mask = sheared.transform(
            sheared.size, Image.AFFINE, (1, shear, 0, 0, 1, 0),
            resample=Image.BICUBIC,
        )
        w = mask.width
    grad = linear_gradient((w, h), start, end, angle=12)
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(grad, (0, 0), mask)
    return out


def _rounded(draw: ImageDraw.ImageDraw, box, radius: int, **kwargs) -> None:
    draw.rounded_rectangle(box, radius=radius, **kwargs)


# --------------------------------------------------------------------------
# Career Shaper logo
# --------------------------------------------------------------------------
def _chevron_mark(size: int, palette: dict[str, str]) -> Image.Image:
    """Rounded-square mark with an upward 'career shaping' chevron stack."""
    scale = 4
    dim = size * scale
    img = Image.new("RGBA", (dim, dim), (0, 0, 0, 0))
    mask = Image.new("L", (dim, dim), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, dim - 1, dim - 1), radius=int(dim * 0.24), fill=255
    )
    grad = linear_gradient((dim, dim), palette["primary_purple"],
                           palette["primary_blue"], angle=35)
    img.paste(grad, (0, 0), mask)

    draw = ImageDraw.Draw(img)
    # Three ascending chevrons: small/mid in translucent white, top in teal.
    thickness = int(dim * 0.085)
    specs = (
        (0.74, 0.30, (255, 255, 255, 110)),
        (0.60, 0.42, (255, 255, 255, 175)),
        (0.46, 0.54, (255, 255, 255, 255)),
    )
    for y_frac, width_frac, colour in specs:
        half = dim * width_frac / 2
        cx, cy = dim / 2, dim * y_frac
        rise = dim * 0.15
        draw.line([(cx - half, cy), (cx, cy - rise), (cx + half, cy)],
                  fill=colour, width=thickness, joint="curve")
    # Teal spark at the apex conveys the "supercharged" outcome.
    apex = dim * 0.46 - dim * 0.15
    r = int(dim * 0.055)
    tr, tg, tb = config.hex_to_rgb(palette["secondary_teal"])
    draw.ellipse((dim / 2 - r, apex - r * 2.1, dim / 2 + r, apex + r * 0.2),
                 fill=(tr, tg, tb, 255))
    return img.resize((size, size), Image.LANCZOS)


def build_logo(palette: dict[str, str] | None = None,
               force: bool = False) -> tuple[Path, Path]:
    """Generate the Career Shaper horizontal logo and square mark.

    Returns ``(wordmark_path, mark_path)``. Drop a real
    ``assets/career_shaper_logo.png`` in place to override the wordmark.
    """
    palette = palette or config.HCLTECH_PALETTE
    logo_path, mark_path = config.LOGO_PATH, config.LOGO_MARK_PATH
    if logo_path.exists() and mark_path.exists() and not force:
        return logo_path, mark_path

    # --- square mark ---
    mark = _chevron_mark(512, palette)
    mark.save(mark_path, "PNG")

    # --- horizontal wordmark ---
    height = 240
    mark_size = 168
    mark_small = _chevron_mark(mark_size, palette)

    f_career = font("black", 96)
    f_shaper = font("black", 96)
    f_tag = font("semibold", 31)

    career_img = _gradient_text("Career", f_career, palette["dark_neutral"],
                                config.mix(palette["dark_neutral"],
                                           palette["primary_purple"], 0.45),
                                shear=-0.13)
    shaper_img = _gradient_text("Shaper", f_shaper, palette["primary_purple"],
                                palette["primary_blue"], shear=-0.13)

    gap = 14
    text_w = career_img.width + gap + shaper_img.width
    tag_text = "LEARNING ASSET DEVELOPMENT"
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tag_w = text_size(probe, tag_text, f_tag)[0]

    pad_l = 24
    total_w = pad_l + mark_size + 34 + max(text_w, tag_w) + 30
    canvas = Image.new("RGBA", (total_w, height), (255, 255, 255, 0))
    canvas.paste(mark_small, (pad_l, (height - mark_size) // 2), mark_small)

    text_x = pad_l + mark_size + 34
    top = 34
    canvas.paste(career_img, (text_x, top), career_img)
    canvas.paste(shaper_img, (text_x + career_img.width + gap, top), shaper_img)

    draw = ImageDraw.Draw(canvas)
    tag_y = top + career_img.height + 4
    draw.text((text_x + 4, tag_y), tag_text, font=f_tag,
              fill=config.hex_to_rgb(palette["secondary_teal"]))
    draw.line([(text_x + 4, tag_y + 42), (text_x + 4 + tag_w, tag_y + 42)],
              fill=config.hex_to_rgb(palette["primary_blue"]), width=4)

    canvas.save(logo_path, "PNG")
    return logo_path, mark_path


def build_reversed_logo(palette: dict[str, str], out_path: Path) -> Path:
    """Wordmark reversed for dark backgrounds, flattened onto the dark colour.

    Two reasons this exists rather than relying on PNG alpha: the brand
    guidance specifies a reversed white treatment on dark solids, and some
    xlsx/pptx consumers (LibreOffice's converter among them) drop the alpha
    channel and render a white box behind a transparent logo.
    """
    out_path = Path(out_path)
    height = 240
    mark_size = 168
    mark = _chevron_mark(mark_size, palette)

    f_word = font("black", 96)
    f_tag = font("semibold", 31)
    career = _gradient_text("Career", f_word, "#FFFFFF", "#FFFFFF", shear=-0.13)
    shaper = _gradient_text("Shaper", f_word,
                            config.lighten(palette["primary_blue"], 0.55),
                            palette["primary_blue"], shear=-0.13)

    gap = 14
    tag_text = "LEARNING ASSET DEVELOPMENT"
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    tag_w = text_size(probe, tag_text, f_tag)[0]

    pad_l = 24
    total_w = pad_l + mark_size + 34 + max(career.width + gap + shaper.width,
                                           tag_w) + 30
    canvas = Image.new("RGB", (total_w, height),
                       config.hex_to_rgb(palette["dark_neutral"]))
    canvas.paste(mark, (pad_l, (height - mark_size) // 2), mark)

    text_x = pad_l + mark_size + 34
    top = 34
    canvas.paste(career, (text_x, top), career)
    canvas.paste(shaper, (text_x + career.width + gap, top), shaper)

    draw = ImageDraw.Draw(canvas)
    tag_y = top + career.height + 4
    draw.text((text_x + 4, tag_y), tag_text, font=f_tag,
              fill=config.hex_to_rgb(palette["secondary_teal"]))
    draw.line([(text_x + 4, tag_y + 42), (text_x + 4 + tag_w, tag_y + 42)],
              fill=config.hex_to_rgb(palette["primary_blue"]), width=4)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def logo_for(palette: dict[str, str] | None = None) -> Path:
    """Path to a usable wordmark: a logo supplied in ``assets/`` wins."""
    source = user_logo()
    if source is not None:
        return source
    if config.LOGO_PATH.exists():
        return config.LOGO_PATH
    return build_logo(palette)[0]


def build_compact_logo(palette: dict[str, str], out_path: Path,
                       reversed_: bool = False) -> Path:
    """Mark + wordmark with no tagline - legible at slide-bar size (~0.4in).

    The full wordmark carries a "LEARNING ASSET DEVELOPMENT" tagline that turns
    into an illegible smudge once scaled down to a slide's top bar.
    """
    out_path = Path(out_path)
    height = 150
    mark_size = 128
    mark = _chevron_mark(mark_size, palette)

    f_word = font("black", 84)
    if reversed_:
        career = _gradient_text("Career", f_word, "#FFFFFF", "#FFFFFF", shear=-0.13)
        shaper = _gradient_text("Shaper", f_word,
                                config.lighten(palette["primary_blue"], 0.55),
                                palette["primary_blue"], shear=-0.13)
    else:
        career = _gradient_text("Career", f_word, palette["dark_neutral"],
                                config.mix(palette["dark_neutral"],
                                           palette["primary_purple"], 0.45),
                                shear=-0.13)
        shaper = _gradient_text("Shaper", f_word, palette["primary_purple"],
                                palette["primary_blue"], shear=-0.13)

    gap = 12
    pad = 10
    total_w = pad + mark_size + 22 + career.width + gap + shaper.width + pad
    if reversed_:
        canvas = Image.new("RGB", (total_w, height),
                           config.hex_to_rgb(palette["dark_neutral"])).convert("RGBA")
    else:
        canvas = Image.new("RGBA", (total_w, height), (255, 255, 255, 0))

    canvas.paste(mark, (pad, (height - mark_size) // 2), mark)
    text_x = pad + mark_size + 22
    top = (height - career.height) // 2
    canvas.paste(career, (text_x, top), career)
    canvas.paste(shaper, (text_x + career.width + gap, top), shaper)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def _palette_tag(palette: dict[str, str]) -> str:
    return hashlib.sha256(
        f"{palette['dark_neutral']}|{palette['primary_blue']}|"
        f"{palette['secondary_teal']}|{palette['primary_purple']}".encode()
    ).hexdigest()[:10]


def user_logo() -> Path | None:
    """A brand logo supplied in ``assets/``, if one is present.

    This is the hook for using a real logo instead of the generated placeholder:
    drop the file in and every surface picks it up, because the derived
    slide-bar and reversed variants are rebuilt from it rather than drawn.
    """
    for name in config.USER_LOGO_NAMES:
        candidate = config.ASSETS_DIR / name
        if candidate.exists() and candidate.stat().st_size > 0:
            return candidate
    return None


def _source_tag(path: Path) -> str:
    """Cache key that changes when the source file is replaced."""
    stat = path.stat()
    return hashlib.sha256(
        f"{path.name}|{stat.st_size}|{int(stat.st_mtime)}".encode()
    ).hexdigest()[:10]


def _derive_bar(source: Path, palette: dict[str, str], out_path: Path,
                reversed_: bool) -> Path:
    """Scale a supplied logo to the slide-bar size, flattening if reversed."""
    image = Image.open(source)
    if image.mode != "RGBA":
        image = image.convert("RGBA")
    image = image.crop(image.getbbox() or (0, 0, image.width, image.height))
    height = 150
    width = max(1, int(image.width * (height / max(image.height, 1))))
    image = image.resize((width, height), Image.LANCZOS)

    pad = 10
    canvas_size = (width + pad * 2, height + pad * 2)
    if reversed_:
        canvas = Image.new("RGB", canvas_size,
                           config.hex_to_rgb(palette["dark_neutral"])).convert("RGBA")
    else:
        canvas = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
    canvas.paste(image, (pad, pad), image)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG")
    return out_path


def logo_compact(palette: dict[str, str] | None = None,
                 reversed_: bool = False) -> Path:
    """Cached compact wordmark for slide top bars."""
    palette = palette or config.HCLTECH_PALETTE
    suffix = "rev" if reversed_ else "std"
    source = user_logo()
    if source is not None:
        path = (config.ASSETS_DIR /
                f"brandbar_{suffix}_{_source_tag(source)}_"
                f"{_palette_tag(palette)}.png")
        if path.exists():
            return path
        try:
            return _derive_bar(source, palette, path, reversed_)
        except Exception:
            pass  # fall through to the generated wordmark
    path = (config.ASSETS_DIR
            / f"career_shaper_bar_{suffix}_{_palette_tag(palette)}.png")
    if path.exists():
        return path
    return build_compact_logo(palette, path, reversed_=reversed_)


def logo_on_dark(palette: dict[str, str] | None = None) -> Path:
    """Reversed wordmark keyed to the active palette's dark neutral (cached)."""
    palette = palette or config.HCLTECH_PALETTE
    source = user_logo()
    if source is not None:
        path = (config.ASSETS_DIR /
                f"brandmark_rev_{_source_tag(source)}_{_palette_tag(palette)}.png")
        if path.exists():
            return path
        try:
            return _derive_bar(source, palette, path, reversed_=True)
        except Exception:
            pass  # fall back to the drawn reversed wordmark
    path = config.ASSETS_DIR / f"career_shaper_reversed_{_palette_tag(palette)}.png"
    if path.exists():
        return path
    return build_reversed_logo(palette, path)


def mark_for(palette: dict[str, str] | None = None) -> Path:
    if config.LOGO_MARK_PATH.exists():
        return config.LOGO_MARK_PATH
    return build_logo(palette)[1]


# --------------------------------------------------------------------------
# Programmatic brand illustration (Agent 3 fallback)
# --------------------------------------------------------------------------
_MOTIFS = ("nodes", "layers", "flow", "orbit", "grid", "bars")


def _motif_for(seed: str) -> str:
    digest = hashlib.sha256(seed.encode("utf-8")).digest()
    return _MOTIFS[digest[0] % len(_MOTIFS)]


def _draw_nodes(draw: ImageDraw.ImageDraw, box, palette, rnd) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    pts = [(x0 + w * fx, y0 + h * fy) for fx, fy in
           ((0.12, 0.62), (0.32, 0.24), (0.5, 0.72), (0.7, 0.32), (0.88, 0.6))]
    for i in range(len(pts) - 1):
        draw.line([pts[i], pts[i + 1]], fill=(255, 255, 255, 120),
                  width=max(2, int(w / 200)))
    for i, (px, py) in enumerate(pts):
        r = w * (0.055 if i % 2 == 0 else 0.038)
        colour = palette["secondary_teal"] if i % 2 else palette["white"]
        cr, cg, cb = config.hex_to_rgb(colour)
        draw.ellipse((px - r, py - r, px + r, py + r), fill=(cr, cg, cb, 235))


def _draw_layers(draw, box, palette, rnd) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    for i in range(4):
        inset = w * 0.06 * i
        top = y0 + h * (0.18 + 0.16 * i)
        alpha = 235 - i * 45
        cr, cg, cb = config.hex_to_rgb(
            palette["white"] if i % 2 == 0 else palette["secondary_teal"])
        draw.rounded_rectangle(
            (x0 + inset, top, x1 - inset, top + h * 0.13),
            radius=int(h * 0.05), fill=(cr, cg, cb, alpha))


def _draw_flow(draw, box, palette, rnd) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cy = y0 + h * 0.5
    bw, gap = w * 0.19, w * 0.07
    for i in range(4):
        bx = x0 + w * 0.04 + i * (bw + gap)
        cr, cg, cb = config.hex_to_rgb(
            palette["white"] if i % 2 == 0 else palette["secondary_teal"])
        draw.rounded_rectangle((bx, cy - h * 0.13, bx + bw, cy + h * 0.13),
                               radius=int(h * 0.05), fill=(cr, cg, cb, 225))
        if i < 3:
            ax = bx + bw + gap * 0.15
            draw.polygon([(ax, cy - h * 0.05), (ax + gap * 0.7, cy),
                          (ax, cy + h * 0.05)], fill=(255, 255, 255, 200))


def _draw_orbit(draw, box, palette, rnd) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cx, cy = x0 + w / 2, y0 + h / 2
    for i, frac in enumerate((0.44, 0.32, 0.20)):
        rx, ry = w * frac, h * frac * 0.78
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry),
                     outline=(255, 255, 255, 150 - i * 30),
                     width=max(2, int(w * 0.008)))
    cr, cg, cb = config.hex_to_rgb(palette["secondary_teal"])
    r = w * 0.075
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(cr, cg, cb, 245))
    for ang, frac in ((35, 0.44), (150, 0.32), (255, 0.20)):
        rx, ry = w * frac, h * frac * 0.78
        px = cx + rx * math.cos(math.radians(ang))
        py = cy + ry * math.sin(math.radians(ang))
        d = w * 0.028
        draw.ellipse((px - d, py - d, px + d, py + d), fill=(255, 255, 255, 235))


def _draw_grid(draw, box, palette, rnd) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    cols, rows = 5, 3
    cw, ch = w / cols, h / rows
    filled = {(0, 1), (1, 0), (2, 2), (3, 1), (4, 0), (1, 2), (3, 2)}
    for c in range(cols):
        for r in range(rows):
            bx, by = x0 + c * cw + cw * 0.12, y0 + r * ch + ch * 0.14
            box_ = (bx, by, bx + cw * 0.76, by + ch * 0.72)
            if (c, r) in filled:
                cr, cg, cb = config.hex_to_rgb(
                    palette["secondary_teal"] if (c + r) % 2 else palette["white"])
                draw.rounded_rectangle(box_, radius=int(ch * 0.12),
                                       fill=(cr, cg, cb, 225))
            else:
                draw.rounded_rectangle(box_, radius=int(ch * 0.12),
                                       outline=(255, 255, 255, 110),
                                       width=max(2, int(w * 0.005)))


def _draw_bars(draw, box, palette, rnd) -> None:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    heights = (0.36, 0.58, 0.44, 0.78, 0.62, 0.92)
    bw = w * 0.1
    gap = (w - bw * len(heights)) / (len(heights) + 1)
    for i, frac in enumerate(heights):
        bx = x0 + gap + i * (bw + gap)
        cr, cg, cb = config.hex_to_rgb(
            palette["secondary_teal"] if i % 2 else palette["white"])
        draw.rounded_rectangle((bx, y1 - h * frac, bx + bw, y1),
                               radius=int(bw * 0.22), fill=(cr, cg, cb, 230))


_MOTIF_FUNCS = {
    "nodes": _draw_nodes, "layers": _draw_layers, "flow": _draw_flow,
    "orbit": _draw_orbit, "grid": _draw_grid, "bars": _draw_bars,
}


def render_brand_illustration(
    out_path: Path,
    *,
    title: str,
    subtitle: str = "",
    chips: list[str] | None = None,
    palette: dict[str, str] | None = None,
    size: tuple[int, int] = (1280, 720),
    seed: str | None = None,
    caption: bool = True,
) -> Path:
    """Compose an on-brand concept illustration and save it as PNG.

    With ``caption=False`` the motif fills the whole frame and no text is drawn.
    That is the mode Agent 3 uses for slide artwork: a caption inside the image
    would compete with the slide's own title and reintroduce exactly the
    spelling/ambiguity risk the no-text image rule exists to remove.
    """
    palette = palette or config.HCLTECH_PALETTE
    seed = seed or title
    digest = hashlib.sha256(seed.encode("utf-8")).digest()

    angle = 8 + (digest[1] % 30)
    pair = [
        (palette["primary_purple"], palette["primary_blue"]),
        (palette["primary_blue"], palette["secondary_teal"]),
        (palette["primary_purple"], palette["accent_magenta"]),
        (palette["dark_neutral"], palette["primary_purple"]),
        (palette["dark_neutral"], palette["primary_blue"]),
    ][digest[2] % 5]

    base = linear_gradient(size, pair[0], pair[1], angle=angle).convert("RGBA")
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    w, h = size

    # Soft depth blobs.
    for i in range(3):
        rx = w * (0.34 + 0.1 * i)
        cx = w * ((digest[3 + i] % 100) / 100.0)
        cy = h * ((digest[6 + i] % 100) / 100.0)
        draw.ellipse((cx - rx, cy - rx * 0.7, cx + rx, cy + rx * 0.7),
                     fill=(255, 255, 255, 16))
    overlay = overlay.filter(ImageFilter.GaussianBlur(radius=w // 40))
    base = Image.alpha_composite(base, overlay)

    art = Image.new("RGBA", size, (0, 0, 0, 0))
    art_draw = ImageDraw.Draw(art)
    motif = _motif_for(seed)
    if caption:
        motif_box = (w * 0.10, h * 0.14, w * 0.90, h * 0.60)
    else:
        motif_box = (w * 0.11, h * 0.16, w * 0.89, h * 0.84)
    _MOTIF_FUNCS[motif](art_draw, motif_box, palette, digest)
    if not caption:
        # A second, offset motif adds depth now that there is no caption band.
        second = _MOTIFS[(digest[4] + 3) % len(_MOTIFS)]
        if second != motif:
            faint = Image.new("RGBA", size, (0, 0, 0, 0))
            _MOTIF_FUNCS[second](ImageDraw.Draw(faint),
                                 (w * 0.20, h * 0.30, w * 0.80, h * 0.70),
                                 palette, digest)
            art = Image.alpha_composite(art, faint.point(
                lambda value: int(value * 0.34) if value else 0))
    base = Image.alpha_composite(base, art)

    if not caption:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        base.convert("RGB").save(out_path, "PNG", optimize=True)
        return out_path

    # Caption band.
    band = Image.new("RGBA", size, (0, 0, 0, 0))
    band_draw = ImageDraw.Draw(band)
    band_top = h * 0.63
    dr, dg, db = config.hex_to_rgb(palette["dark_neutral"])
    band_draw.rectangle((0, band_top, w, h), fill=(dr, dg, db, 205))
    band_draw.rectangle((0, band_top, w, band_top + max(4, h * 0.008)),
                        fill=config.hex_to_rgb(palette["secondary_teal"]))
    base = Image.alpha_composite(base, band)

    draw = ImageDraw.Draw(base)
    f_title = font("black", int(h * 0.072))
    f_sub = font("regular", int(h * 0.040))
    f_chip = font("semibold", int(h * 0.031))

    y = band_top + h * 0.045
    for line in textwrap.wrap(title.strip() or "Concept", width=42)[:2]:
        draw.text((w * 0.06, y), line, font=f_title, fill=(255, 255, 255))
        y += f_title.size * 1.16

    if subtitle:
        for line in textwrap.wrap(subtitle.strip(), width=76)[:2]:
            draw.text((w * 0.06, y + h * 0.008), line, font=f_sub,
                      fill=config.hex_to_rgb(palette["light_blue"]))
            y += f_sub.size * 1.25

    if chips:
        cx = w * 0.06
        chip_y = h - h * 0.085
        for chip in chips[:4]:
            label = chip.strip()[:26]
            if not label:
                continue
            tw = draw.textbbox((0, 0), label, font=f_chip)[2]
            cw = tw + w * 0.035
            if cx + cw > w * 0.95:
                break
            cr, cg, cb = config.hex_to_rgb(palette["primary_blue"])
            draw.rounded_rectangle((cx, chip_y, cx + cw, chip_y + h * 0.055),
                                   radius=int(h * 0.027), fill=(cr, cg, cb, 235))
            draw.text((cx + w * 0.0175, chip_y + h * 0.0125), label,
                      font=f_chip, fill=(255, 255, 255))
            cx += cw + w * 0.014

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base.convert("RGB").save(out_path, "PNG", optimize=True)
    return out_path


def render_placeholder_frame(out_path: Path, palette: dict[str, str],
                             label: str = "Image", size=(960, 540)) -> Path:
    """Neutral dashed frame used as the pre-Agent-3 image placeholder."""
    w, h = size
    img = Image.new("RGB", size, config.hex_to_rgb(palette["surface_alt"]))
    draw = ImageDraw.Draw(img)
    dash, inset = 22, 10
    edge = config.hex_to_rgb(palette["primary_blue"])
    for x in range(inset, w - inset, dash * 2):
        draw.line([(x, inset), (min(x + dash, w - inset), inset)], fill=edge, width=3)
        draw.line([(x, h - inset), (min(x + dash, w - inset), h - inset)],
                  fill=edge, width=3)
    for y in range(inset, h - inset, dash * 2):
        draw.line([(inset, y), (inset, min(y + dash, h - inset))], fill=edge, width=3)
        draw.line([(w - inset, y), (w - inset, min(y + dash, h - inset))],
                  fill=edge, width=3)
    f = font("semibold", int(h * 0.062))
    text = f"{label}"
    tw, th = text_size(draw, text, f)
    draw.text(((w - tw) / 2, (h - th) / 2 - h * 0.03), text, font=f,
              fill=config.hex_to_rgb(palette["primary_purple"]))
    f2 = font("regular", int(h * 0.040))
    sub = "Agent 3 will generate the visual here"
    tw2, _ = text_size(draw, sub, f2)
    draw.text(((w - tw2) / 2, (h + th) / 2 + h * 0.02), sub, font=f2,
              fill=config.hex_to_rgb(palette["text_muted"]))
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


def audio_icon(out_path: Path, palette: dict[str, str], size: int = 128) -> Path:
    """Small speaker badge used as the poster frame for embedded slide audio."""
    out_path = Path(out_path)
    if out_path.exists():
        return out_path
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    cr, cg, cb = config.hex_to_rgb(palette["primary_purple"])
    draw.ellipse((0, 0, size - 1, size - 1), fill=(cr, cg, cb, 255))
    s = size / 128
    draw.polygon([(38 * s, 52 * s), (56 * s, 52 * s), (74 * s, 34 * s),
                  (74 * s, 94 * s), (56 * s, 76 * s), (38 * s, 76 * s)],
                 fill=(255, 255, 255, 255))
    for i, r in enumerate((16, 26)):
        draw.arc((74 * s - r * s, 64 * s - r * s, 74 * s + r * s * 2.0,
                  64 * s + r * s), start=-55, end=55,
                 fill=(255, 255, 255, 220 - i * 60), width=int(max(2, 5 * s)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, "PNG")
    return out_path

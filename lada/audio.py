"""Embedding narration audio into PowerPoint slides.

python-pptx exposes no audio API - only ``shapes.add_movie``. That call already
does the hard, easy-to-break part correctly: it registers the media part, writes
an ``audio/wav`` content-type override, and creates both the ``media`` and the
playback relationships. What it produces is a *video* shape, so this module
applies the minimal delta to turn it into an audio shape:

1. ``<a:videoFile r:link="rIdN"/>``  ->  ``<a:audioFile r:link="rIdM"/>``
2. the ``.../relationships/video`` relationship  ->  ``.../relationships/audio``
3. optionally a ``p:timing`` tree so the narration plays automatically.

Starting from python-pptx's known-good output and changing only these keeps us
well away from the malformed-XML territory that makes PowerPoint offer to
"repair" a file.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn
from pptx.util import Inches

from . import graphics

RT_AUDIO = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/audio"

_NS = {
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

#: Autoplay timing tree. ``{spid}`` is the audio shape id, ``{dur}`` its length
#: in milliseconds. Mirrors what PowerPoint itself writes for an automatically
#: started embedded sound.
_TIMING_XML = """
<p:timing xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
          xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:tnLst>
    <p:par>
      <p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">
        <p:childTnLst>
          <p:seq concurrent="1" nextAc="seek">
            <p:cTn id="2" dur="indefinite" nodeType="mainSeq">
              <p:childTnLst>
                <p:par>
                  <p:cTn id="3" fill="hold">
                    <p:stCondLst>
                      <p:cond delay="indefinite"/>
                      <p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond>
                    </p:stCondLst>
                    <p:childTnLst>
                      <p:par>
                        <p:cTn id="4" fill="hold">
                          <p:stCondLst><p:cond delay="0"/></p:stCondLst>
                          <p:childTnLst>
                            <p:par>
                              <p:cTn id="5" presetID="1" presetClass="mediacall"
                                     presetSubtype="0" fill="hold"
                                     nodeType="withEffect">
                                <p:stCondLst><p:cond delay="0"/></p:stCondLst>
                                <p:childTnLst>
                                  <p:cmd type="call" cmd="playFrom(0.0)">
                                    <p:cBhvr>
                                      <p:cTn id="6" dur="{dur}" fill="hold"/>
                                      <p:tgtEl><p:spTgt spid="{spid}"/></p:tgtEl>
                                    </p:cBhvr>
                                  </p:cmd>
                                </p:childTnLst>
                              </p:cTn>
                            </p:par>
                          </p:childTnLst>
                        </p:cTn>
                      </p:par>
                    </p:childTnLst>
                  </p:cTn>
                </p:par>
              </p:childTnLst>
            </p:cTn>
            <p:prevCondLst>
              <p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond>
            </p:prevCondLst>
            <p:nextCondLst>
              <p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond>
            </p:nextCondLst>
          </p:seq>
        </p:childTnLst>
      </p:cTn>
    </p:par>
  </p:tnLst>
</p:timing>
"""


class AudioEmbedError(RuntimeError):
    """Raised when narration could not be attached to a slide."""


def _poster(palette: dict[str, str]) -> Path:
    from . import config
    return graphics.audio_icon(
        config.ASSETS_DIR / f"audio_badge_{palette['primary_purple'].lstrip('#')}.png",
        palette)


def embed_slide_audio(
    slide,
    wav_path: Path,
    palette: dict[str, str],
    *,
    left: float = 15.05,
    top: float = 0.28,
    size: float = 0.40,
    duration_seconds: float = 0.0,
    autoplay: bool = True,
    name: str = "LADA_AUDIO",
) -> None:
    """Attach ``wav_path`` to ``slide`` as an embedded, playable audio shape."""
    wav_path = Path(wav_path)
    if not wav_path.exists():
        raise AudioEmbedError(f"Narration file missing: {wav_path.name}")

    try:
        movie = slide.shapes.add_movie(
            str(wav_path), Inches(left), Inches(top), Inches(size), Inches(size),
            poster_frame_image=str(_poster(palette)), mime_type="audio/wav")
    except Exception as exc:
        raise AudioEmbedError(f"add_movie failed: {exc}") from None

    movie.name = name
    pic = movie._element

    video_file = pic.find(f".//{{{_NS['a']}}}videoFile")
    if video_file is None:
        raise AudioEmbedError("add_movie produced no videoFile element to convert.")

    old_rid = video_file.get(qn("r:link"))
    try:
        media_part = slide.part.rels[old_rid].target_part
    except KeyError:
        raise AudioEmbedError("playback relationship is missing.") from None

    # Re-point the shape at an *audio* relationship, then retire the video one.
    new_rid = slide.part.relate_to(media_part, RT_AUDIO)
    audio_file = etree.SubElement(video_file.getparent(), f"{{{_NS['a']}}}audioFile")
    audio_file.set(qn("r:link"), new_rid)
    parent = video_file.getparent()
    parent.remove(audio_file)
    parent.insert(list(parent).index(video_file), audio_file)
    parent.remove(video_file)
    try:
        slide.part.drop_rel(old_rid)
    except Exception:
        pass  # a stale video rel is harmless; a missing audio rel would not be

    if autoplay:
        _set_autoplay(slide, movie.shape_id, duration_seconds)


def _set_autoplay(slide, shape_id: int, duration_seconds: float) -> None:
    """Replace the slide's timing tree with one that plays the narration."""
    milliseconds = max(1000, int((duration_seconds or 5.0) * 1000))
    timing = etree.fromstring(
        _TIMING_XML.strip().format(spid=shape_id, dur=milliseconds))
    slide_element = slide._element
    for existing in slide_element.findall(qn("p:timing")):
        slide_element.remove(existing)
    slide_element.append(timing)


def narration_shapes(slide) -> list:
    return [s for s in slide.shapes if (s.name or "").startswith("LADA_AUDIO")]


def strip_slide_audio(slide) -> int:
    """Remove previously embedded narration so a rerun does not stack tracks."""
    removed = 0
    for shape in narration_shapes(slide):
        shape._element.getparent().remove(shape._element)
        removed += 1
    for timing in slide._element.findall(qn("p:timing")):
        slide._element.remove(timing)
    return removed

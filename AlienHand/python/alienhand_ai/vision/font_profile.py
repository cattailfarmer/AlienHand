from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_SIGNATURE_GLYPHS = tuple("ager tyAGQRMW012478?!:".replace(" ", ""))


@dataclass(frozen=True)
class GlyphSignature:
    char: str
    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class FontProfile:
    id: str
    glyphs: dict[str, GlyphSignature] = field(default_factory=dict)
    signature_glyphs: tuple[str, ...] = DEFAULT_SIGNATURE_GLYPHS
    notes: str = ""


@dataclass(frozen=True)
class FontMatch:
    profile_id: str
    confidence: float
    scale: float = 1.0


def most_distinctive_glyphs(profiles: list[FontProfile], limit: int = 12) -> tuple[str, ...]:
    """Pick glyphs that appear across profiles and vary most by bitmap size.

    This is a lightweight placeholder for a richer glyph-embedding scorer. It is
    still useful because letters such as g, a, Q, R, 2, and 7 often differ in
    proportions across fonts.
    """
    scores: dict[str, int] = {}
    for glyph in DEFAULT_SIGNATURE_GLYPHS:
        sizes = {(profile.glyphs[glyph].width, profile.glyphs[glyph].height) for profile in profiles if glyph in profile.glyphs}
        if len(sizes) > 1:
            scores[glyph] = len(sizes)
    ranked = sorted(scores, key=lambda glyph: (-scores[glyph], DEFAULT_SIGNATURE_GLYPHS.index(glyph)))
    return tuple(ranked[:limit] or DEFAULT_SIGNATURE_GLYPHS[:limit])

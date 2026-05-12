from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from alienhand_ai.bitmap import Bitmap


SegmentName = str
SegmentPattern = frozenset[SegmentName]


class ReadoutKind(str, Enum):
    NUMERIC = "numeric"
    TEXT = "text"
    UNKNOWN = "unknown"


class DisplayGeometry(str, Enum):
    SEVEN_SEGMENT = "seven_segment"
    FOURTEEN_SEGMENT = "fourteen_segment"
    SIXTEEN_SEGMENT = "sixteen_segment"
    DOT_MATRIX = "dot_matrix"


@dataclass(frozen=True)
class SegmentBox:
    name: SegmentName
    left: int
    top: int
    right: int
    bottom: int


@dataclass(frozen=True)
class LcdGlyph:
    pattern: SegmentPattern
    char: str
    confidence: float
    alternatives: tuple[str, ...] = ()
    numeric_char: str | None = None
    text_only: bool = False


@dataclass(frozen=True)
class LcdToken:
    text: str
    kind: ReadoutKind
    glyphs: tuple[LcdGlyph, ...]


@dataclass(frozen=True)
class LcdReadout:
    text: str
    kind: ReadoutKind
    glyphs: tuple[LcdGlyph, ...]
    tokens: tuple[LcdToken, ...] = ()
    geometry: DisplayGeometry = DisplayGeometry.SEVEN_SEGMENT


NUMERIC_PATTERNS: dict[str, SegmentPattern] = {
    "0": frozenset("abcdef"),
    "1": frozenset("bc"),
    "2": frozenset("abdeg"),
    "3": frozenset("abcdg"),
    "4": frozenset("bcfg"),
    "5": frozenset("acdfg"),
    "6": frozenset("acdefg"),
    "7": frozenset("abc"),
    "8": frozenset("abcdefg"),
    "9": frozenset("abcdfg"),
    "-": frozenset("g"),
}

TEXT_PATTERNS: dict[str, SegmentPattern] = {
    "A": frozenset("abcefg"),
    "b": frozenset("cdefg"),
    "C": frozenset("adef"),
    "D": frozenset("bcdeg"),
    "d": frozenset("bcdeg"),
    "E": frozenset("adefg"),
    "F": frozenset("aefg"),
    "H": frozenset("bcefg"),
    "I": frozenset("bc"),
    "L": frozenset("def"),
    "n": frozenset("ceg"),
    "O": frozenset("abcdef"),
    "o": frozenset("cdeg"),
    "P": frozenset("abefg"),
    "r": frozenset("eg"),
    "S": frozenset("acdfg"),
    "t": frozenset("defg"),
    "U": frozenset("bcdef"),
    "Y": frozenset("bcdfg"),
}

SEGMENT_PATTERNS: dict[str, SegmentPattern] = {**NUMERIC_PATTERNS, **TEXT_PATTERNS}
TEXT_ONLY_PATTERNS = frozenset(set(TEXT_PATTERNS.values()) - set(NUMERIC_PATTERNS.values()))
UNRELIABLE_SEVEN_SEGMENT_LETTERS = frozenset("GJKMQTVWXZ")
DISPLAY_GEOMETRY_NOTES = {
    DisplayGeometry.SEVEN_SEGMENT: "Best for digits and a small ambiguous letter set.",
    DisplayGeometry.FOURTEEN_SEGMENT: "Adds diagonals and split strokes for readable alphabetic text.",
    DisplayGeometry.SIXTEEN_SEGMENT: "Adds more stroke combinations for improved alphabetic displays.",
    DisplayGeometry.DOT_MATRIX: "Dot-matrix displays are best handled as bitmap font/template matches before using trained OCR.",
}

PREFERRED_CHARS = {
    frozenset("abcdef"): "0",
    frozenset("bc"): "1",
    frozenset("acdfg"): "5",
}


def decode_patterns(patterns: list[set[str] | frozenset[str]], geometry: DisplayGeometry = DisplayGeometry.SEVEN_SEGMENT) -> LcdReadout:
    glyphs = tuple(decode_pattern(frozenset(pattern)) for pattern in patterns)
    tokens = tokenize_glyphs(glyphs)
    return LcdReadout(text="".join(glyph.char for glyph in glyphs), kind=classify_tokens(tokens), glyphs=glyphs, tokens=tokens, geometry=geometry)


def decode_pattern(pattern: SegmentPattern) -> LcdGlyph:
    if not pattern:
        return LcdGlyph(pattern=pattern, char=" ", confidence=1.0)
    numeric_char = _numeric_char_for(pattern)
    exact = tuple(char for char, known in SEGMENT_PATTERNS.items() if known == pattern)
    if pattern in PREFERRED_CHARS:
        char = PREFERRED_CHARS[pattern]
        return LcdGlyph(pattern=pattern, char=char, confidence=1.0, alternatives=exact, numeric_char=numeric_char, text_only=False)
    if exact:
        text_only = pattern in TEXT_ONLY_PATTERNS
        return LcdGlyph(pattern=pattern, char=exact[0], confidence=1.0, alternatives=exact, numeric_char=numeric_char, text_only=text_only)

    best_char = "?"
    best_score = -1.0
    best_alternatives: tuple[str, ...] = ()
    for char, known in SEGMENT_PATTERNS.items():
        union = pattern | known
        score = 1.0 if not union else len(pattern & known) / len(union)
        if score > best_score:
            best_char = char
            best_score = score
            best_alternatives = tuple(candidate for candidate, candidate_pattern in SEGMENT_PATTERNS.items() if candidate_pattern == known)
    return LcdGlyph(
        pattern=pattern,
        char=best_char,
        confidence=best_score,
        alternatives=best_alternatives,
        numeric_char=numeric_char,
        text_only=pattern in TEXT_ONLY_PATTERNS or numeric_char is None,
    )


def classify_readout(glyphs: tuple[LcdGlyph, ...]) -> ReadoutKind:
    visible = [glyph for glyph in glyphs if glyph.char.strip()]
    if not visible:
        return ReadoutKind.UNKNOWN
    if all(glyph.numeric_char is not None and not glyph.text_only for glyph in visible):
        return ReadoutKind.NUMERIC
    return ReadoutKind.TEXT


def tokenize_glyphs(glyphs: tuple[LcdGlyph, ...]) -> tuple[LcdToken, ...]:
    tokens: list[LcdToken] = []
    current: list[LcdGlyph] = []
    for glyph in glyphs:
        if glyph.char.strip():
            current.append(glyph)
        elif current:
            tokens.append(_token_from_glyphs(tuple(current)))
            current = []
    if current:
        tokens.append(_token_from_glyphs(tuple(current)))
    return tuple(tokens)


def classify_tokens(tokens: tuple[LcdToken, ...]) -> ReadoutKind:
    visible = [token for token in tokens if token.kind != ReadoutKind.UNKNOWN]
    if not visible:
        return ReadoutKind.UNKNOWN
    if all(token.kind == ReadoutKind.NUMERIC for token in visible):
        return ReadoutKind.NUMERIC
    return ReadoutKind.TEXT


def numeric_text(readout: LcdReadout) -> str | None:
    if readout.kind != ReadoutKind.NUMERIC:
        return None
    return "".join(glyph.numeric_char or glyph.char for glyph in readout.glyphs)


def token_numeric_text(token: LcdToken) -> str | None:
    if token.kind != ReadoutKind.NUMERIC:
        return None
    return "".join(glyph.numeric_char or glyph.char for glyph in token.glyphs)


def unsupported_letters_for_geometry(letters: str, geometry: DisplayGeometry = DisplayGeometry.SEVEN_SEGMENT) -> tuple[str, ...]:
    if geometry != DisplayGeometry.SEVEN_SEGMENT:
        return ()
    return tuple(dict.fromkeys(char.upper() for char in letters if char.upper() in UNRELIABLE_SEVEN_SEGMENT_LETTERS))


def geometry_note(geometry: DisplayGeometry) -> str:
    return DISPLAY_GEOMETRY_NOTES[geometry]


def read_segment_glyph(bitmap: Bitmap, boxes: tuple[SegmentBox, ...], threshold: int = 96) -> LcdGlyph:
    active = set()
    confidences = []
    for box in boxes:
        brightness = _average_brightness(bitmap, box)
        if brightness >= threshold:
            active.add(box.name)
        confidences.append(abs(brightness - threshold) / max(1, 255 - threshold))
    glyph = decode_pattern(frozenset(active))
    segment_confidence = sum(confidences) / max(1, len(confidences))
    return LcdGlyph(
        pattern=glyph.pattern,
        char=glyph.char,
        confidence=min(glyph.confidence, segment_confidence),
        alternatives=glyph.alternatives,
        numeric_char=glyph.numeric_char,
        text_only=glyph.text_only,
    )


def _numeric_char_for(pattern: SegmentPattern) -> str | None:
    for char, known in NUMERIC_PATTERNS.items():
        if known == pattern:
            return char
    return None


def _token_from_glyphs(glyphs: tuple[LcdGlyph, ...]) -> LcdToken:
    return LcdToken(text="".join(glyph.char for glyph in glyphs), kind=classify_readout(glyphs), glyphs=glyphs)


def _average_brightness(bitmap: Bitmap, box: SegmentBox) -> float:
    total = 0
    count = 0
    for y in range(max(0, box.top), min(bitmap.height, box.bottom)):
        for x in range(max(0, box.left), min(bitmap.width, box.right)):
            r, g, b = bitmap.rgb_at(x, y)
            total += max(r, g, b)
            count += 1
    return total / max(1, count)

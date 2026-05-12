from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
from time import time
from typing import Any

from .learning_packets import LearningPacket


JsonDict = dict[str, Any]


SUBJECTIVE_TERMS = {
    "acceptable",
    "bad",
    "better",
    "clean",
    "clear",
    "correct",
    "enough",
    "fast",
    "fine",
    "good",
    "look",
    "looks",
    "nice",
    "normal",
    "obvious",
    "proper",
    "properly",
    "ready",
    "reasonable",
    "responsive",
    "right",
    "safe",
    "slow",
    "stable",
    "strange",
    "weird",
    "wrong",
}

HEDGE_TERMS = {"appears", "maybe", "probably", "seems", "should", "usually"}

PRIMITIVE_KEYWORDS = {
    "observe": "observe",
    "watch": "observe",
    "check": "observe",
    "read": "observe",
    "wait": "wait_for",
    "if": "branch",
    "click": "click",
    "press": "key",
    "type": "text",
    "enter": "text",
    "open": "interface_action",
    "run": "interface_action",
    "save": "interface_action",
    "stop": "interface_action",
    "close": "interface_action",
    "call": "interface_route",
    "record": "record",
}


@dataclass(frozen=True)
class SOPStep:
    id: str
    source: str
    primitive: str
    status: str
    confidence: float
    args: JsonDict = field(default_factory=dict)
    unresolved_terms: tuple[str, ...] = ()
    protocols: tuple[str, ...] = ()

    def to_dict(self) -> JsonDict:
        return asdict(self)


@dataclass(frozen=True)
class SOPProgram:
    name: str
    source: str
    steps: tuple[SOPStep, ...]
    packets: tuple[LearningPacket, ...] = ()

    def to_dict(self) -> JsonDict:
        return {
            "schema_version": 1,
            "name": self.name,
            "source": self.source,
            "steps": [step.to_dict() for step in self.steps],
            "packets": [packet.to_dict() for packet in self.packets],
            "summary": summarize_sop(self),
        }

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return output


def compile_sop_text(text: str, name: str = "sop", source: str = "inline") -> SOPProgram:
    steps: list[SOPStep] = []
    packets: list[LearningPacket] = []
    for index, line in enumerate(_instruction_lines(text), start=1):
        step = _compile_line(line, index)
        steps.append(step)
        if step.status != "executable":
            packets.append(_packet_from_step(step, name=name, source=source))
    return SOPProgram(name=name, source=source, steps=tuple(steps), packets=tuple(packets))


def compile_sop_file(path: str | Path, name: str | None = None) -> SOPProgram:
    source = Path(path)
    return compile_sop_text(source.read_text(encoding="utf-8"), name=name or source.stem, source=str(source))


def summarize_sop(program: SOPProgram) -> JsonDict:
    by_status: dict[str, int] = {}
    by_primitive: dict[str, int] = {}
    unresolved_terms: dict[str, int] = {}
    for step in program.steps:
        by_status[step.status] = by_status.get(step.status, 0) + 1
        by_primitive[step.primitive] = by_primitive.get(step.primitive, 0) + 1
        for term in step.unresolved_terms:
            unresolved_terms[term] = unresolved_terms.get(term, 0) + 1
    return {
        "steps": len(program.steps),
        "packets": len(program.packets),
        "by_status": by_status,
        "by_primitive": by_primitive,
        "unresolved_terms": unresolved_terms,
    }


def write_sop_packets(program: SOPProgram, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as file:
        for packet in program.packets:
            file.write(json.dumps(packet.to_dict(), sort_keys=True))
            file.write("\n")
    return output


def _instruction_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\s*(?:[-*]|\d+[.)])\s*", "", line).strip()
        if line:
            lines.append(line)
    return lines


def _compile_line(line: str, index: int) -> SOPStep:
    tokens = _tokens(line)
    primitive = _primitive_for(line, tokens)
    unresolved = tuple(sorted((tokens & SUBJECTIVE_TERMS) | (tokens & HEDGE_TERMS)))
    args = _args_for(line, primitive)
    protocols = _protocols_for(primitive, unresolved)
    confidence = _confidence(primitive, unresolved, args)
    status = "executable" if primitive != "unknown" and not unresolved else "needs_digest"
    return SOPStep(
        id=f"sop_step_{index:03d}",
        source=line,
        primitive=primitive,
        status=status,
        confidence=confidence,
        args=args,
        unresolved_terms=unresolved,
        protocols=protocols,
    )


def _primitive_for(line: str, tokens: set[str]) -> str:
    if not tokens:
        return "unknown"
    words = re.sub(r"[^a-zA-Z0-9]+", " ", line).lower().split()
    first = words[0] if words else ""
    if first in PRIMITIVE_KEYWORDS:
        return PRIMITIVE_KEYWORDS[first]
    return next((PRIMITIVE_KEYWORDS[token] for token in words if token in PRIMITIVE_KEYWORDS), "unknown")


def _args_for(line: str, primitive: str) -> JsonDict:
    if primitive == "branch":
        match = re.match(r"if\s+(?P<condition>.+?)\s+then\s+(?P<action>.+)$", line, re.IGNORECASE)
        if match:
            return {"condition": match.group("condition").strip(), "then": match.group("action").strip()}
        return {"condition": re.sub(r"^if\s+", "", line, flags=re.IGNORECASE).strip()}
    if primitive == "wait_for":
        return {"condition": re.sub(r"^wait(?:\s+until|\s+for)?\s+", "", line, flags=re.IGNORECASE).strip()}
    if primitive == "click":
        return {"target": re.sub(r"^click\s+", "", line, flags=re.IGNORECASE).strip()}
    if primitive == "key":
        return {"key": re.sub(r"^press\s+", "", line, flags=re.IGNORECASE).strip()}
    if primitive == "text":
        return {"text": re.sub(r"^(?:type|enter)\s+", "", line, flags=re.IGNORECASE).strip()}
    if primitive == "interface_route":
        return {"route": re.sub(r"^call\s+", "", line, flags=re.IGNORECASE).strip()}
    if primitive == "interface_action":
        tokens = line.split(maxsplit=1)
        return {"action": tokens[0].lower(), "target": tokens[1].strip() if len(tokens) > 1 else ""}
    if primitive == "observe":
        return {"target": re.sub(r"^(?:observe|watch|check|read)\s+", "", line, flags=re.IGNORECASE).strip()}
    if primitive == "record":
        return {"target": re.sub(r"^record\s+", "", line, flags=re.IGNORECASE).strip()}
    return {"text": line}


def _protocols_for(primitive: str, unresolved: tuple[str, ...]) -> tuple[str, ...]:
    protocols = []
    if primitive == "unknown":
        protocols.append("primitive_selection")
    if unresolved:
        protocols.append("subjective_term_resolution")
        protocols.append("threshold_binding")
    if primitive in {"branch", "wait_for"}:
        protocols.append("condition_delineation")
    if primitive in {"click", "interface_action", "interface_route"}:
        protocols.append("interface_route_matching")
    return tuple(sorted(set(protocols)))


def _confidence(primitive: str, unresolved: tuple[str, ...], args: JsonDict) -> float:
    score = 0.95 if primitive != "unknown" else 0.25
    score -= min(0.50, 0.12 * len(unresolved))
    if not any(str(value).strip() for value in args.values()):
        score -= 0.20
    return round(max(0.0, min(1.0, score)), 3)


def _packet_from_step(step: SOPStep, name: str, source: str) -> LearningPacket:
    payload = {
        "schema_version": 1,
        "sop_name": name,
        "source": source,
        "step": step.to_dict(),
    }
    packet_id = _stable_packet_id(payload)
    return LearningPacket(
        id=packet_id,
        kind="sop_distillation",
        status="pending_llm_digest",
        created_at=time(),
        source_run=f"sop:{source}",
        tags=tuple(sorted({"sop", "pending_llm_digest", *step.protocols, *step.unresolved_terms})),
        payload=payload,
        digest={
            "schema_version": 1,
            "needs": [
                "primitive_selection",
                "subjective_term_resolution",
                "threshold_binding",
                "decision_tree_confirmation",
            ],
        },
    )


def _tokens(value: str) -> set[str]:
    return {chunk for chunk in re.sub(r"[^a-zA-Z0-9]+", " ", value).lower().split() if chunk}


def _stable_packet_id(payload: JsonDict) -> str:
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return "sop_" + hashlib.sha1(encoded).hexdigest()[:16]

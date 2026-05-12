from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Iterable


AI_RELEVANT_CLASSES = {
    "EditorInterface",
    "EditorPlugin",
    "EditorScript",
    "EditorCommandPalette",
    "EditorFileSystem",
    "EditorSelection",
    "EditorUndoRedoManager",
    "SceneTree",
    "Node",
    "Input",
    "InputMap",
    "ProjectSettings",
    "ResourceLoader",
    "ResourceSaver",
    "Engine",
    "Performance",
    "RenderingServer",
    "PhysicsServer2D",
    "PhysicsServer3D",
    "DisplayServer",
    "OS",
}

AI_METHOD_KEYWORDS = {
    "add",
    "call",
    "close",
    "create",
    "debug",
    "edit",
    "emit",
    "execute",
    "find",
    "get",
    "inspect",
    "load",
    "open",
    "play",
    "print",
    "quit",
    "record",
    "reload",
    "remove",
    "run",
    "save",
    "select",
    "set",
    "snapshot",
    "stop",
}

INTENT_RULES = {
    "observe": {
        "get",
        "has",
        "inspect",
        "is",
        "list",
        "performance",
        "profile",
        "select",
        "snapshot",
        "watch",
    },
    "control": {
        "action",
        "add",
        "call",
        "close",
        "create",
        "edit",
        "erase",
        "execute",
        "open",
        "play",
        "remove",
        "run",
        "save",
        "select",
        "set",
        "stop",
    },
    "debug": {"break", "debug", "inspect", "performance", "profile", "step", "trace"},
    "input": {"action", "input", "key", "mouse", "shortcut"},
    "project": {"autoload", "file", "filesystem", "folder", "path", "project", "resource", "scene", "script"},
    "lifecycle": {"close", "quit", "reload", "restart", "run", "play", "stop"},
    "telemetry": {"event", "frame", "log", "record", "recording", "session", "telemetry"},
    "ui": {"dialog", "dock", "focus", "inspector", "menu", "popup", "shortcut", "ui", "window"},
}

TOKEN_ALIASES = {
    "animation": "animate",
    "animations": "animate",
    "collisions": "collision",
    "continue": "play",
    "current": "scene",
    "debugger": "debug",
    "files": "file",
    "filesystem": "file",
    "folders": "folder",
    "keys": "key",
    "libraries": "library",
    "loading": "load",
    "main": "project",
    "opened": "open",
    "opening": "open",
    "paths": "path",
    "playing": "play",
    "project": "project",
    "resources": "resource",
    "run": "play",
    "running": "play",
    "scenes": "scene",
    "scripts": "script",
    "selected": "select",
    "selection": "select",
    "settings": "setting",
    "shortcuts": "shortcut",
    "stopped": "stop",
    "stopping": "stop",
}

TOKEN_STOPWORDS = {
    "a",
    "all",
    "and",
    "as",
    "editor",
    "from",
    "godot",
    "in",
    "interface",
    "of",
    "on",
    "the",
    "to",
    "with",
}

STRONG_EQUIVALENCE_TERMS = {
    "add",
    "break",
    "call",
    "close",
    "command",
    "copy",
    "create",
    "cut",
    "debug",
    "delete",
    "duplicate",
    "edit",
    "erase",
    "inspect",
    "load",
    "open",
    "paste",
    "play",
    "quit",
    "record",
    "reload",
    "remove",
    "restart",
    "save",
    "select",
    "set",
    "stop",
}

WEAK_EQUIVALENCE_TERMS = {
    "all",
    "canvas",
    "down",
    "first",
    "focus",
    "group",
    "item",
    "last",
    "left",
    "line",
    "mode",
    "next",
    "node",
    "panel",
    "path",
    "previous",
    "prev",
    "right",
    "scene",
    "show",
    "toggle",
    "transform",
    "tree",
    "up",
    "viewport",
}

UI_SHORTCUT_RE = re.compile(
    r'ED_SHORTCUT\(\s*"(?P<id>[^"]+)"\s*,\s*(?:TTRC?|RTR)\("(?P<label>[^"]+)"\)',
    re.MULTILINE,
)
UI_SHORTCUT_USE_RE = re.compile(r'ED_IS_SHORTCUT\(\s*"(?P<id>[^"]+)"')


@dataclass(frozen=True)
class InterfaceEntry:
    id: str
    surface: str
    kind: str
    name: str
    source: str
    path: str | None = None
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class InterfaceEquivalence:
    left_id: str
    right_id: str
    confidence: float
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class InterfaceGuidance:
    id: str
    route: str
    title: str
    preferred_entry_id: str | None
    alternate_entry_ids: tuple[str, ...] = ()
    confidence: float = 1.0
    intent_tags: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class InterfaceMap:
    entries: tuple[InterfaceEntry, ...]
    equivalences: tuple[InterfaceEquivalence, ...]
    guidance: tuple[InterfaceGuidance, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "entries": [entry.to_dict() for entry in self.entries],
            "equivalences": [equivalence.to_dict() for equivalence in self.equivalences],
            "guidance": [item.to_dict() for item in self.guidance],
        }

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return output


def probe_godot_interfaces(root: str | Path) -> InterfaceMap:
    root_path = Path(root)
    entries: list[InterfaceEntry] = []
    entries.extend(_probe_godot_doc_classes(root_path))
    entries.extend(_probe_godot_editor_shortcuts(root_path))
    entries.extend(_probe_godot_projects(root_path))
    entries.extend(_probe_alienhand_module(root_path))
    entries = [_with_intent_metadata(entry) for entry in entries]
    equivalences = _build_equivalences(entries)
    return InterfaceMap(
        entries=tuple(entries),
        equivalences=tuple(equivalences),
        guidance=tuple(_build_guidance(entries, equivalences)),
    )


def summarize_interface_map(interface_map: InterfaceMap) -> dict[str, object]:
    by_surface: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_route: dict[str, int] = {}
    for entry in interface_map.entries:
        by_surface[entry.surface] = by_surface.get(entry.surface, 0) + 1
        by_kind[entry.kind] = by_kind.get(entry.kind, 0) + 1
    for guidance in interface_map.guidance:
        by_route[guidance.route] = by_route.get(guidance.route, 0) + 1
    return {
        "entries": len(interface_map.entries),
        "equivalences": len(interface_map.equivalences),
        "guidance": len(interface_map.guidance),
        "by_surface": by_surface,
        "by_kind": by_kind,
        "by_route": by_route,
    }


def _probe_godot_doc_classes(root: Path) -> list[InterfaceEntry]:
    docs = root / "doc" / "classes"
    entries: list[InterfaceEntry] = []
    if not docs.exists():
        return entries
    for xml_path in docs.glob("*.xml"):
        try:
            tree = ET.parse(xml_path)
        except ET.ParseError:
            continue
        class_node = tree.getroot()
        class_name = class_node.attrib.get("name", xml_path.stem)
        if class_name not in AI_RELEVANT_CLASSES:
            continue
        for method in class_node.findall("./methods/method"):
            method_name = method.attrib.get("name", "")
            if not method_name:
                continue
            tokens = _tokens(method_name)
            if class_name.startswith("Editor") or tokens & AI_METHOD_KEYWORDS:
                entries.append(
                    InterfaceEntry(
                        id=f"godot.api.{class_name}.{method_name}",
                        surface="ai_api",
                        kind="class_method",
                        name=f"{class_name}.{method_name}",
                        source="godot_doc_classes",
                        path=str(xml_path),
                        metadata={
                            "class": class_name,
                            "method": method_name,
                            "returns": _method_return(method),
                            "params": _method_params(method),
                            "description": _clean_text(method.findtext("description", "")),
                        },
                    )
                )
        for signal in class_node.findall("./signals/signal"):
            signal_name = signal.attrib.get("name", "")
            if not signal_name:
                continue
            entries.append(
                InterfaceEntry(
                    id=f"godot.api.{class_name}.{signal_name}",
                    surface="ai_api",
                    kind="class_signal",
                    name=f"{class_name}.{signal_name}",
                    source="godot_doc_classes",
                    path=str(xml_path),
                    metadata={
                        "class": class_name,
                        "signal": signal_name,
                        "params": _signal_params(signal),
                        "description": _clean_text(signal.findtext("description", "")),
                    },
                )
            )
    return entries


def _probe_godot_editor_shortcuts(root: Path) -> list[InterfaceEntry]:
    editor = root / "editor"
    entries_by_id: dict[str, InterfaceEntry] = {}
    if not editor.exists():
        return []
    for source_path in _iter_source_files(editor):
        text = source_path.read_text(encoding="utf-8", errors="ignore")
        for match in UI_SHORTCUT_RE.finditer(text):
            shortcut_id = match.group("id")
            label = match.group("label")
            entries_by_id[shortcut_id] = InterfaceEntry(
                id=f"godot.ui.shortcut.{shortcut_id}",
                surface="ui_action",
                kind="editor_shortcut",
                name=label,
                source="godot_editor_source",
                path=str(source_path),
                metadata={"shortcut_id": shortcut_id, "used": False},
            )
        for match in UI_SHORTCUT_USE_RE.finditer(text):
            shortcut_id = match.group("id")
            if shortcut_id not in entries_by_id:
                entries_by_id[shortcut_id] = InterfaceEntry(
                    id=f"godot.ui.shortcut.{shortcut_id}",
                    surface="ui_action",
                    kind="editor_shortcut_use",
                    name=shortcut_id,
                    source="godot_editor_source",
                    path=str(source_path),
                    metadata={"shortcut_id": shortcut_id, "used": True},
                )
    return sorted(entries_by_id.values(), key=lambda entry: entry.id)


def _probe_godot_projects(root: Path) -> list[InterfaceEntry]:
    entries: list[InterfaceEntry] = []
    for project_path in root.rglob("project.godot"):
        relative = project_path.relative_to(root)
        entries.append(
            InterfaceEntry(
                id=f"godot.project.{_stable_path_id(relative)}",
                surface="project_config",
                kind="project_file",
                name=str(relative),
                source="godot_project",
                path=str(project_path),
                metadata={"root": str(project_path.parent)},
            )
        )
        entries.extend(_probe_project_file(root, project_path))
    return entries


def _probe_project_file(root: Path, project_path: Path) -> list[InterfaceEntry]:
    entries: list[InterfaceEntry] = []
    try:
        text = project_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return entries

    relative = project_path.relative_to(root)
    project_id = _stable_path_id(relative)
    section = ""
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith(";"):
            continue
        section_match = re.match(r"\[(?P<section>[^\]]+)\]", line)
        if section_match:
            section = section_match.group("section").strip().lower()
            continue
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip().strip('"')
        if not key:
            continue
        if section == "input":
            entries.append(
                InterfaceEntry(
                    id=f"godot.project.{project_id}.input.{_stable_path_id(Path(key))}",
                    surface="project_input",
                    kind="input_action",
                    name=key,
                    source="godot_project",
                    path=str(project_path),
                    metadata={"project": str(relative), "action": key},
                )
            )
        elif section == "autoload":
            entries.append(
                InterfaceEntry(
                    id=f"godot.project.{project_id}.autoload.{_stable_path_id(Path(key))}",
                    surface="project_config",
                    kind="autoload",
                    name=key,
                    source="godot_project",
                    path=str(project_path),
                    metadata={"project": str(relative), "autoload": key},
                )
            )
    return entries


def _probe_alienhand_module(root: Path) -> list[InterfaceEntry]:
    module = root / "modules" / "alienhand_agent"
    if not module.exists():
        return []
    entries = [
        InterfaceEntry(
            id="godot.ai.alienhand_agent.module",
            surface="ai_bridge",
            kind="engine_module",
            name="AlienHandAgent",
            source="alienhand_agent_module",
            path=str(module),
            metadata={
                "capabilities": [
                    "record_frame",
                    "log_event",
                    "start_recording",
                    "stop_recording",
                ]
            },
        )
    ]
    header = module / "alienhand_agent.h"
    if header.exists():
        text = header.read_text(encoding="utf-8", errors="ignore")
        for method in _public_cpp_methods(text):
            if method.startswith("_"):
                continue
            entries.append(
                InterfaceEntry(
                    id=f"godot.ai.alienhand_agent.{method}",
                    surface="ai_bridge",
                    kind="module_method",
                    name=f"AlienHandAgent.{method}",
                    source="alienhand_agent_module",
                    path=str(header),
                    metadata={"method": method},
                )
            )
    return entries


def _public_cpp_methods(header_text: str) -> list[str]:
    methods: list[str] = []
    in_public_section = False
    for line in header_text.splitlines():
        stripped = line.strip()
        if stripped == "public:":
            in_public_section = True
            continue
        if stripped in {"private:", "protected:"}:
            in_public_section = False
            continue
        if not in_public_section:
            continue
        match = re.match(r"\b(?:Error|void|bool|String|int|Dictionary)\s+([a-zA-Z_][a-zA-Z0-9_]*)\(", stripped)
        if match:
            methods.append(match.group(1))
    return methods


def _build_equivalences(entries: list[InterfaceEntry]) -> list[InterfaceEquivalence]:
    api_entries = [entry for entry in entries if entry.surface in {"ai_api", "ai_bridge"}]
    ui_entries = [entry for entry in entries if entry.surface == "ui_action"]
    equivalences: list[InterfaceEquivalence] = []
    for ui_entry in ui_entries:
        ui_terms = _entry_match_terms(ui_entry)
        best: tuple[float, InterfaceEntry] | None = None
        for api_entry in api_entries:
            api_terms = _entry_match_terms(api_entry)
            score = _term_score(ui_terms, api_terms)
            if score >= 0.6 and (best is None or score > best[0]):
                best = (score, api_entry)
        if best is not None:
            equivalences.append(
                InterfaceEquivalence(
                    left_id=ui_entry.id,
                    right_id=best[1].id,
                    confidence=round(best[0], 3),
                    reason="overlapping normalized action terms",
                )
            )
    return sorted(equivalences, key=lambda item: (-item.confidence, item.left_id, item.right_id))


def _build_guidance(entries: list[InterfaceEntry], equivalences: list[InterfaceEquivalence]) -> list[InterfaceGuidance]:
    entries_by_id = {entry.id: entry for entry in entries}
    paired_ui_ids = {equivalence.left_id for equivalence in equivalences}
    paired_api_ids = {equivalence.right_id for equivalence in equivalences}
    guidance: list[InterfaceGuidance] = []

    for equivalence in equivalences:
        ui_entry = entries_by_id[equivalence.left_id]
        api_entry = entries_by_id[equivalence.right_id]
        intent_tags = tuple(sorted(_entry_intent_tags(ui_entry) | _entry_intent_tags(api_entry)))
        guidance.append(
            InterfaceGuidance(
                id=f"route.{_stable_id(ui_entry.id)}.{_stable_id(api_entry.id)}",
                route="api_and_ui",
                title=f"{ui_entry.name} -> {api_entry.name}",
                preferred_entry_id=api_entry.id,
                alternate_entry_ids=(ui_entry.id,),
                confidence=equivalence.confidence,
                intent_tags=intent_tags,
                reason="Prefer the Godot/AlienHand API for automation; use the UI action as the human-facing equivalent or fallback.",
            )
        )

    for entry in entries:
        if entry.id in paired_api_ids or entry.id in paired_ui_ids:
            continue
        intent_tags = tuple(sorted(_entry_intent_tags(entry)))
        if entry.surface == "ai_bridge":
            guidance.append(
                InterfaceGuidance(
                    id=f"route.{_stable_id(entry.id)}",
                    route="ai_bridge",
                    title=entry.name,
                    preferred_entry_id=entry.id,
                    confidence=1.0,
                    intent_tags=intent_tags,
                    reason="Direct AlienHand integration hook exposed inside the Godot fork.",
                )
            )
        elif entry.surface == "ai_api" and _is_guidance_worthy(entry):
            guidance.append(
                InterfaceGuidance(
                    id=f"route.{_stable_id(entry.id)}",
                    route="ai_api",
                    title=entry.name,
                    preferred_entry_id=entry.id,
                    confidence=0.8,
                    intent_tags=intent_tags,
                    reason="Scriptable Godot API entry point suitable for editor automation or inspection.",
                )
            )
        elif entry.surface == "ui_action" and _is_guidance_worthy(entry):
            guidance.append(
                InterfaceGuidance(
                    id=f"route.{_stable_id(entry.id)}",
                    route="ui_only",
                    title=entry.name,
                    preferred_entry_id=entry.id,
                    confidence=0.6,
                    intent_tags=intent_tags,
                    reason="Known editor UI action without a confident API equivalent yet.",
                )
            )
        elif entry.surface in {"project_config", "project_input"}:
            guidance.append(
                InterfaceGuidance(
                    id=f"route.{_stable_id(entry.id)}",
                    route=entry.surface,
                    title=entry.name,
                    preferred_entry_id=entry.id,
                    confidence=0.7,
                    intent_tags=intent_tags,
                    reason="Project-level surface AlienHand can inspect to understand how this Godot project is wired.",
                )
            )

    route_priority = {
        "api_and_ui": 0,
        "ai_bridge": 1,
        "project_config": 2,
        "project_input": 3,
        "ai_api": 4,
        "ui_only": 5,
    }
    return sorted(guidance, key=lambda item: (route_priority.get(item.route, 99), -item.confidence, item.title))


def _iter_source_files(root: Path) -> Iterable[Path]:
    for suffix in ("*.cpp", "*.h", "*.hpp"):
        yield from root.rglob(suffix)


def _method_return(method: ET.Element) -> str | None:
    return_node = method.find("return")
    return return_node.attrib.get("type") if return_node is not None else None


def _method_params(method: ET.Element) -> list[dict[str, str]]:
    params = []
    for param in method.findall("param"):
        params.append({key: value for key, value in param.attrib.items()})
    return params


def _signal_params(signal: ET.Element) -> list[dict[str, str]]:
    params = []
    for param in signal.findall("param"):
        params.append({key: value for key, value in param.attrib.items()})
    return params


def _clean_text(text: str) -> str:
    return " ".join(text.split())


def _with_intent_metadata(entry: InterfaceEntry) -> InterfaceEntry:
    intent_tags = sorted(_intent_tags(_entry_terms(entry)))
    if not intent_tags:
        return entry
    metadata = dict(entry.metadata)
    metadata["intent_tags"] = intent_tags
    return InterfaceEntry(
        id=entry.id,
        surface=entry.surface,
        kind=entry.kind,
        name=entry.name,
        source=entry.source,
        path=entry.path,
        metadata=metadata,
    )


def _entry_intent_tags(entry: InterfaceEntry) -> set[str]:
    value = entry.metadata.get("intent_tags")
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return _intent_tags(_entry_terms(entry))


def _intent_tags(terms: set[str]) -> set[str]:
    return {tag for tag, keywords in INTENT_RULES.items() if terms & keywords}


def _is_guidance_worthy(entry: InterfaceEntry) -> bool:
    tags = _entry_intent_tags(entry)
    return bool(tags & {"control", "debug", "input", "lifecycle", "observe", "project", "telemetry"})


def _entry_terms(entry: InterfaceEntry) -> set[str]:
    terms = _tokens(entry.name)
    metadata = entry.metadata
    for key in ("action", "autoload", "class", "method", "project", "shortcut_id", "signal"):
        value = metadata.get(key)
        if isinstance(value, str):
            terms |= _tokens(value)
    return terms


def _entry_match_terms(entry: InterfaceEntry) -> set[str]:
    metadata = entry.metadata
    if entry.surface in {"ai_api", "ai_bridge"}:
        terms: set[str] = set()
        for key in ("method", "signal"):
            value = metadata.get(key)
            if isinstance(value, str):
                terms |= _tokens(value)
        if not terms:
            terms |= _tokens(entry.name.split(".")[-1])
        return terms
    if entry.surface == "ui_action":
        terms = _tokens(entry.name)
        shortcut_id = metadata.get("shortcut_id")
        if isinstance(shortcut_id, str):
            terms |= _tokens(shortcut_id)
        return terms
    return _entry_terms(entry)


def _tokens(value: str) -> set[str]:
    chunks = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    chunks = re.sub(r"[^a-zA-Z0-9]+", " ", chunks).lower()
    terms = set()
    for chunk in chunks.split():
        if not chunk or chunk in TOKEN_STOPWORDS:
            continue
        terms.add(TOKEN_ALIASES.get(chunk, chunk))
    return terms


def _term_score(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    intersection = left & right
    if not intersection:
        return 0.0
    if intersection <= WEAK_EQUIVALENCE_TERMS:
        return 0.0
    if len(intersection) == 1 and not (intersection & STRONG_EQUIVALENCE_TERMS):
        return 0.0
    if len(intersection) == 1 and min(len(left), len(right)) == 1 and max(len(left), len(right)) > 1:
        return 0.55
    union = left | right
    jaccard = len(intersection) / len(union)
    overlap = len(intersection) / min(len(left), len(right))
    score = max(jaccard, overlap * 0.9)
    missing_left_verbs = (left & STRONG_EQUIVALENCE_TERMS) - right
    if missing_left_verbs:
        score -= 0.2 * len(missing_left_verbs)
    return max(0.0, score)


def _stable_path_id(path: Path) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", ".", str(path)).strip(".").lower()


def _stable_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", ".", value).strip(".").lower()

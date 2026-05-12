from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from time import time


@dataclass(frozen=True)
class ReplayEvent:
    observation: dict
    action: dict | None
    outcome: dict | None
    timestamp: float = 0.0


class ReplayLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, event: ReplayEvent) -> None:
        payload = asdict(event)
        if payload["timestamp"] == 0.0:
            payload["timestamp"] = time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, sort_keys=True))
            file.write("\n")

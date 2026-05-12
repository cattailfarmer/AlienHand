from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LearningProfile:
    name: str
    mechanics_sensitivity: float
    pattern_sensitivity: float
    reward_sensitivity: float
    backpropagation_rate: float
    exploration_rate: float
    memory_decay: float
    notes: str = ""

    def to_dict(self) -> dict[str, float | str]:
        return asdict(self)


PROFILES: dict[str, LearningProfile] = {
    "observer": LearningProfile(
        name="observer",
        mechanics_sensitivity=0.25,
        pattern_sensitivity=0.35,
        reward_sensitivity=0.10,
        backpropagation_rate=0.0001,
        exploration_rate=0.00,
        memory_decay=0.01,
        notes="Conservative profile for watching and logging without changing behavior quickly.",
    ),
    "novice": LearningProfile(
        name="novice",
        mechanics_sensitivity=0.75,
        pattern_sensitivity=0.65,
        reward_sensitivity=0.55,
        backpropagation_rate=0.0005,
        exploration_rate=0.20,
        memory_decay=0.03,
        notes="Learns cautiously while still exploring enough to discover basic mechanics.",
    ),
    "steady": LearningProfile(
        name="steady",
        mechanics_sensitivity=1.00,
        pattern_sensitivity=1.00,
        reward_sensitivity=0.85,
        backpropagation_rate=0.0010,
        exploration_rate=0.10,
        memory_decay=0.02,
        notes="Balanced default for normal training runs.",
    ),
    "expert": LearningProfile(
        name="expert",
        mechanics_sensitivity=0.55,
        pattern_sensitivity=1.20,
        reward_sensitivity=0.70,
        backpropagation_rate=0.0003,
        exploration_rate=0.03,
        memory_decay=0.005,
        notes="Preserves learned mechanics and makes smaller policy updates.",
    ),
    "experimental": LearningProfile(
        name="experimental",
        mechanics_sensitivity=1.50,
        pattern_sensitivity=1.35,
        reward_sensitivity=1.15,
        backpropagation_rate=0.0030,
        exploration_rate=0.35,
        memory_decay=0.08,
        notes="Aggressive profile for fast adaptation and high variance experiments.",
    ),
}


def get_learning_profile(name: str) -> LearningProfile:
    key = name.strip().lower()
    if key not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise KeyError(f"Unknown learning profile {name!r}. Known profiles: {known}")
    return PROFILES[key]


def list_learning_profiles() -> tuple[LearningProfile, ...]:
    return tuple(PROFILES[name] for name in sorted(PROFILES))

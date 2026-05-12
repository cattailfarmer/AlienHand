from __future__ import annotations

from alienhand_ai.transformer import MissingTorchError, build_grid_policy_model


def build_policy_model(rows: int, cols: int, cell_types: int = 12):
    return build_grid_policy_model(sequence_length=rows * cols, token_types=cell_types, action_types=3)

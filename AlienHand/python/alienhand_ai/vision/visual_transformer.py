from __future__ import annotations

from alienhand_ai.transformer import MissingTorchError


def build_object_sequence_model(max_objects: int = 32, feature_size: int = 8, action_types: int = 8):
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise MissingTorchError("Install the optional ml dependencies to train the visual object transformer") from exc

    class ObjectSequencePolicy(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            hidden = 96
            self.project = nn.Linear(feature_size, hidden)
            self.position = nn.Parameter(torch.zeros(max_objects, hidden))
            layer = nn.TransformerEncoderLayer(d_model=hidden, nhead=4, batch_first=True)
            self.encoder = nn.TransformerEncoder(layer, num_layers=3)
            self.object_head = nn.Linear(hidden, 1)
            self.action_head = nn.Linear(hidden, action_types)

        def forward(self, object_features):
            x = self.project(object_features) + self.position
            encoded = self.encoder(x)
            object_logits = self.object_head(encoded).squeeze(-1)
            scene = encoded.mean(dim=1)
            action_logits = self.action_head(scene)
            return {"object_logits": object_logits, "action_logits": action_logits}

    return ObjectSequencePolicy()

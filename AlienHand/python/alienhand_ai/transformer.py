from __future__ import annotations


class MissingTorchError(RuntimeError):
    pass


def build_grid_policy_model(sequence_length: int, token_types: int, action_types: int = 3):
    try:
        import torch
        from torch import nn
    except ImportError as exc:
        raise MissingTorchError("Install the optional ml dependencies to train the transformer policy") from exc

    class GridPolicy(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            hidden = 64
            self.embed = nn.Embedding(token_types, hidden)
            self.position = nn.Parameter(torch.zeros(sequence_length, hidden))
            layer = nn.TransformerEncoderLayer(d_model=hidden, nhead=4, batch_first=True)
            self.encoder = nn.TransformerEncoder(layer, num_layers=2)
            self.head = nn.Linear(hidden, action_types)

        def forward(self, tokens):
            x = self.embed(tokens) + self.position
            x = self.encoder(x)
            return self.head(x)

    return GridPolicy()

import torch
import torch.nn as nn


class SmartQueueRanker(nn.Module):
    def __init__(self, user_feat_dim: int = 32, song_feat_dim: int = 32):
        super().__init__()
        input_dim = user_feat_dim + song_feat_dim
        self.net = nn.Sequential(
            nn.Linear(input_dim, 128), nn.ReLU(),
            nn.Linear(128, 64),        nn.ReLU(),
            nn.Linear(64, 1),          nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x).squeeze(-1)

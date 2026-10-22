#!/usr/bin/env python3
# leak_classifier.py
# Tiny 1D CNN: clean vs split-contaminated 64-bar windows of close prices.
#
# The task mirrors the repo's planted crime: a 4:1 split injected mid-window
# with no back-adjustment, so the close series drops 75 percent in one bar
# and stays down. The demo file's own forensic layer catches that split
# (split_caught: true in leak_demo.json). This model is a second opinion:
# same input shape, learned instead of rule-based.
#
# Train: 2 epochs, Adam, BCEWithLogitsLoss, CPU. Then export ONNX with a
# dynamic batch axis for the gallery.
#
# Run: python3 leak_classifier.py

import os
import time

import torch
import torch.nn as nn

torch.manual_seed(0)

BARS = 64
N_TRAIN = 4096
N_TEST = 2048
SPLIT_FACTOR = 4.0
EPOCHS = 2
BATCH = 64
LR = 1e-2

OUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "leak_classifier.onnx"
)


def synth_windows(n: int, gen: torch.Generator) -> tuple[torch.Tensor, torch.Tensor]:
    """Geometric random walk windows, half contaminated.

    Every window is log-price from cumulative gaussian returns at 1 percent
    per bar. Even-indexed windows take a 4:1 split at a random bar near the
    middle: log price drops by log(4), no adjustment, the level just falls
    and stays down. Per-window z-scoring does not erase that, because it is
    a step, not a scale.
    """
    xs = torch.empty(n, 1, BARS)
    ys = torch.empty(n)
    for i in range(n):
        rets = torch.randn(BARS, generator=gen) * 0.01
        logp = torch.cumsum(rets, dim=0)
        label = 1.0 if i % 2 == 0 else 0.0
        if label == 1.0:
            cut = BARS // 2 + int(torch.randint(-8, 9, (1,), generator=gen))
            logp[cut:] -= float(torch.log(torch.tensor(SPLIT_FACTOR)))
        z = (logp - logp.mean()) / logp.std().clamp_min(1e-6)
        xs[i, 0] = z
        ys[i] = label
    return xs, ys


class SplitNet(nn.Module):
    """3 conv layers, widths 8/8/4, ReLU, global average pool, linear head."""

    def __init__(self) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv1d(1, 8, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.Conv1d(8, 8, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.Conv1d(8, 4, kernel_size=5, padding=2),
            nn.ReLU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.head = nn.Linear(4, 1)
        # Head starts at zero. The pooled feature gap between the two classes
        # is already large at random init, so the head only has to learn a
        # sign and a scale. With a random head the initial logits sit far
        # from zero and 128 Adam steps at any sane lr cannot walk them back.
        nn.init.zeros_(self.head.weight)
        nn.init.zeros_(self.head.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        z = self.features(x).squeeze(-1)
        return self.head(z)


def main() -> None:
    t0 = time.time()
    gen = torch.Generator().manual_seed(0)

    x_train, y_train = synth_windows(N_TRAIN, gen)
    x_test, y_test = synth_windows(N_TEST, gen)
    print(f"data: {N_TRAIN} train, {N_TEST} test windows, {BARS} bars")

    model = SplitNet()
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    loss_fn = nn.BCEWithLogitsLoss()

    for epoch in range(EPOCHS):
        model.train()
        perm = torch.randperm(N_TRAIN, generator=gen)
        total = 0.0
        for lo in range(0, N_TRAIN, BATCH):
            idx = perm[lo : lo + BATCH]
            logits = model(x_train[idx]).squeeze(-1)
            loss = loss_fn(logits, y_train[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item() * idx.numel()
        print(f"epoch {epoch + 1}/{EPOCHS}  loss {total / N_TRAIN:.4f}")

    model.eval()
    with torch.no_grad():
        pred = (model(x_test).squeeze(-1) > 0).float()
    acc = (pred == y_test).float().mean().item()
    print(f"test accuracy: {acc:.4f}")

    # ONNX export, dynamic batch axis. The legacy TorchScript exporter is
    # used on purpose. torch 2.13's exporter serializes the ModelProto with
    # its own C++ code, then asks the onnx package to insert custom
    # onnx-script functions for custom ops. This graph has no custom ops,
    # so if the onnx package is absent the insertion step is bypassed and
    # the same bytes are written directly. Same exporter, minus an optional
    # post-pass.
    model.eval()
    dummy = torch.randn(1, 1, BARS)
    export_kwargs = dict(
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=17,
        dynamo=False,
    )
    try:
        torch.onnx.export(model, dummy, OUT_PATH, **export_kwargs)
        how = "torch.onnx.export"
    except Exception as exc:
        print(f"standard export unavailable: {type(exc).__name__}")
        from torch.onnx._internal.torchscript_exporter import onnx_proto_utils

        onnx_proto_utils._add_onnxscript_fn = (
            lambda model_bytes, custom_opsets: model_bytes
        )
        torch.onnx.export(model, dummy, OUT_PATH, **export_kwargs)
        how = "torch exporter, custom-op pass bypassed"
    size_kb = os.path.getsize(OUT_PATH) / 1024.0
    print(f"exported {OUT_PATH} via {how} ({size_kb:.1f} KB)")
    print(f"done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()

"""Render the repo's HD graphics from the real demo data. No dependencies.

    python tools/graphics.py [--frames-only]

Produces, into docs/graphics/:
    hero.png        1920x1080 chart: both equity curves, leak band, verdicts
    scene3d.png     1920x1080 software-3D render: ribbons over the leak plane
    frames/f_*.png  1080p animation frames (12 s, 30 fps)

ffmpeg (already on this box) then builds the mp4 and the GIF:
    ffmpeg -framerate 30 -i frames/f_%04d.png -i <audio> -c:v libx264 ...
    ffmpeg -i frames -vf fps=12,scale=800:-1,split,palettegen,paletteuse ...

Everything is painted with numpy onto a 2x supersampled canvas and
downsampled for anti-aliasing. Text uses a hand-set 5x7 bitmap font, so the
renders carry real labels with zero font dependencies. Stdlib + numpy only.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import zlib

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
DATA = os.path.join(REPO, "docs", "data", "leak_demo.json")
OUT = os.path.join(REPO, "docs", "graphics")

CANVAS_BG = (11, 12, 16)
HAIR = (42, 45, 58)
TEXT = (242, 243, 247)
MUTED = (138, 143, 163)
GHOST = (74, 78, 99)
LAVENDER = (208, 196, 223)
LAVENDER_DEEP = (112, 94, 177)
CRIMSON = (229, 72, 77)
SULFUR = (230, 180, 85)

FONT = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11100 10010 10001 10001 10001 10010 11100",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 11011 10001", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    ".": "00000 00000 00000 00000 00000 01100 01100", ",": "00000 00000 00000 00000 01100 01100 01000",
    ":": "00000 01100 01100 00000 01100 01100 00000", "+": "00000 00100 00100 11111 00100 00100 00000",
    "-": "00000 00000 00000 11111 00000 00000 00000", "/": "00001 00010 00010 00100 01000 01000 10000",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "%": "11001 11010 00010 00100 01000 01011 10011", "·": "00000 00000 01100 01100 00000 00000 00000",
    " ": "00000 00000 00000 00000 00000 00000 00000",
}


def write_png(path, arr):
    h, w, _ = arr.shape
    raw = b"".join(b"\x00" + arr[y].tobytes() for y in range(h))

    def chunk(tag, data):
        c = tag + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


class Painter:
    """Supersampled float canvas, coverage-based compositing."""

    def __init__(self, w, h, ss=2):
        self.w, self.h, self.ss = w, h, ss
        self.W, self.H = w * ss, h * ss
        self.img = np.zeros((self.H, self.W, 3), np.float32)
        self.img[:, :] = CANVAS_BG

    def rect(self, x, y, w, h, color, alpha=1.0):
        X, Y, Wd, Hd = int(x * self.ss), int(y * self.ss), int(w * self.ss), int(h * self.ss)
        a = np.float32(alpha)
        self.img[Y:Y + Hd, X:X + Wd] = self.img[Y:Y + Hd, X:X + Wd] * (1 - a) + np.array(color, np.float32) * a

    def hline(self, x0, x1, y, color, alpha=1.0, width=1.0):
        self.line(x0, y, x1, y, color, alpha, width)

    def _cov_discs(self, pts, radius):
        """Accumulate capsule coverage from densely spaced SS points."""
        r = radius * self.ss
        ri = int(np.ceil(r))
        dy, dx = np.mgrid[-ri:ri + 1, -ri:ri + 1]
        kernel = np.clip(r + 0.5 - np.sqrt(dx * dx + dy * dy), 0, 1)  # soft edge
        cov = np.zeros((self.H, self.W), np.float32)
        for x, y in pts:
            X, Y = int(round(x * self.ss)), int(round(y * self.ss))
            y0, y1 = max(0, Y - ri), min(self.H, Y + ri + 1)
            x0, x1 = max(0, X - ri), min(self.W, X + ri + 1)
            if y1 <= y0 or x1 <= x0:
                continue
            cov[y0:y1, x0:x1] += kernel[y0 - (Y - ri):y1 - (Y - ri), x0 - (X - ri):x1 - (X - ri)]
        np.clip(cov, 0, 1, out=cov)
        return cov

    def line(self, x0, y0, x1, y1, color, alpha=1.0, width=2.0, density=2.0):
        n = max(2, int(np.hypot(x1 - x0, y1 - y0) * self.ss * density / 8) + 2)
        t = np.linspace(0, 1, n)
        pts = list(zip(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t))
        cov = self._cov_discs(pts, width / 2)
        a = np.float32(alpha) * cov[..., None]
        self.img = self.img * (1 - a) + np.array(color, np.float32) * a

    def curve(self, xs, ys, color, alpha=1.0, width=2.4):
        pts = list(zip(xs, ys))
        cov = self._cov_discs(pts, width / 2)
        a = np.float32(alpha) * cov[..., None]
        self.img = self.img * (1 - a) + np.array(color, np.float32) * a

    def text(self, x, y, s, color, scale=3):
        """Blocky bitmap text, drawn at SS resolution, crisp."""
        color = np.array(color, np.float32)
        cx = int(x * self.ss)
        cy = int(y * self.ss)
        for ch in s.upper():
            glyph = FONT.get(ch, FONT[" "])
            for r, row in enumerate(glyph.split(" ")):
                for c, bit in enumerate(row):
                    if bit == "1":
                        Y0 = cy + r * scale * self.ss
                        X0 = cx + c * scale * self.ss
                        self.img[Y0:Y0 + scale * self.ss, X0:X0 + scale * self.ss] = color
            cx += 6 * scale * self.ss

    def text_w(self, s, scale=3):
        return len(s) * 6 * scale

    def downsample(self):
        a = self.img
        return ((a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2]) / 4).astype(np.uint8)


# ---------------------------------------------------------------- scenes

class Demo:
    def __init__(self):
        with open(DATA) as f:
            d = json.load(f)
        self.dates = [p["date"] for p in d["curve"]]
        self.honest = [p["honest"] for p in d["curve"]]
        self.naive = [p["naive"] for p in d["curve"]]
        self.i0 = next(i for i, dt in enumerate(self.dates) if dt >= "2024-01-02")
        self.i1 = next(i for i, dt in enumerate(self.dates) if dt >= "2024-03-01")


def frame(p: Painter, dm: Demo, *, fh=1.0, fn=1.0, band=1.0, chips=1.0, zoom=1.0):
    """One chart frame. fh/fn: draw fraction per curve; band/chips: alphas."""
    W, H = p.w, p.h
    X0, X1, Y0, Y1 = 150, 1780, 940, 220
    lo, hi = 96000, 147000
    n = len(dm.honest)

    p.rect(0, 0, W, H, CANVAS_BG)
    for val in (100_000, 120_000, 140_000):
        y = Y1 + (Y0 - Y1) * (val - lo) / (hi - lo)
        p.hline(X0, X1, y, HAIR, 1.0, 1.2)

    for i in range(0, n, n // 5):
        x = X0 + (X1 - X0) * i / (n - 1)


    def to_xy(vals, frac):
        m = max(2, int(n * frac))
        xs = [X0 + (X1 - X0) * i / (n - 1) for i in range(m)]
        ys = [Y1 + (Y0 - Y1) * (v - lo) / (hi - lo) for v in vals[:m]]
        return xs, ys

    if band > 0:
        bx0 = X0 + (X1 - X0) * dm.i0 / (n - 1)
        bx1 = X0 + (X1 - X0) * dm.i1 / (n - 1)
        p.rect(bx0, 150, bx1 - bx0, Y0 - 150 + 30, CRIMSON, 0.10 * band)
        p.line(bx0, 150, bx0, Y0 + 30, CRIMSON, 0.55 * band, 1.4)
        p.line(bx1, 150, bx1, Y0 + 30, CRIMSON, 0.55 * band, 1.4)

    hx, hy = to_xy(dm.honest, fh)
    nx, ny = to_xy(dm.naive, fn)
    p.curve(hx, hy, LAVENDER, 1.0, 3.0)
    p.curve(nx, ny, CRIMSON, 1.0, 3.0)

    if chips > 0:
        p.rect(X1 + 6, Y1 + (Y0 - Y1) * (dm.naive[-1] - lo) / (hi - lo) - 5, 12, 12, CRIMSON, chips)
        p.rect(X1 + 6, Y1 + (Y0 - Y1) * (dm.honest[-1] - lo) / (hi - lo) - 5, 12, 12, LAVENDER, chips)


def render_hero(dm):
    p = Painter(1920, 1080)
    frame(p, dm)
    write_png(os.path.join(OUT, "hero.png"), p.downsample())
    print("wrote hero.png")


def project(pts, eye=(0.0, 0.55, -1.35), fov=1.05, cx=0.5, cy=0.52):
    """Simple pinhole: world x in [0,1], y up, z depth. Returns screen (0..1)."""
    out = []
    for x, y, z in pts:
        dz = z - eye[2]
        sx = cx + fov * (x - eye[0]) / dz
        sy = cy - fov * (y - eye[1]) / dz
        out.append((sx, sy, dz))
    return out


def render_3d(dm):
    W, H = 1920, 1080
    p = Painter(W, H, ss=2)
    n = len(dm.honest)
    lo, hi = 96000, 147000
    span = 1.0

    def norm(v):
        return (v - lo) / (hi - lo)

    # world: x = time [0.05,0.95], y = equity (normalized), z = lane
    lane_h, lane_n = -0.22, 0.22
    xw = [0.05 + 0.9 * i / (n - 1) for i in range(n)]
    hw = [(x, 0.12 + 0.62 * norm(v), lane_h) for x, v in zip(xw, dm.honest)]
    nw = [(x, 0.12 + 0.62 * norm(v), lane_n) for x, v in zip(xw, dm.naive)]

    p.rect(0, 0, W, H, CANVAS_BG)
    # floor grid
    for gx in np.linspace(0.05, 0.95, 13):
        a = project([(gx, 0.0, lane_h - 0.28), (gx, 0.0, lane_n + 0.28)])
        p.line(a[0][0] * W, a[0][1] * H, a[1][0] * W, a[1][1] * H, HAIR, 0.8, 1.2)
    for gz in np.linspace(lane_h - 0.28, lane_n + 0.28, 9):
        a = project([(0.05, 0.0, gz), (0.95, 0.0, gz)])
        p.line(a[0][0] * W, a[0][1] * H, a[1][0] * W, a[1][1] * H, HAIR, 0.5, 1.0)

    # leak plane: translucent crimson wall between lanes across the window
    x0w, x1w = xw[dm.i0], xw[dm.i1]
    plane = [(x0w, 0.0, lane_h), (x0w, 1.0, lane_h), (x0w, 1.0, lane_n), (x0w, 0.0, lane_n),
             (x1w, 0.0, lane_n), (x1w, 1.0, lane_n), (x1w, 1.0, lane_h), (x1w, 0.0, lane_h)]
    # draw as vertical slice walls at window edges + top film
    for xe in (x0w, x1w):
        a = project([(xe, 0.0, lane_h), (xe, 1.0, lane_h), (xe, 1.0, lane_n), (xe, 0.0, lane_n)])
        p.line(a[0][0] * W, a[0][1] * H, a[1][0] * W, a[1][1] * H, CRIMSON, 0.5, 1.6)
        p.line(a[3][0] * W, a[3][1] * H, a[2][0] * W, a[2][1] * H, CRIMSON, 0.35, 1.2)

    # ribbons: far lane first (painter), thickness scaled by depth
    def draw_curve(world, color, width):
        scr = project(world)
        xs = [s[0] * W for s in scr]
        ys = [s[1] * H for s in scr]
        p.curve(xs, ys, color, 1.0, width)

    draw_curve(hw, LAVENDER_DEEP, 5.0)
    draw_curve(nw, CRIMSON, 5.0)
    draw_curve(hw, LAVENDER, 2.2)

    write_png(os.path.join(OUT, "scene3d.png"), p.downsample())
    print("wrote scene3d.png")


def _render_one(args):
    i, total, out_dir = args
    t = i / (total - 1)
    fh = min(1.0, t / 0.30)
    fn = min(1.0, max(0.0, (t - 0.28) / 0.27))
    band = min(1.0, max(0.0, (t - 0.52) / 0.16))
    chips = min(1.0, max(0.0, (t - 0.66) / 0.12))
    p = Painter(1600, 900)
    frame(p, Demo(), fh=fh, fn=fn, band=band, chips=chips)
    write_png(os.path.join(out_dir, f"f_{i:04d}.png"), p.downsample())
    return i


def render_frames(dm, out_dir, total=360, fps=30, workers=4):
    os.makedirs(out_dir, exist_ok=True)
    from multiprocessing import Pool
    with Pool(workers) as pool:
        for k, i in enumerate(pool.imap_unordered(_render_one, [(i, total, out_dir) for i in range(total)], chunksize=8)):
            if k % 90 == 0:
                print(f"{k}/{total} frames")
    print(f"wrote {total} frames ({total / fps:.0f}s at {fps} fps) into {out_dir}")


def main():
    os.makedirs(OUT, exist_ok=True)
    dm = Demo()
    render_hero(dm)
    render_3d(dm)
    if "--frames-only" not in sys.argv:
        frames = os.path.join(OUT, "frames")
        render_frames(dm, frames)
    else:
        print("skipping frames (--frames-only)")


if __name__ == "__main__":
    main()

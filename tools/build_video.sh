#!/usr/bin/env bash
# Build the 1080p mp4 and the inline GIF from the rendered frames.
#
# frames : /tmp/nh_frames/f_0001.png .. f_0240.png
#          1600x900, 30 fps, 240 frames = 8 s. Rendered by the graphics
#          pipeline and kept out of the repo: docs/graphics/frames/ is
#          gitignored, so pass the directory as $1 if it lives elsewhere.
# audio  : leak_chorus.wav, the sonified demo. Rebuilt here from the
#          gallery sonifier at commit 2df2423 (the demo data now lives at
#          docs/data/leak_demo.json; the sonifier expects it one directory
#          up from its own, so it is staged into /tmp before running).
#
# usage: tools/build_video.sh [FRAMES_DIR]
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
FRAMES="${1:-/tmp/nh_frames}"
OUT="$REPO/docs/graphics"
WORK="/tmp/nh_build"
AUDIO="$WORK/audio/leak_chorus.wav"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found"; exit 1; }
[ -d "$FRAMES" ] || { echo "frames dir not found: $FRAMES"; exit 1; }

# --- soundtrack -------------------------------------------------------------
if [ ! -f "$AUDIO" ]; then
  mkdir -p "$WORK/gallery/audio" "$WORK/gallery/data" "$WORK/audio"
  git -C "$REPO" show 2df2423:gallery/audio/sonify.py > "$WORK/gallery/audio/sonify.py"
  cp "$REPO/docs/data/leak_demo.json" "$WORK/gallery/data/leak_demo.json"
  python3 "$WORK/gallery/audio/sonify.py" --out "$WORK/audio"
fi

# --- mp4: 1080p, h264 yuv420p, sonified soundtrack, trimmed to video --------
ffmpeg -y -framerate 30 -i "$FRAMES/f_%04d.png" -i "$AUDIO" \
  -c:v libx264 -crf 18 -preset slow -pix_fmt yuv420p \
  -vf "scale=1920:1080:flags=lanczos" \
  -c:a aac -b:a 192k -ar 44100 -ac 2 \
  -shortest -movflags +faststart \
  "$OUT/nohindsight_leak.mp4"

# --- gif: 800px, 12 fps, two-pass palette, loops forever --------------------
ffmpeg -y -framerate 30 -i "$FRAMES/f_%04d.png" \
  -vf "fps=12,scale=800:-1:flags=lanczos,palettegen=max_colors=128" \
  "$WORK/palette.png"
ffmpeg -y -framerate 30 -i "$FRAMES/f_%04d.png" -i "$WORK/palette.png" \
  -lavfi "fps=12,scale=800:-1:flags=lanczos[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5" \
  -loop 0 \
  "$OUT/leak.gif"

echo "--- ffprobe mp4 ---"
ffprobe -v error -select_streams v:0 \
  -show_entries stream=width,height,codec_name,pix_fmt -show_entries format=duration \
  -of default=noprint_wrappers=1 "$OUT/nohindsight_leak.mp4"
ls -la "$OUT/nohindsight_leak.mp4" "$OUT/leak.gif"

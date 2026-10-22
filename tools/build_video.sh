#!/usr/bin/env bash
# Build the 1080p mp4 and the inline GIF from the rendered frames.
# frames: /tmp/nh_frames/f_*.png (1600x900, 30 fps, 360 frames = 12 s)
# audio:  /tmp/nh_audio.wav (the sonified demo: leak chorus, 25 s, trimmed to video)
set -euo pipefail
FRAMES=docs/graphics/frames
AUDIO=/tmp/nh_audio.wav
OUT=docs/graphics
ffmpeg -y -framerate 30 -i "$FRAMES/f_%04d.png" -i "$AUDIO" \
  -c:v libx264 -crf 18 -preset medium -pix_fmt yuv420p -vf scale=1920:1080:flags=lanczos \
  -c:a aac -b:a 160k -shortest -movflags +faststart "$OUT/nohindsight_leak.mp4"
ffmpeg -y -framerate 30 -i "$FRAMES/f_%04d.png" -vf "fps=12,scale=800:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse" \
  -loop 0 "$OUT/leak.gif"
echo "--- ffprobe mp4 ---"
ffprobe -v error -select_streams v:0 -show_entries stream=width,height,duration -of csv=p=0 "$OUT/nohindsight_leak.mp4"
ls -la "$OUT"

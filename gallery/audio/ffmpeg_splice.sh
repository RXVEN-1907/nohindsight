#!/usr/bin/env bash
# Splice the nohindsight leak story into video. Three commands, each one
# commented with what it does and why those flags.
#
# Inputs:
#   frames: gallery/media/frames/frame_00000.png and up, rendered by
#           gallery/media/manim_two_clocks.py (the media slice owns that).
#   audio:  gallery/audio/leak_chorus.wav, rendered by gallery/audio/sonify.py.
#
# Outputs land next to this script by default. Override with env vars:
#   FRAMES_DIR, FRAME_PATTERN, AUDIO, OUT_DIR, FPS.
# Default mode prints a plan and the tool versions. RUN=1 executes.
# Nothing here muxes files that do not exist.

set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRAMES_DIR="${FRAMES_DIR:-$SCRIPT_DIR/../media/frames}"
FRAME_PATTERN="${FRAME_PATTERN:-frame_%05d.png}"
AUDIO="${AUDIO:-$SCRIPT_DIR/leak_chorus.wav}"
OUT_DIR="${OUT_DIR:-$SCRIPT_DIR}"
FPS="${FPS:-30}"
RUN="${RUN:-0}"

echo "ffmpeg:  $(ffmpeg -version 2>/dev/null | head -1)"
if command -v ffprobe >/dev/null 2>&1; then
  echo "ffprobe: $(command -v ffprobe)"
else
  echo "ffprobe: missing"
fi
echo "frames:  $FRAMES_DIR ($FRAME_PATTERN)"
echo "audio:   $AUDIO"
echo "out:     $OUT_DIR"
echo "mode:    $([ "$RUN" = "1" ] && echo run || echo plan)"

frames_ok=1
audio_ok=1
muxed_ok=1
[ -d "$FRAMES_DIR" ] || { frames_ok=0; }
[ -f "$AUDIO" ] || { audio_ok=0; }
[ -f "$OUT_DIR/nohindsight_leak.mp4" ] || { muxed_ok=0; }

# Measure the audio once. The fade-out needs a start time, which is the
# duration minus 2 s. awk does the arithmetic, no python dependency.
fade_out_start=""
if [ "$audio_ok" = "1" ]; then
  dur="$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$AUDIO")"
  fade_out_start="$(awk -v d="$dur" 'BEGIN { printf "%.3f", d - 2.0 }')"
fi

# do_step LABEL GUARD CMD...
#   GUARD "ok" runs or plans the command. Any other text is a skip reason.
do_step() {
  local label="$1"; shift
  local guard="$1"; shift
  if [ "$guard" = "ok" ]; then
    if [ "$RUN" = "1" ]; then
      echo "run  [$label]"
      "$@"
    else
      printf 'plan [%s]\n  ' "$label"
      printf '%q ' "$@"
      printf '\n'
    fi
  else
    echo "skip [$label] $guard"
  fi
}

# (1) Mux the manim frames with the chorus into nohindsight_leak.mp4.
# The image pattern feeds ffmpeg's image2 demuxer.
#   -framerate $FPS : must match the rate manim rendered at. A mismatch
#                     silently stretches or compresses the timeline.
#   -c:v libx264    : h264, the one codec every player agrees on.
#   -pix_fmt yuv420p: 4:2:0 chroma. Fancier formats still break players.
#   -crf 18         : visually transparent without huge files.
#   -c:a aac 192k   : mp4 audio. A tone track needs nothing more.
#   -shortest       : end at the shorter stream, so the video does not
#                     outlive the audio into frozen frames or silence.
do_step "mux" "$([ "$frames_ok" = "1" ] && [ "$audio_ok" = "1" ] && echo ok || echo "missing frames or audio")" \
  ffmpeg -y -framerate "$FPS" -i "$FRAMES_DIR/$FRAME_PATTERN" -i "$AUDIO" \
    -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
    -c:a aac -b:a 192k -shortest \
    "$OUT_DIR/nohindsight_leak.mp4"

# (2) Same mux, with the audio faded. Hard-cutting a tone track clicks; the
# two afade filters fix that.
#   afade=t=in:st=0:d=2        : ramp from silence over the first 2 s.
#   afade=t=out:st=$start:d=2  : ramp to silence over the last 2 s. The
#                                start is duration minus 2, measured by
#                                ffprobe above. The fade touches audio
#                                only, the video stays full strength.
# The .wav output of sonify.py is about 25 s, so the fade-out starts near
# t=23 s. The beat section sits at the head and survives the fade-in.
do_step "fade-mux" "$([ "$frames_ok" = "1" ] && [ "$audio_ok" = "1" ] && echo ok || echo "missing frames or audio")" \
  ffmpeg -y -framerate "$FPS" -i "$FRAMES_DIR/$FRAME_PATTERN" -i "$AUDIO" \
    -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p \
    -af "afade=t=in:st=0:d=2,afade=t=out:st=${fade_out_start:-23.0}:d=2" \
    -c:a aac -b:a 192k -shortest \
    "$OUT_DIR/nohindsight_leak_faded.mp4"

# (3) One-liner: 15 s preview at 720p for social cards, cut from the
# finished mux.
#   -ss 0 before -i : seek on the input, fast, no decode of skipped time.
#   -t 15           : cap the clip at 15 s.
#   scale=-2:720    : height 720, width auto, and -2 forces the width even,
#                     which h264 requires.
#   crf 23, fast    : a preview can be a little cheaper than the master.
do_step "preview" "$([ "$muxed_ok" = "1" ] && echo ok || echo "run the mux step first")" \
  ffmpeg -y -ss 0 -t 15 -i "$OUT_DIR/nohindsight_leak.mp4" \
    -vf "scale=-2:720" -c:v libx264 -crf 23 -preset fast \
    -c:a aac -b:a 128k \
    "$OUT_DIR/leak_preview_720p.mp4"

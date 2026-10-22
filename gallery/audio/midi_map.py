#!/usr/bin/env python3
"""Render the nohindsight leak demo as a Standard MIDI File.

Writes leak_score.mid next to this script. No mido: the file is written by
hand, and the layout below doubles as a lesson in the format.

Byte layout
-----------
The file is two chunks, back to back.

  MThd  4D 54 68 64    chunk tag, the ASCII string "MThd"
        00 00 00 06    header chunk length, always 6
        00 00          format 0: one track carries everything
        00 01          one track
        01 E0          division: 480 ticks per quarter note

  MTrk  4D 54 72 6B    chunk tag, the ASCII string "MTrk"
        xx xx xx xx    track length in bytes, patched in after serialization
        ...events...   each event is a delta time, then event bytes

Events used here:

  FF 51 03 07 A1 20   tempo meta: 500000 microseconds per quarter note,
                      which is 120 BPM. An eighth note is 240 ticks.
  Cn PP               program change on channel n. Channel 0 gets program
                      48, string ensemble: the honest lane hums. Channel 1
                      gets program 80, square lead: the naive lane talks
                      over it.
  9n KK VV            note on: channel n, key KK (60 is middle C),
                      velocity VV.
  8n KK VV            note off, same fields, velocity 0.
  FF 2F 00            end of track. Last event, always.

Delta times are variable-length quantities: 7 data bits per byte, high bit
set on every byte except the last, most significant group first. The grid
spacing is 240 ticks, which does not fit in 7 bits, so slot deltas are the
two bytes 81 70. Delta 0 is a single 00 byte and chains events at one tick.

Score layout
------------
The curve is quantized to one 1/8th-note slot per curve point, 95 slots,
23.75 seconds. Pitch is log2(equity / 100000) scaled into MIDI 45 to 69,
A2 to A4, 110 to 440 Hz: the same range sonify.py sings. When the naive
equity diverges from the honest equity by more than 5 percent, the naive
channel doubles its note one semitone up. A minor second. The interval that
sounds wrong on purpose, once per leaked decision.

Usage:
  python gallery/audio/midi_map.py           write leak_score.mid, then verify
  python gallery/audio/midi_map.py --verify  parse the existing file back
"""

import argparse
import json
import math
import struct
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_PATH = SCRIPT_DIR.parent / "data" / "leak_demo.json"
MIDI_PATH = SCRIPT_DIR / "leak_score.mid"

TICKS_PER_QUARTER = 480
BPM = 120.0
MICROSECONDS_PER_QUARTER = int(60_000_000 / BPM)  # 500000
SLOT_TICKS = TICKS_PER_QUARTER // 2               # eighth note: 240 ticks

NOTE_LOW = 45   # A2, 110 Hz
NOTE_HIGH = 69  # A4, 440 Hz, same range as the WAVs

HONEST_CHANNEL = 0
NAIVE_CHANNEL = 1
HONEST_PROGRAM = 48   # string ensemble
NAIVE_PROGRAM = 80    # square lead
HONEST_VELOCITY = 72
NAIVE_VELOCITY = 84
DISSONANT_VELOCITY = 76

DIVERGENCE_THRESHOLD = 0.05  # naive more than 5 percent off honest


def vlq(n):
    # Variable-length quantity: 7 bits per byte, high bit on all but last.
    out = [n & 0x7F]
    n >>= 7
    while n:
        out.append((n & 0x7F) | 0x80)
        n >>= 7
    return bytes(reversed(out))


def make_note_fn(curve):
    eqs = [float(p["honest"]) for p in curve] + [float(p["naive"]) for p in curve]
    logs = [math.log2(v / 100000.0) for v in eqs]
    lo, hi = min(logs), max(logs)

    def note(equity):
        t = (math.log2(equity / 100000.0) - lo) / (hi - lo)
        return max(NOTE_LOW, min(NOTE_HIGH, NOTE_LOW + round((NOTE_HIGH - NOTE_LOW) * t)))

    return note


def serialize(events):
    # events: (tick, order, payload). Note-offs sort before note-ons at the
    # same tick, so a slot ends before the next one begins. Meta events at
    # tick 0 ride in between with order 1.
    events = sorted(events, key=lambda e: (e[0], e[1]))
    data = bytearray()
    last = 0
    for tick, _order, payload in events:
        data += vlq(tick - last)
        data += payload
        last = tick
    data += vlq(0) + b"\xff\x2f\x00"  # end of track at the final tick
    track = b"MTrk" + struct.pack(">I", len(data)) + bytes(data)
    header = b"MThd" + struct.pack(">IHHH", 6, 0, 1, TICKS_PER_QUARTER)
    return header + track


def build_score(curve):
    note = make_note_fn(curve)
    tempo = b"\xff\x51\x03" + struct.pack(">I", MICROSECONDS_PER_QUARTER)[1:]
    events = [
        (0, 1, tempo),
        (0, 1, bytes([0xC0 | HONEST_CHANNEL, HONEST_PROGRAM])),
        (0, 1, bytes([0xC0 | NAIVE_CHANNEL, NAIVE_PROGRAM])),
    ]
    dissonant = 0
    for i, p in enumerate(curve):
        on = i * SLOT_TICKS
        off = (i + 1) * SLOT_TICKS
        hn = note(float(p["honest"]))
        nn = note(float(p["naive"]))
        events.append((off, 0, bytes([0x80 | HONEST_CHANNEL, hn, 0])))
        events.append((on, 2, bytes([0x90 | HONEST_CHANNEL, hn, HONEST_VELOCITY])))
        events.append((off, 0, bytes([0x80 | NAIVE_CHANNEL, nn, 0])))
        events.append((on, 2, bytes([0x90 | NAIVE_CHANNEL, nn, NAIVE_VELOCITY])))
        gap = float(p["naive"]) / float(p["honest"]) - 1.0
        if abs(gap) > DIVERGENCE_THRESHOLD:
            events.append((off, 0, bytes([0x80 | NAIVE_CHANNEL, nn + 1, 0])))
            events.append((on, 2, bytes([0x90 | NAIVE_CHANNEL, nn + 1, DISSONANT_VELOCITY])))
            dissonant += 1
    total_ticks = len(curve) * SLOT_TICKS
    return serialize(events), dissonant, total_ticks


def read_vlq(buf, i):
    value = 0
    while True:
        b = buf[i]
        i += 1
        value = (value << 7) | (b & 0x7F)
        if b < 0x80:
            return value, i


def verify(path):
    """Tiny SMF reader. Walks the chunks, counts note-ons, measures time."""
    raw = path.read_bytes()
    if raw[:4] == b"MThd":
        print("header bytes: b'MThd', correct")
    else:
        print("header bytes: %r, wrong, expected b'MThd'" % raw[:4])
        return 1
    header_len, fmt, ntrks, division = struct.unpack(">IHHH", raw[4:14])
    print("header: format=%d tracks=%d division=%d ticks/quarter" % (fmt, ntrks, division))
    pos = 14
    if raw[pos:pos + 4] == b"MTrk":
        print("track tag: b'MTrk', correct")
    else:
        print("track tag: %r, wrong" % raw[pos:pos + 4])
        return 1
    track_len = struct.unpack(">I", raw[pos + 4:pos + 8])[0]
    track = raw[pos + 8:pos + 8 + track_len]
    i = 0
    tick = 0
    eot_tick = 0
    tempo_us = None
    ons = {HONEST_CHANNEL: 0, NAIVE_CHANNEL: 0}
    while i < len(track):
        delta, i = read_vlq(track, i)
        tick += delta
        status = track[i]
        if status == 0xFF:
            mtype = track[i + 1]
            mlen, i = read_vlq(track, i + 2)
            payload = track[i:i + mlen]
            i += mlen
            if mtype == 0x51 and mlen == 3:
                tempo_us = int.from_bytes(payload, "big")
            elif mtype == 0x2F:
                eot_tick = tick
        elif status in (0xF0, 0xF7):
            mlen, i = read_vlq(track, i + 1)
            i += mlen
        else:
            high = status & 0xF0
            ch = status & 0x0F
            if high in (0xC0, 0xD0):
                i += 2
            else:
                if high == 0x90 and track[i + 2] > 0 and ch in ons:
                    ons[ch] += 1
                i += 3
    seconds_per_tick = (tempo_us or 500000) / 1e6 / division
    print("tempo: %d us/quarter (%.0f BPM)" % (tempo_us or 500000, 60e6 / (tempo_us or 500000)))
    print("note-ons: %d total (%d honest, %d naive)" % (sum(ons.values()), ons[0], ons[1]))
    print("end of track at tick %d, duration %.2f s" % (eot_tick, eot_tick * seconds_per_tick))
    return 0


def main():
    ap = argparse.ArgumentParser(description="Write the leak score as MIDI.")
    ap.add_argument("--verify", action="store_true",
                    help="parse leak_score.mid back instead of writing it")
    args = ap.parse_args()
    if args.verify:
        raise SystemExit(verify(MIDI_PATH))
    with open(DATA_PATH, "r") as f:
        data = json.load(f)
    curve = data["curve"]
    payload, dissonant, total_ticks = build_score(curve)
    MIDI_PATH.write_bytes(payload)
    print("wrote %s: %d bytes" % (MIDI_PATH.name, len(payload)))
    seconds_per_tick = (60.0 / BPM) / TICKS_PER_QUARTER
    print("score: %d slots of %d ticks, %.2f s at %.0f BPM"
          % (len(curve), SLOT_TICKS, total_ticks * seconds_per_tick, BPM))
    print("dissonant slots: %d of %d, naive more than %d percent off honest"
          % (dissonant, len(curve), int(DIVERGENCE_THRESHOLD * 100)))
    print()
    raise SystemExit(verify(MIDI_PATH))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Generate a small, license-free playful bed for the promo composition."""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np


SR = 48_000
BPM = 120
BEAT = 60.0 / BPM


def env(length: int, attack: float, release: float) -> np.ndarray:
    out = np.ones(length, dtype=np.float64)
    a = min(length, max(1, int(attack * SR)))
    r = min(length, max(1, int(release * SR)))
    out[:a] = np.linspace(0.0, 1.0, a, endpoint=False)
    out[-r:] *= np.linspace(1.0, 0.0, r, endpoint=False)
    return out


def add_tone(track: np.ndarray, start: float, duration: float, freq: float, amp: float, kind: str = "sine") -> None:
    first = max(0, int(start * SR))
    last = min(len(track), int((start + duration) * SR))
    if last <= first:
        return
    t = np.arange(last - first, dtype=np.float64) / SR
    phase = 2.0 * np.pi * freq * t
    if kind == "triangle":
        wave_data = 2.0 * np.abs(2.0 * ((freq * t) % 1.0) - 1.0) - 1.0
    else:
        wave_data = np.sin(phase)
    track[first:last] += amp * wave_data * env(last - first, 0.012, min(0.18, duration * 0.4))


def add_noise(track: np.ndarray, start: float, duration: float, amp: float) -> None:
    first = max(0, int(start * SR))
    last = min(len(track), int((start + duration) * SR))
    if last <= first:
        return
    rng = np.random.default_rng(7)
    noise = rng.standard_normal(last - first)
    track[first:last] += amp * noise * env(last - first, 0.002, min(0.08, duration * 0.7))


def build(duration: float) -> np.ndarray:
    n = int(duration * SR)
    track = np.zeros(n, dtype=np.float64)
    roots = [261.63, 196.00, 220.00, 174.61]  # C, G, Am, F
    thirds = [329.63, 246.94, 261.63, 220.00]
    fifths = [392.00, 293.66, 329.63, 261.63]
    arp_offsets = [0, 1, 2, 1, 3, 2, 1, 0]
    melody = [
        392.00, 440.00, 523.25, 440.00, 392.00, 329.63, 392.00, 0.0,
        392.00, 440.00, 587.33, 523.25, 440.00, 392.00, 329.63, 0.0,
    ]

    bars = int(np.ceil(duration / (BEAT * 4)))
    for bar in range(bars):
        bar_start = bar * BEAT * 4
        chord = bar % 4
        root = roots[chord]

        # Quiet pad: enough harmonic motion to make the cut feel intentional.
        for note, level in ((root, 0.035), (thirds[chord], 0.022), (fifths[chord], 0.018)):
            add_tone(track, bar_start, BEAT * 4, note, level, "sine")

        # Bouncy bass and a small arpeggio pattern.
        for beat in range(4):
            add_tone(track, bar_start + beat * BEAT, 0.28, root / 2, 0.085, "triangle")
        chord_notes = [root, thirds[chord], fifths[chord], root * 2]
        for step, offset in enumerate(arp_offsets):
            add_tone(track, bar_start + step * BEAT / 2, 0.16, chord_notes[offset], 0.032, "triangle")

        # Soft rhythm: kick, clap and hats, kept deliberately behind the visuals.
        for beat in range(4):
            beat_start = bar_start + beat * BEAT
            add_tone(track, beat_start, 0.12, 95.0, 0.055, "sine")
            add_noise(track, beat_start + BEAT / 2, 0.065, 0.016)
        for eighth in range(8):
            add_noise(track, bar_start + eighth * BEAT / 2 + 0.04, 0.018, 0.007)

        # A simple, sparse top line leaves the text readable.
        for step in range(8):
            note = melody[(bar * 2 + step) % len(melody)]
            if note:
                add_tone(track, bar_start + step * BEAT / 2, 0.18, note, 0.026, "sine")

    # Tiny chimes at the five gallery hand-offs add a sense of response.
    for start, base in zip((12.0, 18.0, 24.0, 30.0, 36.0), (523.25, 587.33, 659.25, 783.99, 880.0)):
        add_tone(track, start, 0.28, base, 0.095, "sine")
        add_tone(track, start + 0.14, 0.38, base * 1.25, 0.045, "sine")

    fade_in = min(n, int(0.8 * SR))
    fade_out = min(n, int(1.0 * SR))
    track[:fade_in] *= np.linspace(0.0, 1.0, fade_in, endpoint=False)
    track[-fade_out:] *= np.linspace(1.0, 0.0, fade_out, endpoint=False)
    peak = float(np.max(np.abs(track))) or 1.0
    return (track / peak * 0.82).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--duration", type=float, default=42.0)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    mono = build(args.duration)
    stereo = np.column_stack((mono, np.roll(mono, int(0.006 * SR))))
    stereo[0, 1] = 0.0
    pcm = np.clip(stereo * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(output), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(SR)
        handle.writeframes(pcm.tobytes())


if __name__ == "__main__":
    main()

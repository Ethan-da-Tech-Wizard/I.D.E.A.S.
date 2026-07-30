"""One-time content generator for the bundled synth/sfx sample library.

Pure stdlib (wave + math + random) so it needs nothing beyond the project's
own venv. Run it again if you want to tweak a sound or add more; it just
overwrites the files under app/resources/samples/.
"""
from __future__ import annotations

import math
import random
import wave
from array import array
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.security import resolve_sample_path, validate_sample_name
from app.services.audio_fx import apply_delay, apply_distortion, apply_pitch_shift, apply_reverb

SAMPLE_RATE = 44100
SAMPLES_ROOT = Path(__file__).resolve().parent.parent / "app" / "resources" / "samples"


def n_samples(duration: float) -> int:
    return max(1, int(SAMPLE_RATE * duration))


def phase_wave(freqs: list[float], wave_shape: str = "sine", pulse_width: float = 0.5) -> list[float]:
    out = []
    phase = 0.0
    for f in freqs:
        phase = (phase + f / SAMPLE_RATE) % 1.0
        if wave_shape == "sine":
            out.append(math.sin(2 * math.pi * phase))
        elif wave_shape == "saw":
            out.append(2.0 * phase - 1.0)
        elif wave_shape == "square":
            out.append(1.0 if phase < pulse_width else -1.0)
        elif wave_shape == "triangle":
            out.append(4.0 * abs(phase - 0.5) - 1.0)
        else:
            raise ValueError(f"unknown wave shape {wave_shape}")
    return out


def adsr(n: int, attack: float, decay: float, sustain: float, release: float) -> list[float]:
    a = int(SAMPLE_RATE * attack)
    d = int(SAMPLE_RATE * decay)
    r = int(SAMPLE_RATE * release)
    s = max(0, n - a - d - r)
    env: list[float] = []
    env.extend(i / a if a else 1.0 for i in range(a))
    env.extend(1.0 - (1.0 - sustain) * (i / d if d else 1.0) for i in range(d))
    env.extend([sustain] * s)
    env.extend(sustain * (1 - i / r if r else 0) for i in range(r))
    if len(env) < n:
        env.extend([env[-1] if env else 0.0] * (n - len(env)))
    return env[:n]


def lfo(n: int, rate: float, lo: float, hi: float) -> list[float]:
    mid, amp = (hi + lo) / 2, (hi - lo) / 2
    return [mid + amp * math.sin(2 * math.pi * rate * (i / SAMPLE_RATE)) for i in range(n)]


def lowpass(samples: list[float], cutoffs: list[float]) -> list[float]:
    # one-pole IIR; alpha derived from cutoff so a varying cutoff list gives a filter sweep/wobble
    out = []
    y = 0.0
    for x, cutoff in zip(samples, cutoffs):
        cutoff = max(20.0, min(cutoff, SAMPLE_RATE / 2 - 100))
        alpha = 1 - math.exp(-2 * math.pi * cutoff / SAMPLE_RATE)
        y += alpha * (x - y)
        out.append(y)
    return out


def white_noise(n: int, seed: int = 0) -> list[float]:
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(n)]


def apply_env(samples: list[float], env: list[float]) -> list[float]:
    return [s * e for s, e in zip(samples, env)]


def exp_sweep(f_start: float, f_end: float, n: int) -> list[float]:
    ratio = f_end / f_start
    return [f_start * (ratio ** (i / n)) for i in range(n)]


def finalize(samples: list[float], peak: float = 0.9, fade_ms: float = 5.0) -> array:
    if not samples:
        return array("h")
    max_abs = max(abs(s) for s in samples) or 1.0
    scale = peak / max_abs
    n = len(samples)
    fade_n = min(max(1, int(SAMPLE_RATE * fade_ms / 1000)), n // 2)
    out = array("h")
    for i, s in enumerate(samples):
        v = s * scale
        if i < fade_n:
            v *= i / fade_n
        elif i >= n - fade_n:
            v *= (n - i) / fade_n
        out.append(int(max(-32767, min(32767, v * 32767))))
    return out


def write_wav(relative_path: str, samples: list[float]) -> None:
    validate_sample_name(relative_path)
    path = SAMPLES_ROOT / relative_path
    root = SAMPLES_ROOT.resolve()
    resolved_parent = path.parent.resolve(strict=False)
    if root != resolved_parent and root not in resolved_parent.parents:
        raise ValueError(f"refusing to write outside sample root: {relative_path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = finalize(samples)
    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm.tobytes())


# --- recipes -----------------------------------------------------------

def gen_wub(freq: float, wobble_rate: float, cutoff_lo: float, cutoff_hi: float,
            duration: float = 2.0, wave_shape: str = "saw") -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, wave_shape)
    filtered = lowpass(osc, lfo(n, wobble_rate, cutoff_lo, cutoff_hi))
    return apply_env(filtered, adsr(n, 0.02, 0.05, 1.0, 0.15))


def gen_wub_growl(freq: float, wobble_rate: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square")
    noise = white_noise(n, seed=int(freq))
    blended = [0.85 * o + 0.15 * z for o, z in zip(osc, noise)]
    filtered = lowpass(blended, lfo(n, wobble_rate, 150, 1800))
    return apply_env(filtered, adsr(n, 0.02, 0.05, 1.0, 0.15))


def gen_wub_robotic(freq: float, wobble_rate: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    pw = lfo(n, wobble_rate, 0.2, 0.8)
    phase = 0.0
    osc = []
    for i in range(n):
        phase = (phase + freq / SAMPLE_RATE) % 1.0
        osc.append(1.0 if phase < pw[i] else -1.0)
    filtered = lowpass(osc, lfo(n, wobble_rate * 1.3, 300, 2200))
    return apply_env(filtered, adsr(n, 0.02, 0.05, 1.0, 0.15))


def gen_sub_sine(freq: float, duration: float = 2.0, swell: bool = False) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    if swell:
        trem = lfo(n, 0.3, 0.7, 1.0)
        osc = [o * t for o, t in zip(osc, trem)]
    return apply_env(osc, adsr(n, 0.05, 0.1, 0.9, 0.3))


def gen_fm_bass(carrier: float, modulator: float, index_start: float, index_end: float,
                 duration: float = 1.2) -> list[float]:
    n = n_samples(duration)
    out = []
    c_phase = m_phase = 0.0
    for i in range(n):
        idx = index_start + (index_end - index_start) * (i / n)
        m_phase = (m_phase + modulator / SAMPLE_RATE) % 1.0
        c_phase = (c_phase + carrier / SAMPLE_RATE) % 1.0
        out.append(math.sin(2 * math.pi * c_phase + idx * math.sin(2 * math.pi * m_phase)))
    return apply_env(out, adsr(n, 0.01, 0.15, 0.6, 0.3))


def gen_acid_bass(freq: float, duration: float, cutoff_start: float, cutoff_end: float,
                   pitch_drop: bool = False) -> list[float]:
    n = n_samples(duration)
    freqs = [freq] * n
    if pitch_drop:
        drop_n = min(int(0.05 * SAMPLE_RATE), n)
        for i in range(drop_n):
            freqs[i] = freq * (1 + 2 * (1 - i / drop_n))
    osc = phase_wave(freqs, "saw")
    cutoffs = [cutoff_start + (cutoff_end - cutoff_start) * (i / n) for i in range(n)]
    filtered = lowpass(osc, cutoffs)
    resonant = [f + 0.3 * (o - f) for o, f in zip(osc, filtered)]  # crude resonance boost
    return apply_env(resonant, adsr(n, 0.005, 0.1, 0.5, 0.2))


def gen_reese_bass(freq: float, detune_cents: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    f2 = freq * (2 ** (detune_cents / 1200))
    osc1, osc2 = phase_wave([freq] * n, "saw"), phase_wave([f2] * n, "saw")
    blended = [(a + b) * 0.5 for a, b in zip(osc1, osc2)]
    filtered = lowpass(blended, lfo(n, 3.0, 200, 1200))
    return apply_env(filtered, adsr(n, 0.05, 0.1, 0.85, 0.3))


def gen_punch_bass(freq_start: float, freq_end: float, duration: float = 0.4) -> list[float]:
    n = n_samples(duration)
    freqs = [freq_start + (freq_end - freq_start) * (i / n) for i in range(n)]
    osc = phase_wave(freqs, "sine")
    return apply_env(osc, adsr(n, 0.001, 0.05, 0.3, 0.2))


def gen_drone(freq: float, duration: float) -> list[float]:
    n = n_samples(duration)
    osc1, osc2 = phase_wave([freq] * n, "sine"), phase_wave([freq] * n, "saw")
    blended = [0.6 * a + 0.4 * b for a, b in zip(osc1, osc2)]
    filtered = lowpass(blended, lfo(n, 0.15, 150, 900))
    return apply_env(filtered, adsr(n, 0.5, 0.3, 0.8, 0.8))


def gen_lead(freq: float, wave_shape: str, duration: float, attack: float = 0.01, decay: float = 0.1,
             sustain: float = 0.8, release: float = 0.2, pulse_width: float = 0.5,
             cutoff: float | None = None) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, wave_shape, pulse_width)
    if cutoff:
        osc = lowpass(osc, [cutoff] * n)
    return apply_env(osc, adsr(n, attack, decay, sustain, release))


def gen_supersaw(freq: float, duration: float, voices: int = 5, detune_cents: float = 14) -> list[float]:
    n = n_samples(duration)
    tracks = []
    for v in range(voices):
        spread = (v - (voices - 1) / 2) / ((voices - 1) / 2 or 1)
        f = freq * (2 ** (spread * detune_cents / 1200))
        tracks.append(phase_wave([f] * n, "saw"))
    blended = [sum(vals) / voices for vals in zip(*tracks)]
    return apply_env(blended, adsr(n, 0.02, 0.1, 0.8, 0.3))


def gen_pluck_lead(freq: float, duration: float, decay: float) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "saw")
    return apply_env(osc, adsr(n, 0.005, decay, 0.0, 0.05))


def gen_sync_lead(freq1: float, freq2: float, duration: float) -> list[float]:
    n = n_samples(duration)
    o1, o2 = phase_wave([freq1] * n, "saw"), phase_wave([freq2] * n, "square")
    blended = [0.5 * a + 0.5 * b for a, b in zip(o1, o2)]
    return apply_env(blended, adsr(n, 0.02, 0.1, 0.8, 0.2))


def gen_sweep_lead(freq_start: float, freq_end: float, wave_shape: str, duration: float) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(freq_start, freq_end, n), wave_shape)
    return apply_env(osc, adsr(n, 0.01, 0.05, 0.7, 0.2))


def gen_pad(freqs: list[float], wave_shape: str, duration: float, attack: float, release: float,
            tremolo: bool = False) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([f] * n, wave_shape) for f in freqs]
    blended = [sum(vals) / len(freqs) for vals in zip(*tracks)]
    if tremolo:
        trem = lfo(n, 4.0, 0.7, 1.0)
        blended = [b * t for b, t in zip(blended, trem)]
    return apply_env(blended, adsr(n, attack, 0.2, 0.75, release))


def gen_airy_pad(freq: float, duration: float) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    filtered_noise = lowpass(white_noise(n, seed=int(freq)), [1200] * n)
    blended = [0.8 * o + 0.2 * z for o, z in zip(osc, filtered_noise)]
    return apply_env(blended, adsr(n, 0.8, 0.3, 0.7, 1.0))


def gen_pluck(freq: float, wave_shape: str, duration: float, decay: float,
              overtone: float | None = None) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, wave_shape)
    if overtone:
        ot = phase_wave([freq * overtone] * n, wave_shape)
        osc = [0.7 * o + 0.3 * t for o, t in zip(osc, ot)]
    return apply_env(osc, adsr(n, 0.002, decay, 0.0, 0.05))


def gen_laser_zap() -> list[float]:
    n = n_samples(0.25)
    osc = phase_wave(exp_sweep(2000, 100, n), "square")
    return apply_env(osc, adsr(n, 0.001, 0.15, 0.1, 0.075))


def gen_laser_charge() -> list[float]:
    n = n_samples(0.6)
    osc = phase_wave(exp_sweep(200, 2000, n), "sine")
    return apply_env(osc, [i / n for i in range(n)])


def gen_whoosh(duration: float) -> list[float]:
    n = n_samples(duration)
    half = n // 2
    cutoffs = [200 + 4000 * (i / half) for i in range(half)]
    cutoffs += [4200 - 4000 * (i / (n - half)) for i in range(n - half)]
    filtered = lowpass(white_noise(n, seed=n), cutoffs)
    return apply_env(filtered, adsr(n, duration * 0.1, duration * 0.2, 0.6, duration * 0.5))


def gen_alarm_siren() -> list[float]:
    n = n_samples(2.0)
    osc = phase_wave(lfo(n, 0.6, 600, 900), "sine")
    return apply_env(osc, adsr(n, 0.05, 0.05, 0.9, 0.1))


def gen_glitch_stutter() -> list[float]:
    n = n_samples(0.6)
    osc = phase_wave([220] * n, "square")
    rng = random.Random(42)
    gate: list[float] = []
    while len(gate) < n:
        seg = rng.randint(int(0.01 * SAMPLE_RATE), int(0.05 * SAMPLE_RATE))
        gate.extend([1.0 if rng.random() > 0.3 else 0.0] * seg)
    return apply_env(osc, gate[:n])


def gen_powerup() -> list[float]:
    notes = [261.6, 329.6, 392.0, 523.3, 659.3]
    n_per = n_samples(0.8 / len(notes))
    out: list[float] = []
    for f in notes:
        seg = phase_wave([f] * n_per, "square")
        out.extend(apply_env(seg, adsr(n_per, 0.005, 0.02, 0.7, 0.03)))
    return out


def gen_error_buzz() -> list[float]:
    n = n_samples(0.3)
    osc = phase_wave([110] * n, "square")
    clipped = [max(-0.6, min(0.6, v * 2.5)) for v in osc]
    return apply_env(clipped, adsr(n, 0.001, 0.05, 0.8, 0.05))


def gen_notification_ping() -> list[float]:
    n = n_samples(0.25)
    osc = phase_wave([1046.5] * n, "sine")
    return apply_env(osc, adsr(n, 0.002, 0.1, 0.0, 0.05))


def gen_boom_deep() -> list[float]:
    n = n_samples(1.5)
    osc = phase_wave(exp_sweep(150, 30, n), "sine")
    noise = white_noise(n, seed=1)
    blended = [0.9 * o + 0.1 * z for o, z in zip(osc, noise)]
    return apply_env(blended, adsr(n, 0.001, 0.3, 0.3, 1.0))


def gen_click_ui() -> list[float]:
    n = n_samples(0.03)
    filtered = lowpass(white_noise(n, seed=2), [6000] * n)
    return apply_env(filtered, adsr(n, 0.001, 0.015, 0.0, 0.012))


def gen_coin() -> list[float]:
    notes = [987.8, 1318.5]
    n_per = n_samples(0.075)
    out: list[float] = []
    for f in notes:
        seg = phase_wave([f] * n_per, "square")
        out.extend(apply_env(seg, adsr(n_per, 0.001, 0.03, 0.6, 0.03)))
    return out


# --- round 2: more variety + a "tunes" category ------------------------

def gen_wub_metallic(freq: float, wobble_rate: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square", pulse_width=0.3)
    overtone = phase_wave([freq * 1.5] * n, "square")
    blended = [0.7 * o + 0.3 * t for o, t in zip(osc, overtone)]
    filtered = lowpass(blended, lfo(n, wobble_rate, 200, 2500))
    return apply_env(filtered, adsr(n, 0.02, 0.05, 1.0, 0.15))


def gen_wub_metallic_ring(freq: float, mod_ratio: float, wobble_rate: float, duration: float = 2.0) -> list[float]:
    # ring-modulating the carrier by a non-integer partial produces the inharmonic,
    # clangorous spectrum that reads as "metallic" (like a detuned bell) rather than
    # a plain filtered tone
    n = n_samples(duration)
    carrier = phase_wave([freq] * n, "square", pulse_width=0.3)
    modulator = phase_wave([freq * mod_ratio] * n, "sine")
    ring = [c * m for c, m in zip(carrier, modulator)]
    filtered = lowpass(ring, lfo(n, wobble_rate, 250, 3000))
    return apply_env(filtered, adsr(n, 0.02, 0.05, 1.0, 0.15))


def gen_wub_clang_stack(freq: float, wobble_rate: float, duration: float = 2.0) -> list[float]:
    # a stack of inharmonic partials (not 1x/2x/3x) wobbled through a filter --
    # same clang/gong character as gen_industrial_clang, but with an LFO wobble
    n = n_samples(duration)
    partials = (1.0, 2.76, 4.07, 5.4)
    tracks = [phase_wave([freq * p] * n, "square") for p in partials]
    blended = [sum(v) / len(partials) for v in zip(*tracks)]
    filtered = lowpass(blended, lfo(n, wobble_rate, 250, 3200))
    return apply_env(filtered, adsr(n, 0.02, 0.05, 1.0, 0.15))


def gen_wub_underwater(freq: float, duration: float = 3.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    filtered = lowpass(osc, lfo(n, 0.6, 80, 500))
    return apply_env(filtered, adsr(n, 0.3, 0.2, 1.0, 0.4))


def gen_wub_siren(freq: float, wobble_rate: float, duration: float = 2.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "saw")
    filtered = lowpass(osc, lfo(n, wobble_rate, 150, 3000))
    return apply_env(filtered, adsr(n, 0.02, 0.05, 1.0, 0.2))


def gen_wub_stab(freq: float, wobble_rate: float, duration: float = 0.6) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square")
    filtered = lowpass(osc, lfo(n, wobble_rate, 200, 2200))
    return apply_env(filtered, adsr(n, 0.005, 0.05, 0.4, 0.1))


def gen_wub_pulse(freq: float, pulse_rate: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "saw")
    trem = lfo(n, pulse_rate, 0.2, 1.0)
    pulsed = [o * max(0.0, t) for o, t in zip(osc, trem)]
    return apply_env(pulsed, adsr(n, 0.02, 0.05, 1.0, 0.15))


def gen_wub_sweep(freq: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "saw")
    cutoffs = [200 + (3000 - 200) * (i / n) for i in range(n)]
    filtered = lowpass(osc, cutoffs)
    return apply_env(filtered, adsr(n, 0.02, 0.1, 0.9, 0.3))


def gen_unison_bass(freq: float, duration: float = 2.0, voices: int = 3, detune_cents: float = 10) -> list[float]:
    n = n_samples(duration)
    tracks = []
    for v in range(voices):
        spread = (v - (voices - 1) / 2) / ((voices - 1) / 2 or 1)
        f = freq * (2 ** (spread * detune_cents / 1200))
        tracks.append(phase_wave([f] * n, "saw"))
    blended = [sum(vals) / voices for vals in zip(*tracks)]
    return apply_env(blended, adsr(n, 0.03, 0.1, 0.85, 0.3))


def gen_dirty_bass(freq: float, duration: float = 1.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "saw")
    driven = [math.tanh(o * 4.0) for o in osc]
    return apply_env(driven, adsr(n, 0.01, 0.1, 0.8, 0.2))


def gen_sine_pulse_bass(freq: float, pulse_rate: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    trem = lfo(n, pulse_rate, 0.3, 1.0)
    pulsed = [o * t for o, t in zip(osc, trem)]
    return apply_env(pulsed, adsr(n, 0.02, 0.05, 0.9, 0.2))


def gen_wobble_sub(freq: float, duration: float = 3.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    trem = lfo(n, 0.4, 0.6, 1.0)
    pulsed = [o * t for o, t in zip(osc, trem)]
    return apply_env(pulsed, adsr(n, 0.1, 0.1, 0.9, 0.4))


def gen_sub_drop(freq_start: float, freq_end: float, duration: float = 1.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(freq_start, freq_end, n), "sine")
    return apply_env(osc, adsr(n, 0.01, 0.3, 0.5, 0.6))


def gen_trance_lead(freq: float, duration: float = 1.5) -> list[float]:
    n = n_samples(duration)
    base = gen_supersaw(freq, duration, voices=5, detune_cents=18)
    trem = lfo(n, 6.0, 0.85, 1.0)
    return [b * t for b, t in zip(base, trem)]


def gen_distorted_lead(freq: float, duration: float = 1.2) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "saw")
    driven = [math.tanh(o * 5.0) for o in osc]
    return apply_env(driven, adsr(n, 0.01, 0.1, 0.8, 0.2))


def gen_bell_lead(freq: float, duration: float = 1.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    ot2 = phase_wave([freq * 2] * n, "sine")
    ot3 = phase_wave([freq * 3] * n, "sine")
    blended = [0.6 * a + 0.25 * b + 0.15 * c for a, b, c in zip(osc, ot2, ot3)]
    return apply_env(blended, adsr(n, 0.01, 0.3, 0.5, 0.5))


def gen_hollow_flute_lead(freq: float, duration: float = 1.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "triangle")
    return apply_env(osc, adsr(n, 0.15, 0.1, 0.8, 0.3))


def gen_arp_stab_lead(freq: float, duration: float = 0.3) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square")
    return apply_env(osc, adsr(n, 0.002, 0.05, 0.3, 0.05))


def gen_rubber_lead(freq: float, wobble_rate: float, duration: float = 1.2) -> list[float]:
    n = n_samples(duration)
    pw = lfo(n, wobble_rate, 0.2, 0.8)
    phase = 0.0
    osc = []
    for i in range(n):
        phase = (phase + freq / SAMPLE_RATE) % 1.0
        osc.append(1.0 if phase < pw[i] else -1.0)
    return apply_env(osc, adsr(n, 0.02, 0.1, 0.8, 0.2))


def gen_ambient_pad(freq: float, duration: float = 4.0) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([freq * d] * n, "sine") for d in (1.0, 2 ** (7 / 1200), 2 ** (-7 / 1200))]
    blended = [sum(v) / 3 for v in zip(*tracks)]
    return apply_env(blended, adsr(n, 1.2, 0.3, 0.8, 1.5))


def gen_ocean_pad(freq: float, duration: float = 4.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    noise = lowpass(white_noise(n, seed=int(freq)), lfo(n, 0.1, 300, 900))
    blended = [0.7 * o + 0.3 * z for o, z in zip(osc, noise)]
    return apply_env(blended, adsr(n, 1.0, 0.3, 0.7, 1.5))


def gen_crystal_pad(freq: float, duration: float = 3.0) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([freq * d] * n, "sine") for d in (1.0, 2.0, 2 ** (12 / 1200))]
    blended = [sum(v) / 3 for v in zip(*tracks)]
    trem = lfo(n, 5.0, 0.75, 1.0)
    shimmered = [b * t for b, t in zip(blended, trem)]
    return apply_env(shimmered, adsr(n, 0.3, 0.2, 0.7, 0.8))


def gen_deep_pad(freq: float, duration: float = 4.0) -> list[float]:
    n = n_samples(duration)
    osc1, osc2 = phase_wave([freq] * n, "sine"), phase_wave([freq] * n, "triangle")
    blended = [0.6 * a + 0.4 * b for a, b in zip(osc1, osc2)]
    return apply_env(blended, adsr(n, 0.8, 0.3, 0.85, 1.2))


def gen_velvet_pad(freq: float, duration: float = 3.0) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([freq * d] * n, "triangle") for d in (1.0, 2 ** (5 / 1200), 2 ** (-5 / 1200))]
    blended = [sum(v) / 3 for v in zip(*tracks)]
    return apply_env(blended, adsr(n, 0.5, 0.2, 0.8, 0.8))


def gen_pluck_wood(freq: float, duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "triangle")
    return apply_env(osc, adsr(n, 0.002, 0.3, 0.0, 0.05))


def gen_pluck_ice(freq: float, duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    ot = phase_wave([freq * 4] * n, "sine")
    blended = [0.7 * o + 0.3 * t for o, t in zip(osc, ot)]
    return apply_env(blended, adsr(n, 0.001, 0.2, 0.0, 0.03))


def gen_pluck_pop(freq: float, duration: float = 0.2) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square")
    return apply_env(osc, adsr(n, 0.001, 0.06, 0.0, 0.02))


def gen_pluck_echo(freq: float, duration: float = 0.6) -> list[float]:
    base = gen_pluck(freq, "sine", duration, 0.4, overtone=2.0)
    return apply_delay(base, SAMPLE_RATE, delay_ms=180.0, feedback=0.4, mix=0.4)


def gen_pluck_koto(freq: float, duration: float = 0.7) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    ot = phase_wave([freq * 1.5] * n, "sine")
    blended = [0.65 * o + 0.35 * t for o, t in zip(osc, ot)]
    return apply_env(blended, adsr(n, 0.003, 0.4, 0.0, 0.05))


def gen_pluck_rubber(freq: float, duration: float = 0.4) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square", pulse_width=0.3)
    return apply_env(osc, adsr(n, 0.002, 0.15, 0.0, 0.04))


NOTE = {
    "C3": 130.81, "D3": 146.83, "E3": 164.81, "F3": 174.61, "G3": 196.00, "A3": 220.00, "B3": 246.94,
    "C4": 261.63, "D4": 293.66, "E4": 329.63, "F4": 349.23, "G4": 392.00, "A4": 440.00, "B4": 493.88,
    "C5": 523.25, "D5": 587.33, "E5": 659.25, "F5": 698.46, "G5": 783.99, "A5": 880.00,
}


def gen_tune(note_names: list[str], note_dur: float, wave_shape: str = "square", gap: float = 0.02) -> list[float]:
    gap_n = n_samples(gap)
    silence = [0.0] * gap_n
    out: list[float] = []
    for name in note_names:
        n = n_samples(note_dur)
        osc = phase_wave([NOTE[name]] * n, wave_shape)
        out.extend(apply_env(osc, adsr(n, 0.005, 0.03, 0.7, 0.05)))
        out.extend(silence)
    return out


def gen_radio_static(duration: float = 1.5) -> list[float]:
    n = n_samples(duration)
    noise = white_noise(n, seed=7)
    trem = lfo(n, 3.0, 0.4, 1.0)
    flickered = [z * t for z, t in zip(noise, trem)]
    return apply_env(flickered, adsr(n, 0.05, 0.1, 0.8, 0.1))


def gen_heartbeat(duration: float = 1.6) -> list[float]:
    thump_n = n_samples(0.15)
    thump = apply_env(
        phase_wave(exp_sweep(120, 40, thump_n), "sine"),
        adsr(thump_n, 0.001, 0.05, 0.2, 0.08),
    )
    gap_n = n_samples(0.15)
    gap = [0.0] * gap_n
    tail_n = max(0, n_samples(duration) - 2 * thump_n - gap_n)
    return thump + gap + thump + [0.0] * tail_n


def gen_drip(duration: float = 0.4) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(1200, 400, n), "sine")
    return apply_env(osc, adsr(n, 0.001, 0.15, 0.0, 0.1))


def gen_engine_rev(duration: float = 1.2) -> list[float]:
    n = n_samples(duration)
    freqs = [60 + 200 * (i / n) for i in range(n)]
    osc = phase_wave(freqs, "saw")
    cutoffs = [400 + 3000 * (i / n) for i in range(n)]
    filtered = lowpass(osc, cutoffs)
    return apply_env(filtered, adsr(n, 0.05, 0.1, 0.9, 0.1))


def gen_thunder_rumble(duration: float = 2.5) -> list[float]:
    n = n_samples(duration)
    filtered = lowpass(white_noise(n, seed=9), [90] * n)
    return apply_env(filtered, adsr(n, 0.3, 0.5, 0.7, 1.0))


def gen_wind_gust(duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    filtered = lowpass(white_noise(n, seed=11), lfo(n, 0.25, 400, 1600))
    return apply_env(filtered, adsr(n, 0.4, 0.3, 0.7, 0.6))


def gen_swoosh_reverse(duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    noise = white_noise(n, seed=13)
    cutoffs = [200 + 5000 * (i / n) for i in range(n)]
    filtered = lowpass(noise, cutoffs)
    env = [(i / n) ** 2 for i in range(n)]
    return apply_env(filtered, env)


def gen_zap_short(duration: float = 0.15) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(3000, 300, n), "square")
    return apply_env(osc, adsr(n, 0.001, 0.08, 0.0, 0.04))


def gen_robot_blip() -> list[float]:
    n_half = n_samples(0.1)
    a = apply_env(phase_wave([800] * n_half, "square"), adsr(n_half, 0.002, 0.03, 0.4, 0.03))
    b = apply_env(phase_wave([1200] * n_half, "square"), adsr(n_half, 0.002, 0.03, 0.4, 0.03))
    return a + [0.0] * n_samples(0.03) + b


def gen_explosion_hit(duration: float = 1.2) -> list[float]:
    n = n_samples(duration)
    noise = white_noise(n, seed=17)
    boom = phase_wave(exp_sweep(180, 40, n), "sine")
    blended = [0.6 * z + 0.4 * b for z, b in zip(noise, boom)]
    return apply_env(blended, adsr(n, 0.001, 0.2, 0.3, 0.7))


def gen_cave_call(duration: float = 1.2) -> list[float]:
    base = gen_sweep_lead(500, 900, "sine", duration)
    return apply_reverb(base, SAMPLE_RATE, decay=0.6, mix=0.5)


def gen_power_down(duration: float = 1.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(800, 60, n), "square")
    return apply_env(osc, adsr(n, 0.01, 0.3, 0.4, 0.4))


# --- round 3: genre production-technique packs (original synthesis, --
# --- generic names -- no artist names, no voices, no sampled audio)  --

def gen_808_slide(freq_start: float, freq_end: float, duration: float = 1.2) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(freq_start, freq_end, n), "sine")
    return apply_env(osc, adsr(n, 0.005, 0.15, 0.8, 0.5))


def gen_808_bump(freq: float, duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(freq * 3, freq, n), "sine")
    return apply_env(osc, adsr(n, 0.002, 0.08, 0.5, 0.3))


def gen_hihat_roll(hits: int, hit_dur: float = 0.05, gap: float = 0.03) -> list[float]:
    n = n_samples(hit_dur)
    gap_n = n_samples(gap)
    silence = [0.0] * gap_n
    out: list[float] = []
    for i in range(hits):
        tick = apply_env(lowpass(white_noise(n, seed=100 + i), [7000] * n), adsr(n, 0.001, 0.02, 0.0, 0.02))
        out.extend(tick)
        out.extend(silence)
    return out


def gen_trap_snare(duration: float = 0.3) -> list[float]:
    n = n_samples(duration)
    noise = white_noise(n, seed=21)
    tone = phase_wave(exp_sweep(400, 180, n), "triangle")
    blended = [0.6 * z + 0.4 * t for z, t in zip(noise, tone)]
    return apply_env(blended, adsr(n, 0.001, 0.08, 0.2, 0.15))


def gen_dusty_hit(freq: float, duration: float = 0.3, wave_shape: str = "triangle") -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, wave_shape)
    dirt = white_noise(n, seed=23)
    blended = [0.85 * o + 0.15 * d for o, d in zip(osc, dirt)]
    filtered = lowpass(blended, [3500] * n)
    return apply_env(filtered, adsr(n, 0.002, 0.1, 0.2, 0.15))


def gen_vinyl_texture(duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    hiss = lowpass(white_noise(n, seed=29), [2500] * n)
    rng = random.Random(31)
    out = list(hiss)
    i = 0
    while i < n:
        if rng.random() < 0.02:
            out[i] = max(-1.0, min(1.0, out[i] + rng.choice([-1, 1]) * rng.uniform(0.3, 0.7)))
        i += 1
    return apply_env(out, adsr(n, 0.05, 0.0, 1.0, 0.05))


def gen_bandpass_sweep(freq: float, wave_shape: str, sweep_rate: float, duration: float) -> list[float]:
    # crude vowel-ish sweep: subtract two lowpass copies at different, LFO-modulated cutoffs
    n = n_samples(duration)
    osc = phase_wave([freq] * n, wave_shape)
    center = lfo(n, sweep_rate, 500, 2200)
    low_cut = [max(50.0, c - 300) for c in center]
    high_cut = [c + 300 for c in center]
    band = [hi - lo for hi, lo in zip(lowpass(osc, high_cut), lowpass(osc, low_cut))]
    return apply_env(band, adsr(n, 0.02, 0.1, 0.85, 0.3))


def gen_funk_bass_pluck(freq: float, duration: float = 0.4) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square", pulse_width=0.4)
    return apply_env(osc, adsr(n, 0.002, 0.12, 0.3, 0.1))


def gen_dark_drone(freq: float, duration: float = 4.0) -> list[float]:
    n = n_samples(duration)
    osc1, osc2 = phase_wave([freq] * n, "sine"), phase_wave([freq * 1.01] * n, "sine")
    blended = [0.5 * a + 0.5 * b for a, b in zip(osc1, osc2)]
    filtered = lowpass(blended, [300] * n)
    return apply_env(filtered, adsr(n, 1.0, 0.3, 0.9, 1.5))


def gen_industrial_clang(duration: float = 0.8) -> list[float]:
    n = n_samples(duration)
    tones = [phase_wave([f] * n, "square") for f in (180, 311, 467)]
    blended = [sum(v) / 3 for v in zip(*tones)]
    noise = white_noise(n, seed=37)
    dirty = [0.7 * b + 0.3 * z for b, z in zip(blended, noise)]
    return apply_env(dirty, adsr(n, 0.001, 0.3, 0.1, 0.4))


def gen_horror_strings(freq: float, duration: float = 3.0) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([freq * d] * n, "saw") for d in (1.0, 2 ** (25 / 1200), 2 ** (-19 / 1200))]
    blended = [sum(v) / 3 for v in zip(*tracks)]
    return apply_env(blended, adsr(n, 0.8, 0.4, 0.8, 1.2))


def gen_siren_wail(duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(lfo(n, 0.35, 300, 1200), "sine")
    return apply_env(osc, adsr(n, 0.2, 0.1, 0.9, 0.3))


def gen_warm_chord_stab(freqs: list[float], duration: float = 0.8) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([f] * n, "triangle") for f in freqs]
    blended = [sum(v) / len(freqs) for v in zip(*tracks)]
    filtered = lowpass(blended, [1800] * n)
    return apply_env(filtered, adsr(n, 0.01, 0.15, 0.6, 0.3))


def gen_organ_swell(freqs: list[float], duration: float = 2.5) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([f] * n, "sine") for f in freqs]
    blended = [sum(v) / len(freqs) for v in zip(*tracks)]
    return apply_env(blended, adsr(n, 0.6, 0.2, 0.8, 0.8))


def gen_tape_warble_pad(freq: float, duration: float = 3.0) -> list[float]:
    n = n_samples(duration)
    wobble = lfo(n, 4.5, freq * 0.985, freq * 1.015)
    osc = phase_wave(wobble, "triangle")
    return apply_env(osc, adsr(n, 0.5, 0.2, 0.8, 0.8))


# --- round 4: more genre-technique packs + electropop/electro-hop/arena --

def gen_open_hat(duration: float = 0.4) -> list[float]:
    n = n_samples(duration)
    filtered = lowpass(white_noise(n, seed=47), [8000] * n)
    return apply_env(filtered, adsr(n, 0.001, 0.05, 0.3, duration * 0.6))


def gen_tape_stop(duration: float = 1.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(300, 30, n), "sine")
    env = [(1 - i / n) for i in range(n)]
    return apply_env(osc, env)


def gen_rhodes_stab(freq: float, duration: float = 1.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "sine")
    ot = phase_wave([freq * 2.01] * n, "sine")
    blended = [0.75 * o + 0.25 * t for o, t in zip(osc, ot)]
    return apply_env(blended, adsr(n, 0.01, 0.3, 0.4, 0.6))


def gen_rnb_snap(duration: float = 0.25) -> list[float]:
    n = n_samples(duration)
    filtered = lowpass(white_noise(n, seed=41), [5000] * n)
    tone = phase_wave([250] * n, "triangle")
    blended = [0.7 * z + 0.3 * t for z, t in zip(filtered, tone)]
    return apply_env(blended, adsr(n, 0.001, 0.06, 0.1, 0.1))


def gen_brush_snare(duration: float = 0.4) -> list[float]:
    n = n_samples(duration)
    noise = lowpass(white_noise(n, seed=43), [4000] * n)
    return apply_env(noise, adsr(n, 0.005, 0.2, 0.1, 0.2))


def gen_funk_clav(freq: float, duration: float = 0.3) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square", pulse_width=0.25)
    filtered = lowpass(osc, [2000] * n)
    return apply_env(filtered, adsr(n, 0.001, 0.08, 0.2, 0.08))


def gen_vibrato_lead(freq: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    freqs = [freq * (2 ** (0.15 * math.sin(2 * math.pi * 5.5 * (i / SAMPLE_RATE)) / 12)) for i in range(n)]
    osc = phase_wave(freqs, "saw")
    return apply_env(osc, adsr(n, 0.05, 0.1, 0.85, 0.4))


def gen_sidechain_pulse_bass(freq: float, pulse_rate: float, duration: float = 2.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "saw")
    period = SAMPLE_RATE / pulse_rate
    env = [0.15 + 0.85 * min(1.0, ((i % period) / period) * 4) for i in range(n)]
    pulsed = [o * e for o, e in zip(osc, env)]
    return apply_env(pulsed, adsr(n, 0.02, 0.1, 1.0, 0.3))


def gen_gated_stab(freq: float, gate_len: float, duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square")
    gate_n = n_samples(gate_len)
    gate = [1.0 if i < gate_n else 0.0 for i in range(n)]
    return apply_env(osc, gate)


def gen_vocoder_chord(freqs: list[float], duration: float = 1.0) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([f] * n, "square", pulse_width=0.4) for f in freqs]
    blended = [sum(v) / len(freqs) for v in zip(*tracks)]
    filtered = lowpass(blended, [1500] * n)
    return apply_env(filtered, adsr(n, 0.01, 0.1, 0.7, 0.2))


def gen_electro_cowbell(duration: float = 0.3) -> list[float]:
    n = n_samples(duration)
    tones = [phase_wave([f] * n, "square") for f in (800, 540)]
    blended = [0.5 * a + 0.5 * b for a, b in zip(*tones)]
    return apply_env(blended, adsr(n, 0.001, 0.15, 0.1, 0.1))


def gen_electro_clap(duration: float = 0.25) -> list[float]:
    n = n_samples(duration)
    filtered = lowpass(white_noise(n, seed=53), [2200] * n)
    return apply_env(filtered, adsr(n, 0.001, 0.05, 0.0, 0.15))


def gen_arp_square_bass(freq: float, duration: float = 1.5) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "square")
    return apply_env(osc, adsr(n, 0.005, 0.05, 0.6, 0.1))


def gen_gated_snare_huge(duration: float = 0.6) -> list[float]:
    n = n_samples(duration)
    noise = white_noise(n, seed=59)
    tone = phase_wave(exp_sweep(300, 150, n), "triangle")
    blended = [0.7 * z + 0.3 * t for z, t in zip(noise, tone)]
    gate_n = n_samples(duration * 0.7)
    gate = [1.0 if i < gate_n else 0.0 for i in range(n)]
    quick_env = adsr(n, 0.001, 0.05, 0.9, 0.02)
    combined = [g * q for g, q in zip(gate, quick_env)]
    return apply_env(blended, combined)


def gen_synth_brass_stab(freqs: list[float], duration: float = 0.6) -> list[float]:
    n = n_samples(duration)
    tracks = [phase_wave([f] * n, "saw") for f in freqs]
    blended = [sum(v) / len(freqs) for v in zip(*tracks)]
    filtered = lowpass(blended, [2500] * n)
    return apply_env(filtered, adsr(n, 0.02, 0.1, 0.7, 0.2))


# --- round 5: full standard-instrument coverage (piano/guitar/violin/drums) --

NOTE_NAMES_MIDI = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_note_name(midi: int) -> str:
    return f"{NOTE_NAMES_MIDI[midi % 12]}{midi // 12 - 1}"


def midi_freq(midi: int) -> float:
    return 440.0 * (2 ** ((midi - 69) / 12))


def gen_piano_note(freq: float, duration: float = 1.8) -> list[float]:
    n = n_samples(duration)
    fundamental = phase_wave([freq] * n, "sine")
    overtone2 = phase_wave([freq * 2.01] * n, "sine")
    overtone3 = phase_wave([freq * 3.02] * n, "sine")
    blended = [0.6 * f + 0.25 * o2 + 0.15 * o3 for f, o2, o3 in zip(fundamental, overtone2, overtone3)]
    return apply_env(blended, adsr(n, 0.005, 0.4, 0.25, 0.5))


def build_piano_catalog() -> dict[str, list[float]]:
    # standard 88-key range: A0 (MIDI 21) .. C8 (MIDI 108)
    return {
        f"instruments/piano_full/{midi_note_name(midi)}.wav": gen_piano_note(midi_freq(midi))
        for midi in range(21, 109)
    }


def karplus_strong(freq: float, duration: float, seed: int = 0, damping: float = 0.5) -> list[float]:
    # classic Karplus-Strong plucked-string algorithm: a noise burst decaying through a short delay loop
    n = n_samples(duration)
    period_n = max(2, int(SAMPLE_RATE / freq))
    rng = random.Random(seed)
    buffer = [rng.uniform(-1.0, 1.0) for _ in range(period_n)]
    out = []
    idx = 0
    prev = buffer[0]
    for _ in range(n):
        cur = buffer[idx]
        avg = (damping * cur + (1 - damping) * prev) * 0.996
        buffer[idx] = avg
        out.append(cur)
        prev = avg
        idx = (idx + 1) % period_n
    return out


def gen_guitar_chord(freqs: list[float], duration: float = 1.5, strum_ms: float = 15.0) -> list[float]:
    n = n_samples(duration)
    out = [0.0] * n
    stagger = n_samples(strum_ms / 1000)
    for i, f in enumerate(freqs):
        string = karplus_strong(f, duration, seed=i * 7 + int(f))
        offset = i * stagger
        for j, v in enumerate(string):
            idx = j + offset
            if idx < n:
                out[idx] += v / len(freqs)
    return out


GUITAR_CHORD_QUALITIES = {
    "maj": [0, 4, 7],
    "min": [0, 3, 7],
    "7": [0, 4, 7, 10],
    "maj7": [0, 4, 7, 11],
    "min7": [0, 3, 7, 10],
    "sus2": [0, 2, 7],
    "sus4": [0, 5, 7],
    "dim": [0, 3, 6],
}
GUITAR_ROOT_NAMES = ["E", "F", "F#", "G", "G#", "A", "A#", "B", "C", "C#", "D", "D#"]


def build_guitar_catalog() -> dict[str, list[float]]:
    # 12 roots x 8 standard chord qualities = 96 chords. "Every chord" isn't a finite
    # target (extensions/altered voicings are unbounded) -- this covers the standard set.
    catalog = {}
    base_root = 82.41  # E2, guitar's low string
    for i, root_name in enumerate(GUITAR_ROOT_NAMES):
        root_freq = base_root * (2 ** (i / 12))
        for quality, intervals in GUITAR_CHORD_QUALITIES.items():
            freqs = [root_freq * (2 ** (iv / 12)) for iv in intervals]
            freqs.append(root_freq * 2)  # doubled root up an octave, like a 6th string
            catalog[f"instruments/guitar_chords/{root_name}_{quality}.wav"] = gen_guitar_chord(freqs)
    return catalog


def gen_violin_note(freq: float, duration: float = 1.8) -> list[float]:
    n = n_samples(duration)
    vibrato_freqs = [freq * (2 ** (0.08 * math.sin(2 * math.pi * 5.5 * (i / SAMPLE_RATE)) / 12)) for i in range(n)]
    osc = phase_wave(vibrato_freqs, "saw")
    filtered = lowpass(osc, [freq * 8] * n)
    return apply_env(filtered, adsr(n, 0.15, 0.1, 0.85, 0.3))


def build_violin_catalog() -> dict[str, list[float]]:
    # standard practical violin range: G3 (MIDI 55) .. C7 (MIDI 96), full chromatic
    return {
        f"instruments/violin_full/{midi_note_name(midi)}.wav": gen_violin_note(midi_freq(midi))
        for midi in range(55, 97)
    }


def gen_kick_drum(duration: float = 0.4) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(150, 50, n), "sine")
    return apply_env(osc, adsr(n, 0.001, 0.1, 0.2, 0.2))


def gen_snare_drum(duration: float = 0.3) -> list[float]:
    n = n_samples(duration)
    noise = white_noise(n, seed=61)
    tone = phase_wave([200] * n, "triangle")
    blended = [0.65 * z + 0.35 * t for z, t in zip(noise, tone)]
    return apply_env(blended, adsr(n, 0.001, 0.1, 0.15, 0.15))


def gen_rimshot() -> list[float]:
    n = n_samples(0.1)
    tone = phase_wave([900] * n, "square")
    return apply_env(tone, adsr(n, 0.001, 0.03, 0.0, 0.03))


def gen_closed_hihat() -> list[float]:
    n = n_samples(0.08)
    filtered = lowpass(white_noise(n, seed=63), [9000] * n)
    return apply_env(filtered, adsr(n, 0.001, 0.02, 0.0, 0.02))


def gen_crash_cymbal() -> list[float]:
    n = n_samples(2.0)
    filtered = lowpass(white_noise(n, seed=67), [10000] * n)
    return apply_env(filtered, adsr(n, 0.001, 0.3, 0.4, 1.2))


def gen_ride_cymbal() -> list[float]:
    n = n_samples(1.2)
    noise = lowpass(white_noise(n, seed=71), [6000] * n)
    ping = phase_wave([3000] * n, "sine")
    blended = [0.7 * z + 0.3 * p for z, p in zip(noise, ping)]
    return apply_env(blended, adsr(n, 0.001, 0.2, 0.3, 0.7))


def gen_tom(freq: float, duration: float = 0.35) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(freq * 1.5, freq * 0.8, n), "sine")
    return apply_env(osc, adsr(n, 0.001, 0.12, 0.2, 0.15))


def build_drum_kit_catalog() -> dict[str, list[float]]:
    return {
        "instruments/drum_kit/kick.wav": gen_kick_drum(),
        "instruments/drum_kit/snare.wav": gen_snare_drum(),
        "instruments/drum_kit/rimshot.wav": gen_rimshot(),
        "instruments/drum_kit/closed_hihat.wav": gen_closed_hihat(),
        "instruments/drum_kit/open_hihat.wav": gen_open_hat(),
        "instruments/drum_kit/crash.wav": gen_crash_cymbal(),
        "instruments/drum_kit/ride.wav": gen_ride_cymbal(),
        "instruments/drum_kit/tom_low.wav": gen_tom(90.0),
        "instruments/drum_kit/tom_mid.wav": gen_tom(130.0),
        "instruments/drum_kit/tom_high.wav": gen_tom(180.0),
        "instruments/drum_kit/clap.wav": gen_electro_clap(0.25),
    }


# --- round 6: funk + psychedelic toolkit (wah guitar, slap bass, --
# --- clavinet, phaser sweeps, breakbeats, cosmic textures) --

def gen_wah_guitar(freq: float, sweep_rate: float, duration: float = 1.2) -> list[float]:
    n = n_samples(duration)
    plucked = karplus_strong(freq, duration, seed=int(freq))
    center = lfo(n, sweep_rate, 500, 2200)
    low_cut = [max(50.0, c - 300) for c in center]
    high_cut = [c + 300 for c in center]
    return [hi - lo for hi, lo in zip(lowpass(plucked, high_cut), lowpass(plucked, low_cut))]


def gen_slap_bass(freq: float, duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    body = phase_wave([freq] * n, "square", pulse_width=0.3)
    out = apply_env(body, adsr(n, 0.002, 0.15, 0.3, 0.15))
    click_n = min(n, n_samples(0.008))
    click = white_noise(click_n, seed=int(freq) + 3)
    for i in range(click_n):
        out[i] = max(-1.0, min(1.0, out[i] + click[i] * 0.6))
    return out


def gen_slap_bass_groove(freqs: list[float], note_dur: float = 0.18, gap: float = 0.03) -> list[float]:
    gap_n = n_samples(gap)
    silence = [0.0] * gap_n
    out: list[float] = []
    for f in freqs:
        out.extend(gen_slap_bass(f, note_dur))
        out.extend(silence)
    return out


def gen_phaser_sweep_pad(freq: float, duration: float = 3.0) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave([freq] * n, "saw")
    center = lfo(n, 0.3, 400, 3000)
    low_cut = [max(50.0, c - 400) for c in center]
    high_cut = [c + 400 for c in center]
    band = [hi - lo for hi, lo in zip(lowpass(osc, high_cut), lowpass(osc, low_cut))]
    return apply_env(band, adsr(n, 1.0, 0.3, 0.8, 1.0))


def gen_breakbeat_loop(hits: list[tuple]) -> list[float]:
    gap_n = n_samples(0.05)
    silence = [0.0] * gap_n
    out: list[float] = []
    for gen_fn, args in hits:
        out.extend(gen_fn(*args))
        out.extend(silence)
    return out


# --- round 7: rap-production "tags" -- airhorn, scratch, hype stabs --

def gen_airhorn_blast(duration: float = 0.5, freq: float = 311.0) -> list[float]:
    n = n_samples(duration)
    osc1 = phase_wave([freq] * n, "square")
    osc2 = phase_wave([freq * 1.005] * n, "square")
    blended = [0.5 * a + 0.5 * b for a, b in zip(osc1, osc2)]
    filtered = lowpass(blended, [3000] * n)
    return apply_env(filtered, adsr(n, 0.02, 0.05, 0.9, 0.15))


def gen_airhorn_multi(count: int, blast_dur: float = 0.4, gap: float = 0.08) -> list[float]:
    gap_n = n_samples(gap)
    silence = [0.0] * gap_n
    out: list[float] = []
    for _ in range(count):
        out.extend(gen_airhorn_blast(blast_dur))
        out.extend(silence)
    return out


def gen_vinyl_scratch(duration: float = 0.4) -> list[float]:
    n = n_samples(duration)
    half = n // 2
    freqs = exp_sweep(150, 600, half) + exp_sweep(600, 100, n - half)
    osc = phase_wave(freqs, "saw")
    noisy = white_noise(n, seed=81)
    blended = [0.75 * o + 0.25 * z for o, z in zip(osc, noisy)]
    return apply_env(blended, adsr(n, 0.005, 0.1, 0.6, 0.1))


def gen_gunshot_hit(duration: float = 0.35) -> list[float]:
    n = n_samples(duration)
    crack = lowpass(white_noise(n, seed=83), [8000] * n)
    tail = phase_wave(exp_sweep(400, 80, n), "sine")
    blended = [0.75 * c + 0.25 * t for c, t in zip(crack, tail)]
    return apply_env(blended, adsr(n, 0.001, 0.05, 0.15, 0.25))


def gen_gunshot_multi(count: int, gap: float = 0.12) -> list[float]:
    gap_n = n_samples(gap)
    silence = [0.0] * gap_n
    out: list[float] = []
    for _ in range(count):
        out.extend(gen_gunshot_hit())
        out.extend(silence)
    return out


def gen_tire_skrrt(duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    noise = white_noise(n, seed=87)
    cutoffs = [max(500.0, 3000 + 4000 * math.sin(2 * math.pi * 3 * (i / n))) for i in range(n)]
    filtered = lowpass(noise, cutoffs)
    return apply_env(filtered, adsr(n, 0.02, 0.1, 0.7, 0.2))


def gen_cash_register(duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    ding1 = phase_wave([1500] * n, "sine")
    ding2 = phase_wave([1800] * n, "sine")
    blended = [0.5 * a + 0.5 * b for a, b in zip(ding1, ding2)]
    return apply_env(blended, adsr(n, 0.001, 0.2, 0.2, 0.2))


# --- round 8: more foley + country / soul-rock / metal packs --

def gen_gun_rack(click_freq1: float, click_freq2: float, gap: float = 0.08) -> list[float]:
    click_n = n_samples(0.03)

    def click(freq: float) -> list[float]:
        tone = phase_wave([freq] * click_n, "square")
        noise = white_noise(click_n, seed=int(freq))
        blended = [0.6 * t + 0.4 * z for t, z in zip(tone, noise)]
        return apply_env(blended, adsr(click_n, 0.001, 0.015, 0.0, 0.014))

    return click(click_freq1) + [0.0] * n_samples(gap) + click(click_freq2)


def gen_pistol_pop(duration: float = 0.2) -> list[float]:
    n = n_samples(duration)
    crack = lowpass(white_noise(n, seed=91), [9000] * n)
    return apply_env(crack, adsr(n, 0.001, 0.03, 0.05, 0.1))


def gen_shotgun_blast(duration: float = 0.5) -> list[float]:
    n = n_samples(duration)
    crack = lowpass(white_noise(n, seed=93), [5000] * n)
    boom = phase_wave(exp_sweep(300, 60, n), "sine")
    blended = [0.6 * c + 0.4 * b for c, b in zip(crack, boom)]
    return apply_env(blended, adsr(n, 0.001, 0.1, 0.2, 0.3))


def gen_banjo_pluck(freq: float, duration: float = 0.6) -> list[float]:
    string1 = karplus_strong(freq, duration, seed=int(freq), damping=0.6)
    string2 = karplus_strong(freq * 1.003, duration, seed=int(freq) + 1, damping=0.6)
    return [0.5 * a + 0.5 * b for a, b in zip(string1, string2)]


def gen_banjo_roll(freqs: list[float], note_dur: float = 0.1, gap: float = 0.02) -> list[float]:
    silence = [0.0] * n_samples(gap)
    out: list[float] = []
    for f in freqs:
        out.extend(gen_banjo_pluck(f, note_dur))
        out.extend(silence)
    return out


def gen_slide_guitar(freq_start: float, freq_end: float, duration: float = 0.8) -> list[float]:
    n = n_samples(duration)
    osc = phase_wave(exp_sweep(freq_start, freq_end, n), "saw")
    filtered = lowpass(osc, [freq_end * 6] * n)
    return apply_env(filtered, adsr(n, 0.01, 0.2, 0.4, 0.2))


def gen_fiddle_lick(freqs: list[float], note_dur: float = 0.15) -> list[float]:
    out: list[float] = []
    for f in freqs:
        n = n_samples(note_dur)
        osc = phase_wave([f] * n, "saw")
        filtered = lowpass(osc, [f * 6] * n)
        out.extend(apply_env(filtered, adsr(n, 0.01, 0.05, 0.6, 0.05)))
    return out


def gen_fiddle_double_stop(freq1: float, freq2: float, duration: float = 1.0) -> list[float]:
    n = n_samples(duration)
    o1 = lowpass(phase_wave([freq1] * n, "saw"), [freq1 * 6] * n)
    o2 = lowpass(phase_wave([freq2] * n, "saw"), [freq2 * 6] * n)
    blended = [0.5 * a + 0.5 * b for a, b in zip(o1, o2)]
    return apply_env(blended, adsr(n, 0.05, 0.1, 0.7, 0.3))


def gen_hammond_swirl(freqs: list[float], duration: float = 2.5) -> list[float]:
    base = gen_organ_swell(freqs, duration)
    trem = lfo(len(base), 6.0, 0.7, 1.0)
    return [b * t for b, t in zip(base, trem)]


def gen_shaker(duration: float = 0.15) -> list[float]:
    n = n_samples(duration)
    filtered = lowpass(white_noise(n, seed=97), [7000] * n)
    return apply_env(filtered, adsr(n, 0.001, 0.05, 0.1, 0.08))


def gen_palm_mute_chug(freq: float, duration: float = 0.15) -> list[float]:
    plucked = karplus_strong(freq, duration, seed=int(freq) + 5, damping=0.7)
    return apply_distortion(plucked, drive=5.0)


def gen_chug_riff(freq: float, pattern: list[float], gap: float = 0.05) -> list[float]:
    silence = [0.0] * n_samples(gap)
    out: list[float] = []
    for dur in pattern:
        out.extend(gen_palm_mute_chug(freq, dur))
        out.extend(silence)
    return out


def gen_tremolo_riff(freq: float, hits: int, hit_dur: float = 0.06, gap: float = 0.01) -> list[float]:
    silence = [0.0] * n_samples(gap)
    out: list[float] = []
    for i in range(hits):
        plucked = karplus_strong(freq, hit_dur, seed=i + int(freq), damping=0.55)
        out.extend(apply_distortion(plucked, drive=6.0))
        out.extend(silence)
    return out


def gen_double_bass_roll(hits: int, hit_dur: float = 0.08, gap: float = 0.02) -> list[float]:
    silence = [0.0] * n_samples(gap)
    out: list[float] = []
    for _ in range(hits):
        out.extend(gen_kick_drum(hit_dur))
        out.extend(silence)
    return out


def build_catalog() -> dict[str, list[float]]:
    catalog = {
        # --- synth/wubs ---
        "synth/wubs/wub_slow_a.wav": gen_wub(55.00, 2.0, 200, 1800, 2.5),
        "synth/wubs/wub_slow_b.wav": gen_wub(65.41, 2.3, 250, 2000, 2.5),
        "synth/wubs/wub_medium_a.wav": gen_wub(73.42, 4.0, 200, 2200, 2.0),
        "synth/wubs/wub_medium_b.wav": gen_wub(82.41, 4.5, 250, 2400, 2.0),
        "synth/wubs/wub_fast_a.wav": gen_wub(55.00, 8.0, 200, 2600, 1.5),
        "synth/wubs/wub_fast_b.wav": gen_wub(98.00, 10.0, 250, 2800, 1.5),
        "synth/wubs/wub_growl_a.wav": gen_wub_growl(49.00, 6.0, 2.0),
        "synth/wubs/wub_growl_b.wav": gen_wub_growl(61.74, 7.0, 2.0),
        "synth/wubs/wub_robotic_a.wav": gen_wub_robotic(55.00, 5.0, 2.0),
        "synth/wubs/wub_robotic_b.wav": gen_wub_robotic(73.42, 6.5, 2.0),
        "synth/wubs/wub_deep_a.wav": gen_wub(41.20, 1.5, 120, 900, 3.0),
        "synth/wubs/wub_deep_b.wav": gen_wub(36.71, 1.8, 110, 1000, 3.0),
        # --- synth/bass ---
        "synth/bass/sub_bass_low.wav": gen_sub_sine(41.20, 2.0),
        "synth/bass/sub_bass_deep.wav": gen_sub_sine(32.70, 2.5, swell=True),
        "synth/bass/fm_bass_metallic.wav": gen_fm_bass(55.00, 165.0, 6, 1, 1.2),
        "synth/bass/fm_bass_bright.wav": gen_fm_bass(82.41, 246.0, 8, 2, 1.2),
        "synth/bass/acid_bass_303.wav": gen_acid_bass(55.00, 1.2, 3500, 200, pitch_drop=True),
        "synth/bass/acid_bass_squelchy.wav": gen_acid_bass(65.41, 1.4, 4000, 150, pitch_drop=True),
        "synth/bass/reese_bass.wav": gen_reese_bass(41.20, 18, 2.0),
        "synth/bass/growl_bass.wav": gen_wub_growl(41.20, 3.0, 2.5),
        "synth/bass/punch_bass.wav": gen_punch_bass(220, 50, 0.4),
        "synth/bass/drone_bass.wav": gen_drone(41.20, 3.5),
        # --- synth/leads ---
        "synth/leads/saw_lead_bright.wav": gen_lead(440.00, "saw", 1.5, release=0.3),
        "synth/leads/saw_lead_warm.wav": gen_lead(329.63, "saw", 1.5, attack=0.05, release=0.4, cutoff=2500),
        "synth/leads/square_lead_chip.wav": gen_lead(523.25, "square", 0.8, attack=0.005, decay=0.05, release=0.1),
        "synth/leads/square_lead_hollow.wav": gen_lead(392.00, "square", 1.2, pulse_width=0.3),
        "synth/leads/supersaw_lead.wav": gen_supersaw(440.00, 1.5),
        "synth/leads/pluck_lead_short.wav": gen_pluck_lead(587.33, 0.5, 0.25),
        "synth/leads/pluck_lead_long.wav": gen_pluck_lead(440.00, 1.2, 0.9),
        "synth/leads/sync_lead.wav": gen_sync_lead(220.00, 660.00, 1.2),
        "synth/leads/screech_lead.wav": gen_sweep_lead(800, 1600, "saw", 1.0),
        "synth/leads/laser_lead.wav": gen_sweep_lead(2000, 300, "square", 0.8),
        # --- synth/pads ---
        "synth/pads/warm_pad.wav": gen_pad([220.0, 220.0 * 2 ** (6 / 1200), 220.0 * 2 ** (-6 / 1200)], "sine", 3.0, 0.6, 0.8),
        "synth/pads/dark_pad.wav": gen_pad([110.0, 110.0 * 2 ** (5 / 1200)], "triangle", 3.0, 0.7, 1.0),
        "synth/pads/string_pad.wav": gen_pad([330.0, 330.0 * 2 ** (8 / 1200), 330.0 * 2 ** (-8 / 1200)], "saw", 3.0, 0.5, 0.7),
        "synth/pads/glass_pad.wav": gen_pad([660.0, 660.0 * 2 ** (7 / 1200)], "sine", 2.5, 0.4, 0.6, tremolo=True),
        "synth/pads/drone_pad.wav": gen_pad([82.41], "sine", 4.0, 1.5, 1.5),
        "synth/pads/airy_pad.wav": gen_airy_pad(220.0, 3.0),
        "synth/pads/choir_pad.wav": gen_pad(
            [440.0, 440.0 * 2 ** (9 / 1200), 440.0 * 2 ** (-9 / 1200), 440.0 * 2 ** (4 / 1200)],
            "sine", 3.0, 0.6, 0.8, tremolo=True,
        ),
        "synth/pads/shimmer_pad.wav": gen_pad([880.0, 880.0 * 2 ** (10 / 1200)], "sine", 2.5, 0.3, 0.9, tremolo=True),
        # --- synth/plucks ---
        "synth/plucks/pluck_bell.wav": gen_pluck(523.25, "sine", 1.0, 0.7, overtone=2.0),
        "synth/plucks/pluck_marimba.wav": gen_pluck(392.00, "sine", 0.6, 0.4),
        "synth/plucks/pluck_glass.wav": gen_pluck(880.00, "sine", 0.8, 0.6, overtone=3.0),
        "synth/plucks/pluck_metal.wav": gen_pluck(440.00, "square", 0.5, 0.35, overtone=1.41),
        "synth/plucks/pluck_soft.wav": gen_pluck(329.63, "triangle", 0.6, 0.45),
        "synth/plucks/pluck_hard.wav": gen_pluck(261.63, "saw", 0.4, 0.25),
        "synth/plucks/pluck_bright.wav": gen_pluck(659.25, "sine", 0.7, 0.5, overtone=1.5),
        "synth/plucks/pluck_dark.wav": gen_pluck(164.81, "sine", 0.8, 0.6),
        # --- sfx (added to the existing folder) ---
        "sfx/laser_zap.wav": gen_laser_zap(),
        "sfx/laser_charge.wav": gen_laser_charge(),
        "sfx/whoosh_fast.wav": gen_whoosh(0.4),
        "sfx/whoosh_slow.wav": gen_whoosh(1.2),
        "sfx/alarm_siren.wav": gen_alarm_siren(),
        "sfx/glitch_stutter.wav": gen_glitch_stutter(),
        "sfx/powerup.wav": gen_powerup(),
        "sfx/error_buzz.wav": gen_error_buzz(),
        "sfx/notification_ping.wav": gen_notification_ping(),
        "sfx/boom_deep.wav": gen_boom_deep(),
        "sfx/click_ui.wav": gen_click_ui(),
        "sfx/coin.wav": gen_coin(),
        # --- synth/wubs (round 2) ---
        "synth/wubs/wub_metallic.wav": gen_wub_metallic(55.00, 5.0),
        "synth/wubs/wub_underwater.wav": gen_wub_underwater(49.00),
        "synth/wubs/wub_siren.wav": gen_wub_siren(65.41, 3.5),
        "synth/wubs/wub_stab.wav": gen_wub_stab(73.42, 9.0),
        "synth/wubs/wub_pulse.wav": gen_wub_pulse(55.00, 5.0),
        "synth/wubs/wub_sweep.wav": gen_wub_sweep(61.74),
        "synth/wubs/wub_metallic_ring_a.wav": gen_wub_metallic_ring(55.00, 3.17, 5.0),
        "synth/wubs/wub_metallic_ring_b.wav": gen_wub_metallic_ring(73.42, 4.23, 6.5),
        "synth/wubs/wub_metallic_fast.wav": gen_wub_metallic_ring(65.41, 2.76, 9.0, duration=1.5),
        "synth/wubs/wub_metallic_clang.wav": gen_wub_clang_stack(49.00, 4.0),
        "synth/wubs/wub_metallic_gong.wav": gen_wub_clang_stack(36.71, 1.2, duration=3.0),
        # --- synth/bass (round 2) ---
        "synth/bass/unison_bass.wav": gen_unison_bass(41.20),
        "synth/bass/dirty_bass.wav": gen_dirty_bass(55.00),
        "synth/bass/sine_pulse_bass.wav": gen_sine_pulse_bass(49.00, 4.0),
        "synth/bass/wobble_sub.wav": gen_wobble_sub(36.71),
        "synth/bass/brass_bass.wav": gen_fm_bass(65.41, 196.0, 3, 5, 1.0),
        "synth/bass/sub_drop.wav": gen_sub_drop(220, 30, 1.5),
        # --- synth/leads (round 2) ---
        "synth/leads/trance_lead.wav": gen_trance_lead(440.00),
        "synth/leads/distorted_lead.wav": gen_distorted_lead(392.00),
        "synth/leads/bell_lead.wav": gen_bell_lead(523.25),
        "synth/leads/hollow_flute_lead.wav": gen_hollow_flute_lead(587.33),
        "synth/leads/arp_stab_lead.wav": gen_arp_stab_lead(659.25),
        "synth/leads/rubber_lead.wav": gen_rubber_lead(440.00, 4.0),
        # --- synth/pads (round 2) ---
        "synth/pads/ambient_pad.wav": gen_ambient_pad(220.0),
        "synth/pads/ocean_pad.wav": gen_ocean_pad(196.0),
        "synth/pads/crystal_pad.wav": gen_crystal_pad(440.0),
        "synth/pads/deep_pad.wav": gen_deep_pad(110.0),
        "synth/pads/velvet_pad.wav": gen_velvet_pad(330.0),
        # --- synth/plucks (round 2) ---
        "synth/plucks/pluck_wood.wav": gen_pluck_wood(293.66),
        "synth/plucks/pluck_ice.wav": gen_pluck_ice(880.00),
        "synth/plucks/pluck_pop.wav": gen_pluck_pop(220.00),
        "synth/plucks/pluck_echo.wav": gen_pluck_echo(440.00),
        "synth/plucks/pluck_koto.wav": gen_pluck_koto(392.00),
        "synth/plucks/pluck_rubber.wav": gen_pluck_rubber(261.63),
        # --- synth/tunes (new: short melodic loops, not just one-shots) ---
        "synth/tunes/arp_major_c.wav": gen_tune(["C4", "E4", "G4", "C5", "G4", "E4"] * 2, 0.14, "square"),
        "synth/tunes/arp_minor_a.wav": gen_tune(["A3", "C4", "E4", "A4", "E4", "C4"] * 2, 0.14, "saw"),
        "synth/tunes/chiptune_riff_1.wav": gen_tune(["C5", "C5", "G4", "G4", "A4", "A4", "G4"], 0.2, "square"),
        "synth/tunes/chiptune_riff_2.wav": gen_tune(["E4", "G4", "A4", "G4", "E4", "D4", "C4"], 0.18, "triangle"),
        "synth/tunes/bassline_groove_1.wav": gen_tune(["E3", "E3", "G3", "A3"] * 2, 0.2, "saw"),
        "synth/tunes/bassline_groove_2.wav": gen_tune(["A3", "C4", "A3", "G3"] * 2, 0.18, "square"),
        "synth/tunes/melody_bell_1.wav": gen_tune(["C5", "E5", "G5", "E5", "C5"], 0.25, "sine"),
        "synth/tunes/melody_bell_2.wav": gen_tune(["D5", "F5", "A5", "F5", "D5"], 0.25, "sine"),
        "synth/tunes/synthwave_riff.wav": gen_tune(["A3", "A3", "C4", "E4", "C4", "A3", "G3"], 0.3, "saw"),
        "synth/tunes/dance_stab_loop.wav": gen_tune(["E4", "E4", "G4", "G4", "C5", "C5", "G4", "G4"], 0.15, "square"),
        "synth/tunes/scale_run_up.wav": gen_tune(["C4", "D4", "E4", "F4", "G4", "A4", "B4", "C5"], 0.12, "square"),
        "synth/tunes/scale_run_down.wav": gen_tune(["C5", "B4", "A4", "G4", "F4", "E4", "D4", "C4"], 0.12, "triangle"),
        "synth/tunes/echo_arp.wav": apply_delay(
            gen_tune(["C4", "E4", "G4", "C5"] * 2, 0.12, "square", gap=0.01),
            SAMPLE_RATE, delay_ms=150.0, feedback=0.45, mix=0.4,
        ),
        # --- sfx (round 2) ---
        "sfx/radio_static.wav": gen_radio_static(),
        "sfx/heartbeat.wav": gen_heartbeat(),
        "sfx/drip.wav": gen_drip(),
        "sfx/engine_rev.wav": gen_engine_rev(),
        "sfx/thunder_rumble.wav": gen_thunder_rumble(),
        "sfx/wind_gust.wav": gen_wind_gust(),
        "sfx/swoosh_reverse.wav": gen_swoosh_reverse(),
        "sfx/zap_short.wav": gen_zap_short(),
        "sfx/robot_blip.wav": gen_robot_blip(),
        "sfx/explosion_hit.wav": gen_explosion_hit(),
        "sfx/cave_call.wav": gen_cave_call(),
        "sfx/power_down.wav": gen_power_down(),
        # --- genre/trap808: sliding sub bass, rolled hats, trap snares ---
        "genre/trap808/808_slide_low.wav": gen_808_slide(41.20, 55.00),
        "genre/trap808/808_slide_deep.wav": gen_808_slide(32.70, 49.00, 1.5),
        "genre/trap808/808_bump_short.wav": gen_808_bump(55.00, 0.35),
        "genre/trap808/808_bump_long.wav": gen_808_bump(41.20, 0.7),
        "genre/trap808/hihat_roll_fast.wav": gen_hihat_roll(12, 0.04, 0.02),
        "genre/trap808/hihat_roll_triplet.wav": gen_hihat_roll(9, 0.06, 0.04),
        "genre/trap808/trap_snare_clap.wav": gen_trap_snare(),
        "genre/trap808/trap_snare_rim.wav": gen_trap_snare(0.2),
        # --- genre/boombap: dusty drums, break hit, vinyl texture ---
        "genre/boombap/dusty_kick.wav": gen_dusty_hit(60.0, 0.35, "sine"),
        "genre/boombap/dusty_snare.wav": gen_dusty_hit(200.0, 0.3, "triangle"),
        "genre/boombap/dusty_rim.wav": gen_dusty_hit(400.0, 0.15, "square"),
        "genre/boombap/dusty_openhat.wav": gen_dusty_hit(3000.0, 0.4, "square"),
        "genre/boombap/boom_bap_break_hit.wav": gen_dusty_hit(90.0, 0.25, "sine"),
        "genre/boombap/vinyl_crackle_loop.wav": gen_vinyl_texture(),
        # --- genre/gfunk: whine lead, talkbox-style sweep, funk bass ---
        "genre/gfunk/synth_whine_lead.wav": gen_bandpass_sweep(440.0, "saw", 1.2, 2.0),
        "genre/gfunk/synth_whine_slow.wav": gen_bandpass_sweep(330.0, "saw", 0.6, 2.5),
        "genre/gfunk/talkbox_sweep.wav": gen_bandpass_sweep(220.0, "square", 2.0, 1.5),
        "genre/gfunk/whistle_lead.wav": gen_bandpass_sweep(880.0, "sine", 0.8, 2.0),
        "genre/gfunk/funk_bass_pluck.wav": gen_funk_bass_pluck(82.41),
        "genre/gfunk/funk_bass_slide.wav": gen_808_slide(55.00, 82.41, 0.5),
        # --- genre/horrorcore: drones, distortion, industrial texture ---
        "genre/horrorcore/dark_drone_low.wav": gen_dark_drone(36.71),
        "genre/horrorcore/distorted_growl_bass.wav": gen_wub_growl(41.20, 2.0, 3.0),
        "genre/horrorcore/industrial_clang.wav": gen_industrial_clang(),
        "genre/horrorcore/horror_strings.wav": gen_horror_strings(220.0),
        "genre/horrorcore/metallic_hit.wav": gen_industrial_clang(0.4),
        "genre/horrorcore/siren_wail.wav": gen_siren_wail(),
        # --- genre/soulchop: warm chords, organ, tape wobble, vinyl bed ---
        "genre/soulchop/warm_chord_stab_maj.wav": gen_warm_chord_stab([261.63, 329.63, 392.00]),
        "genre/soulchop/warm_chord_stab_min.wav": gen_warm_chord_stab([220.00, 261.63, 329.63]),
        "genre/soulchop/chopped_stab_short.wav": gen_warm_chord_stab([293.66, 349.23, 440.00], 0.3),
        "genre/soulchop/chopped_stab_long.wav": gen_warm_chord_stab([196.00, 246.94, 293.66], 1.2),
        "genre/soulchop/soul_organ_swell.wav": gen_organ_swell([130.81, 164.81, 196.00, 261.63]),
        "genre/soulchop/tape_warble_pad.wav": gen_tape_warble_pad(220.0),
        "genre/soulchop/vinyl_hiss_bed.wav": gen_vinyl_texture(3.0),
        # --- genre/trap808 (round 2: overkill) ---
        "genre/trap808/808_slide_high.wav": gen_808_slide(65.41, 98.00, 1.0),
        "genre/trap808/808_slide_reverse.wav": gen_808_slide(98.00, 41.20, 1.0),
        "genre/trap808/808_bump_distorted.wav": apply_distortion(gen_808_bump(49.00, 0.5), drive=5.0),
        "genre/trap808/hihat_roll_slow.wav": gen_hihat_roll(6, 0.08, 0.06),
        "genre/trap808/hihat_roll_32nd.wav": gen_hihat_roll(16, 0.03, 0.015),
        "genre/trap808/trap_kick.wav": gen_808_bump(65.41, 0.25),
        "genre/trap808/trap_clap.wav": gen_trap_snare(0.22),
        "genre/trap808/trap_openhat.wav": gen_open_hat(),
        "genre/trap808/808_slide_up_short.wav": gen_808_slide(41.20, 65.41, 0.6),
        "genre/trap808/808_slide_down_short.wav": gen_808_slide(65.41, 41.20, 0.6),
        # --- genre/boombap (round 2) ---
        "genre/boombap/dusty_crash.wav": gen_open_hat(1.5),
        "genre/boombap/dusty_ride.wav": gen_dusty_hit(2500.0, 0.6, "square"),
        "genre/boombap/boom_bap_break_hit2.wav": gen_dusty_hit(80.0, 0.3, "triangle"),
        "genre/boombap/lofi_tape_stop.wav": gen_tape_stop(),
        "genre/boombap/sample_stutter.wav": gen_glitch_stutter(),
        "genre/boombap/dusty_kick_soft.wav": gen_dusty_hit(50.0, 0.4, "sine"),
        "genre/boombap/dusty_snare_tight.wav": gen_dusty_hit(220.0, 0.2, "triangle"),
        "genre/boombap/vinyl_crackle_loop_long.wav": gen_vinyl_texture(4.0),
        # --- genre/horrorcore (round 2) ---
        "genre/horrorcore/dark_drone_deep.wav": gen_dark_drone(24.5, 5.0),
        "genre/horrorcore/demonic_growl.wav": gen_wub_growl(36.71, 1.5, 3.5),
        "genre/horrorcore/haunted_pad.wav": gen_horror_strings(110.0, 4.0),
        "genre/horrorcore/creepy_bell.wav": gen_bell_lead(587.33, 2.0),
        "genre/horrorcore/screech.wav": gen_sweep_lead(1800, 3000, "square", 0.6),
        "genre/horrorcore/industrial_clang_deep.wav": gen_industrial_clang(1.2),
        "genre/horrorcore/horror_strings_high.wav": gen_horror_strings(440.0, 2.5),
        "genre/horrorcore/siren_wail_slow.wav": gen_siren_wail(3.0),
        # --- genre/gfunk (round 2) ---
        "genre/gfunk/synth_whine_high.wav": gen_bandpass_sweep(660.0, "saw", 1.5, 1.8),
        "genre/gfunk/synth_whine_deep.wav": gen_bandpass_sweep(165.0, "saw", 0.5, 2.5),
        "genre/gfunk/funk_clav.wav": gen_funk_clav(220.0),
        "genre/gfunk/funk_bass_pluck_low.wav": gen_funk_bass_pluck(55.00, 0.5),
        "genre/gfunk/funk_bass_slide2.wav": gen_808_slide(41.20, 65.41, 0.4),
        "genre/gfunk/gfunk_lead_vibrato.wav": gen_vibrato_lead(440.0),
        "genre/gfunk/gfunk_bell.wav": gen_bell_lead(440.0, 1.5),
        "genre/gfunk/talkbox_sweep_fast.wav": gen_bandpass_sweep(220.0, "square", 4.0, 1.2),
        # --- genre/soulchop (round 2) ---
        "genre/soulchop/warm_chord_stab_maj7.wav": gen_warm_chord_stab([261.63, 329.63, 392.00, 493.88], 0.9),
        "genre/soulchop/warm_chord_stab_min7.wav": gen_warm_chord_stab([220.00, 261.63, 329.63, 392.00], 0.9),
        "genre/soulchop/soul_organ_swell_low.wav": gen_organ_swell([98.00, 130.81, 164.81, 196.00], 3.0),
        "genre/soulchop/rhodes_stab_a.wav": gen_rhodes_stab(261.63),
        "genre/soulchop/rhodes_stab_b.wav": gen_rhodes_stab(329.63, 1.2),
        "genre/soulchop/tape_stop_soul.wav": gen_tape_stop(1.2),
        "genre/soulchop/vinyl_hiss_bed_long.wav": gen_vinyl_texture(5.0),
        "genre/soulchop/chopped_stab_reverbed.wav": apply_reverb(
            gen_warm_chord_stab([293.66, 349.23, 440.00], 0.5), SAMPLE_RATE, decay=0.5, mix=0.4,
        ),
        # --- genre/randb (new) ---
        "genre/randb/silky_pad.wav": gen_pad([261.63, 261.63 * 2 ** (6 / 1200), 261.63 * 2 ** (-6 / 1200)], "sine", 3.0, 0.8, 1.0),
        "genre/randb/rnb_rhodes_a.wav": gen_rhodes_stab(293.66, 1.5),
        "genre/randb/rnb_rhodes_b.wav": gen_rhodes_stab(349.23, 1.5),
        "genre/randb/rnb_snap.wav": gen_rnb_snap(),
        "genre/randb/brush_snare.wav": gen_brush_snare(),
        "genre/randb/smooth_sub_bass.wav": gen_sub_sine(41.20, 2.5, swell=True),
        "genre/randb/rnb_hihat_soft.wav": gen_hihat_roll(4, 0.1, 0.08),
        "genre/randb/warm_bell_stab.wav": gen_bell_lead(523.25, 1.2),
        "genre/randb/slow_vinyl_bed.wav": gen_vinyl_texture(4.0),
        "genre/randb/rnb_chord_stab.wav": gen_warm_chord_stab([293.66, 349.23, 440.00, 523.25], 1.0),
        # --- genre/chipmunksoul (new: literal pitch-shifted soul stabs) ---
        "genre/chipmunksoul/chipmunk_soul_stab_a.wav": apply_pitch_shift(
            gen_warm_chord_stab([261.63, 329.63, 392.00], 0.9), 7),
        "genre/chipmunksoul/chipmunk_soul_stab_b.wav": apply_pitch_shift(
            gen_warm_chord_stab([220.00, 261.63, 329.63], 0.9), 9),
        "genre/chipmunksoul/chipmunk_soul_stab_c.wav": apply_pitch_shift(
            gen_warm_chord_stab([293.66, 349.23, 440.00], 0.9), 5),
        "genre/chipmunksoul/chipmunk_organ.wav": apply_pitch_shift(
            gen_organ_swell([130.81, 164.81, 196.00, 261.63], 2.5), 7),
        "genre/chipmunksoul/chipmunk_bell.wav": apply_pitch_shift(gen_bell_lead(392.0, 1.2), 12),
        "genre/chipmunksoul/chipmunk_rhodes.wav": apply_pitch_shift(gen_rhodes_stab(261.63, 1.0), 7),
        "genre/chipmunksoul/deep_soul_stab_a.wav": apply_pitch_shift(
            gen_warm_chord_stab([261.63, 329.63, 392.00], 1.2), -7),
        "genre/chipmunksoul/deep_soul_stab_b.wav": apply_pitch_shift(
            gen_warm_chord_stab([220.00, 261.63, 329.63], 1.2), -9),
        "genre/chipmunksoul/deep_organ.wav": apply_pitch_shift(gen_organ_swell([98.00, 130.81, 164.81], 3.0), -5),
        "genre/chipmunksoul/deep_bell.wav": apply_pitch_shift(gen_bell_lead(392.0, 1.5), -12),
        # --- genre/electropop (new) ---
        "genre/electropop/gated_stab_a.wav": gen_gated_stab(440.0, 0.08),
        "genre/electropop/gated_stab_b.wav": gen_gated_stab(523.25, 0.05),
        "genre/electropop/sidechain_bass.wav": gen_sidechain_pulse_bass(55.0, 2.0),
        "genre/electropop/sidechain_bass_fast.wav": gen_sidechain_pulse_bass(65.41, 4.0),
        "genre/electropop/glossy_pluck.wav": gen_pluck_ice(659.25, 0.4),
        "genre/electropop/bright_synth_pad.wav": gen_pad([440.0, 440.0 * 2 ** (5 / 1200)], "sine", 3.0, 0.3, 0.8, tremolo=True),
        "genre/electropop/vocoder_chord.wav": gen_vocoder_chord([261.63, 329.63, 392.00]),
        "genre/electropop/pop_clap.wav": gen_electro_clap(),
        "genre/electropop/four_on_floor_kick.wav": gen_808_bump(60.0, 0.3),
        "genre/electropop/arp_pluck_seq.wav": gen_tune(["C5", "E5", "G5", "C5"] * 2, 0.1, "square", gap=0.0),
        # --- genre/electrohop (new) ---
        "genre/electrohop/electro_cowbell.wav": gen_electro_cowbell(),
        "genre/electrohop/electro_clap2.wav": gen_electro_clap(0.2),
        "genre/electrohop/electro_snare.wav": gen_trap_snare(0.25),
        "genre/electrohop/arp_square_bass_a.wav": gen_arp_square_bass(55.0),
        "genre/electrohop/arp_square_bass_b.wav": gen_arp_square_bass(65.41, 1.0),
        "genre/electrohop/robo_zap.wav": gen_zap_short(),
        "genre/electrohop/vocoder_stab.wav": gen_vocoder_chord([220.00, 277.18, 329.63]),
        "genre/electrohop/electro_tom.wav": gen_808_bump(98.0, 0.3),
        # --- genre/arenasynthpop (new) ---
        "genre/arenasynthpop/huge_gated_snare.wav": gen_gated_snare_huge(),
        "genre/arenasynthpop/soaring_supersaw.wav": gen_supersaw(440.0, 2.0, voices=7, detune_cents=20),
        "genre/arenasynthpop/pulsing_seq_bass.wav": gen_sidechain_pulse_bass(82.41, 3.0),
        "genre/arenasynthpop/big_pad_swell.wav": gen_pad(
            [220.0, 220.0 * 2 ** (7 / 1200), 220.0 * 2 ** (-7 / 1200)], "sine", 4.5, 2.0, 1.5),
        "genre/arenasynthpop/uplifting_chord_stab.wav": gen_warm_chord_stab([261.63, 329.63, 392.00, 493.88], 0.7),
        "genre/arenasynthpop/crowd_riser.wav": gen_whoosh(2.0),
        "genre/arenasynthpop/synth_brass_stab_a.wav": gen_synth_brass_stab([220.0, 277.18, 329.63]),
        "genre/arenasynthpop/synth_brass_stab_b.wav": gen_synth_brass_stab([261.63, 329.63, 392.00], 0.8),
        "genre/arenasynthpop/anthem_lead.wav": gen_trance_lead(587.33, 2.0),
        "genre/arenasynthpop/arena_clap_stack.wav": gen_electro_clap(0.3),
        # --- genre/funkpsych: funk + psychedelic toolkit ---
        "genre/funkpsych/wah_guitar_a.wav": gen_wah_guitar(110.0, 3.0, 1.2),
        "genre/funkpsych/wah_guitar_b.wav": gen_wah_guitar(146.83, 4.0, 1.0),
        "genre/funkpsych/wah_guitar_slow.wav": gen_wah_guitar(82.41, 1.5, 2.0),
        "genre/funkpsych/slap_bass_a.wav": gen_slap_bass(55.00),
        "genre/funkpsych/slap_bass_b.wav": gen_slap_bass(65.41, 0.4),
        "genre/funkpsych/slap_bass_groove.wav": gen_slap_bass_groove([55.00, 55.00, 82.41, 65.41, 55.00, 41.20]),
        "genre/funkpsych/groove_bass_riff.wav": gen_slap_bass_groove(
            [41.20, 41.20, 55.00, 49.00, 41.20, 36.71, 41.20, 55.00], 0.15, 0.02),
        "genre/funkpsych/clavinet_funk_a.wav": gen_funk_clav(220.0),
        "genre/funkpsych/clavinet_funk_b.wav": gen_funk_clav(293.66, 0.25),
        "genre/funkpsych/phaser_sweep_pad.wav": gen_phaser_sweep_pad(220.0),
        "genre/funkpsych/phaser_sweep_pad_slow.wav": gen_phaser_sweep_pad(110.0, 4.0),
        "genre/funkpsych/cosmic_arp.wav": apply_delay(
            gen_tune(["C4", "E4", "G4", "B4", "D5", "B4", "G4", "E4"], 0.13, "triangle", gap=0.01),
            SAMPLE_RATE, delay_ms=280.0, feedback=0.5, mix=0.45),
        "genre/funkpsych/cosmic_arp_reverbed.wav": apply_reverb(
            gen_tune(["A3", "C4", "E4", "A4"] * 2, 0.12, "triangle", gap=0.01),
            SAMPLE_RATE, decay=0.6, mix=0.5),
        "genre/funkpsych/funky_horn_stab_a.wav": gen_synth_brass_stab([220.0, 277.18, 329.63], 0.4),
        "genre/funkpsych/funky_horn_stab_b.wav": gen_synth_brass_stab([164.81, 207.65, 246.94], 0.5),
        "genre/funkpsych/breakbeat_loop_a.wav": gen_breakbeat_loop([
            (gen_kick_drum, (0.15,)), (gen_snare_drum, (0.12,)), (gen_closed_hihat, ()), (gen_closed_hihat, ()),
            (gen_kick_drum, (0.15,)), (gen_kick_drum, (0.15,)), (gen_snare_drum, (0.12,)), (gen_closed_hihat, ()),
        ]),
        "genre/funkpsych/breakbeat_loop_b.wav": gen_breakbeat_loop([
            (gen_kick_drum, (0.15,)), (gen_closed_hihat, ()), (gen_snare_drum, (0.12,)), (gen_closed_hihat, ()),
            (gen_closed_hihat, ()), (gen_kick_drum, (0.15,)), (gen_snare_drum, (0.12,)), (gen_kick_drum, (0.15,)),
        ]),
        "genre/funkpsych/fuzz_guitar_riff.wav": apply_distortion(karplus_strong(110.0, 1.0, seed=99), drive=6.0),
        "genre/funkpsych/fuzz_bass_riff.wav": apply_distortion(gen_slap_bass(41.20, 0.6), drive=4.0),
        "genre/funkpsych/spaceship_sweep.wav": gen_bandpass_sweep(880.0, "saw", 0.4, 3.0),
        "genre/funkpsych/trippy_delay_pluck.wav": apply_delay(
            gen_pluck(440.0, "sine", 0.5, 0.4, overtone=1.5), SAMPLE_RATE, delay_ms=333.0, feedback=0.55, mix=0.5),
        "genre/funkpsych/funk_organ_stab.wav": gen_organ_swell([130.81, 164.81, 196.00, 233.08], 1.0),
        "genre/funkpsych/wah_talkbox_funk.wav": gen_bandpass_sweep(196.0, "square", 2.5, 1.5),
        "genre/funkpsych/cosmic_pad_swell.wav": gen_pad(
            [220.0, 220.0 * 2 ** (7 / 1200), 440.0], "sine", 4.0, 1.5, 1.5, tremolo=True),
        # --- genre/raptags: producer tags / hype sounds ---
        "genre/raptags/airhorn_blast.wav": gen_airhorn_blast(),
        "genre/raptags/airhorn_double.wav": gen_airhorn_multi(2),
        "genre/raptags/airhorn_triple.wav": gen_airhorn_multi(3),
        "genre/raptags/vinyl_scratch.wav": gen_vinyl_scratch(),
        "genre/raptags/vinyl_scratch_fast.wav": gen_vinyl_scratch(0.25),
        "genre/raptags/gunshot_hit.wav": gen_gunshot_hit(),
        "genre/raptags/gunshot_double.wav": gen_gunshot_multi(2),
        "genre/raptags/tire_skrrt.wav": gen_tire_skrrt(),
        "genre/raptags/cash_register.wav": gen_cash_register(),
        "genre/raptags/dj_turntable_stop.wav": gen_tape_stop(0.6),
        "genre/raptags/hype_horn_stab.wav": gen_synth_brass_stab([220.0, 277.18, 329.63], 0.3),
        "genre/raptags/air_siren_tag.wav": gen_siren_wail(1.2),
        "genre/raptags/bell_tag_ding.wav": gen_bell_lead(1046.5, 0.4),
        "genre/raptags/reverse_riser_tag.wav": gen_swoosh_reverse(0.6),
        "genre/raptags/shotgun_rack.wav": gen_gun_rack(700.0, 500.0, 0.09),
        "genre/raptags/pistol_rack.wav": gen_gun_rack(1000.0, 800.0, 0.05),
        "genre/raptags/pistol_pop.wav": gen_pistol_pop(),
        "genre/raptags/shotgun_blast.wav": gen_shotgun_blast(),
        # --- genre/country ---
        "genre/country/banjo_pluck_a.wav": gen_banjo_pluck(196.0),
        "genre/country/banjo_pluck_b.wav": gen_banjo_pluck(293.66, 0.5),
        "genre/country/banjo_roll.wav": gen_banjo_roll(
            [196.0, 246.94, 293.66, 196.0, 246.94, 293.66, 392.0, 293.66]),
        "genre/country/slide_guitar_bend_up.wav": gen_slide_guitar(110.0, 165.0),
        "genre/country/slide_guitar_bend_down.wav": gen_slide_guitar(220.0, 130.81),
        "genre/country/harmonica_bend.wav": gen_bandpass_sweep(293.66, "square", 1.0, 1.0),
        "genre/country/harmonica_wail.wav": gen_bandpass_sweep(440.0, "square", 0.6, 1.5),
        "genre/country/fiddle_lick.wav": gen_fiddle_lick([392.0, 440.0, 493.88, 523.25, 493.88, 440.0, 392.0]),
        "genre/country/fiddle_double_stop.wav": gen_fiddle_double_stop(293.66, 392.0),
        "genre/country/train_beat_loop.wav": gen_breakbeat_loop([
            (gen_kick_drum, (0.12,)), (gen_brush_snare, (0.15,)), (gen_kick_drum, (0.12,)), (gen_brush_snare, (0.15,)),
            (gen_kick_drum, (0.12,)), (gen_brush_snare, (0.15,)), (gen_kick_drum, (0.12,)), (gen_brush_snare, (0.15,)),
        ]),
        "genre/country/train_beat_loop_fast.wav": gen_breakbeat_loop([
            (gen_kick_drum, (0.08,)), (gen_brush_snare, (0.1,)), (gen_kick_drum, (0.08,)), (gen_brush_snare, (0.1,)),
            (gen_kick_drum, (0.08,)), (gen_brush_snare, (0.1,)),
        ]),
        "genre/country/acoustic_strum_g.wav": gen_guitar_chord([196.0, 246.94, 293.66, 392.0], 1.5),
        "genre/country/acoustic_strum_d.wav": gen_guitar_chord([146.83, 185.00, 220.00, 293.66], 1.5),
        "genre/country/twangy_pluck.wav": gen_banjo_pluck(392.0, 0.4),
        "genre/country/upright_bass_walk.wav": gen_slap_bass_groove([98.0, 110.0, 123.47, 130.81], 0.3, 0.05),
        # --- genre/soulrock ---
        "genre/soulrock/overdriven_organ.wav": apply_distortion(
            gen_organ_swell([130.81, 164.81, 196.00, 246.94], 1.5), drive=4.0),
        "genre/soulrock/gritty_soul_riff.wav": apply_distortion(
            karplus_strong(82.41, 1.0, seed=101, damping=0.45), drive=5.0),
        "genre/soulrock/power_chord_e.wav": apply_distortion(
            gen_guitar_chord([82.41, 82.41 * 2 ** (7 / 12)], 1.2), drive=6.0),
        "genre/soulrock/power_chord_a.wav": apply_distortion(
            gen_guitar_chord([110.0, 110.0 * 2 ** (7 / 12)], 1.2), drive=6.0),
        "genre/soulrock/hammond_swirl.wav": gen_hammond_swirl([196.0, 246.94, 293.66, 392.0]),
        "genre/soulrock/gospel_pad.wav": gen_pad(
            [261.63, 261.63 * 2 ** (4 / 1200), 261.63 * 2 ** (-4 / 1200), 329.63], "sine", 3.0, 1.0, 1.2, tremolo=True),
        "genre/soulrock/horn_section_stab.wav": gen_synth_brass_stab([220.0, 277.18, 329.63, 392.0], 0.5),
        "genre/soulrock/shaker.wav": gen_shaker(),
        "genre/soulrock/soul_rock_groove_bass.wav": gen_slap_bass_groove(
            [82.41, 82.41, 110.0, 98.0, 82.41, 73.42], 0.18, 0.03),
        "genre/soulrock/distorted_horn_hit.wav": apply_distortion(
            gen_synth_brass_stab([164.81, 207.65, 246.94], 0.4), drive=3.0),
        "genre/soulrock/rock_drum_fill.wav": gen_breakbeat_loop([
            (gen_snare_drum, (0.15,)), (gen_tom, (130.0, 0.2)), (gen_tom, (90.0, 0.2)),
            (gen_kick_drum, (0.2,)), (gen_crash_cymbal, ()),
        ]),
        "genre/soulrock/wah_soul_lick.wav": gen_wah_guitar(146.83, 2.5, 1.0),
        # --- genre/metal ---
        "genre/metal/palm_mute_chug_a.wav": gen_palm_mute_chug(41.20),
        "genre/metal/palm_mute_chug_b.wav": gen_palm_mute_chug(36.71),
        "genre/metal/chug_riff_loop.wav": gen_chug_riff(41.20, [0.1, 0.1, 0.1, 0.2, 0.1, 0.1, 0.2]),
        "genre/metal/power_chord_distorted_e.wav": apply_distortion(
            gen_guitar_chord([82.41, 82.41 * 2 ** (7 / 12)], 1.0), drive=8.0),
        "genre/metal/power_chord_distorted_d.wav": apply_distortion(
            gen_guitar_chord([73.42, 73.42 * 2 ** (7 / 12)], 1.0), drive=8.0),
        "genre/metal/tremolo_picked_riff.wav": gen_tremolo_riff(110.0, 10),
        "genre/metal/doom_drone_distorted.wav": apply_distortion(gen_dark_drone(24.5, 4.0), drive=3.0),
        "genre/metal/blast_beat_loop.wav": gen_breakbeat_loop([
            (gen_kick_drum, (0.08,)), (gen_snare_drum, (0.08,)), (gen_kick_drum, (0.08,)), (gen_snare_drum, (0.08,)),
            (gen_kick_drum, (0.08,)), (gen_snare_drum, (0.08,)), (gen_kick_drum, (0.08,)), (gen_snare_drum, (0.08,)),
        ]),
        "genre/metal/double_bass_kick_roll.wav": gen_double_bass_roll(12),
        "genre/metal/screech_harmonic.wav": apply_distortion(gen_sweep_lead(1200, 2400, "square", 0.8), drive=4.0),
        "genre/metal/metal_scream_synth.wav": apply_distortion(gen_siren_wail(1.0), drive=5.0),
        "genre/metal/gallop_rhythm_riff.wav": gen_chug_riff(55.0, [0.06, 0.06, 0.12, 0.06, 0.06, 0.12]),
    }
    catalog.update(build_piano_catalog())
    catalog.update(build_guitar_catalog())
    catalog.update(build_violin_catalog())
    catalog.update(build_drum_kit_catalog())
    return catalog


def main() -> None:
    catalog = build_catalog()
    for relative_path, samples in catalog.items():
        write_wav(relative_path, samples)
    for relative_path in catalog:
        with wave.open(resolve_sample_path(str(SAMPLES_ROOT), relative_path), "r") as check:
            assert check.getnframes() > 0
            assert check.getframerate() == SAMPLE_RATE
    print(f"Generated {len(catalog)} samples under {SAMPLES_ROOT}")


if __name__ == "__main__":
    main()

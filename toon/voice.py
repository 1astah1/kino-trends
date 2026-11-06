"""Озвучка реплик: Gemini 3.8 Flash TTS через OpenRouter (/api/v1/audio/speech, общий ключ, ≈$0.01 за 45 с).

Интонация — полем instructions (06.11.2026: указание в самом тексте модель читает вслух, теги в скобках частично тоже).
Голос героя закреплён в series.S[...]["cast"][key]["voice"]. Огибающая громкости по кадрам — для рта."""

from __future__ import annotations

import pathlib
import subprocess

import httpx
import numpy as np

MODEL = "google/gemini-3.8-flash-tts"
RATE = 24000
EMO_HOW = {"спокойно": "спокойно", "уверенно": "уверенно", "ехидно": "ехидно, с усмешкой", "шок": "в шоке, ошарашенно", "злость": "зло, раздражённо",
           "паника": "в панике, торопливо", "шёпот": "шёпотом", "восторг": "восторженно", "грусть": "грустно", "невозмутимо": "невозмутимо, ровно"}


def synth(settings: dict, text: str, voice: str, how: str, emotion: str, out: pathlib.Path) -> float:
    """Реплика в wav (24 кГц, моно). Возвращает длительность, с."""
    instr = f"Говори по-русски. Манера героя: {how}. В этой реплике — {EMO_HOW.get(emotion, emotion)}. Естественно, как в мультфильме для взрослых."
    err = ""
    for _ in range(3):
        try:
            r = httpx.post("https://openrouter.ai/api/v1/audio/speech", timeout=120,
                           headers={"Authorization": f"Bearer {settings.get('llm_api_key') or ''}"},
                           json={"model": MODEL, "input": text, "voice": voice, "instructions": instr, "response_format": "pcm"})
            if r.status_code == 200 and r.content:
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "-",
                                "-af", "silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_threshold=-45dB:stop_duration=0.25",
                                str(out)], input=r.content, check=True)
                return duration(out)
            err = f"HTTP {r.status_code}: {r.text[:200]}"
        except (httpx.HTTPError, subprocess.CalledProcessError) as e:
            err = str(e)[:200]
    raise RuntimeError(f"озвучка не удалась: {err}")


def duration(path: pathlib.Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout
    return round(float(out.strip() or 0), 3)


def envelope(path: pathlib.Path, fps: int = 30) -> list[float]:
    """Громкость по кадрам 0…1 (RMS, нормировка по 95-му процентилю, сглаживание) — сколько открыт рот."""
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", str(RATE), "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    hop = RATE // fps
    n = max(1, len(x) // hop)
    rms = np.sqrt(np.array([np.mean(x[i * hop:(i + 1) * hop] ** 2) for i in range(n)]) + 1e-9)
    ref = np.percentile(rms, 95) or 1.0
    v = np.clip(rms / ref, 0, 1)
    v = np.where(v < 0.12, 0, v)                      # тишина — рот закрыт
    sm = np.convolve(v, [0.25, 0.5, 0.25], mode="same")
    return [round(float(a), 3) for a in sm]

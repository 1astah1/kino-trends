#!/usr/bin/env python3
"""Бенч одного утверждённого клипа (docs/ARCHITECTURE.md §6.3.10, гейт M5 §10.2):

    python scripts/bench_clipping.py --clip 45            # wall ≤ 4 мин, пиковый RSS ≤ 1,2 ГБ

Подготовка (не входит в замер, кэшируется в var/bench/clip_src/):
* источник 1920×1080 30 fps (testsrc2) с речью из `var/bench/10min_ru.wav` (edge-tts, см.
  bench_asr.py) и её транскриптом GigaAM `10min_ru.gigaam.json.gz`; если их нет — синтетическая
  «речь» из тональных слогов с точными таймингами;
* прокси 540p тем же кодом, что `source_prepare` (`pipeline.proxy.make_proxy`).

Замер (`clipping.produce.produce_clip`): сценарий автора (LLM из окружения, иначе mock или шаблон),
голос (edge-tts, при сбое сети — mock), раскладка EDL, анализ рефрейма на прокси (склейки + YuNet),
субтитры, сведение звука с двухпроходным loudnorm, рендер 1080×1920 одним кодированием, QC одним
проходом, отчёт об оригинальности. Пиковый RSS — максимум из `ru_maxrss` самого процесса и самого
тяжёлого дочернего ffmpeg (`RUSAGE_CHILDREN`). load1 и MemAvailable пишутся раз в секунду.

Реальный источник вместо синтетики: `--source-dir var/media/sources/<id>` (подготовлен
`scripts/clip_local.py` или `source_ingest`; фрагмент ≈ --clip с по границам предложений, начиная
с `--after`) — так замеряется рефрейм `track` на настоящих лицах.

Гейт §10.2 M5: wall ≤ 240 с, пиковый RSS ≤ 1,2 ГБ и load1 за прогон ниже порога допуска
shared_dev (3,0). Бокс общий: load1 до старта (`load1_before`) показывает чужую нагрузку;
`--ignore-load` исключает load1 из итога гейта (значение всё равно пишется).

Код выхода: 0 — гейт пройден, 2 — нет. Правила общего бокса: MemAvailable ≥ 1500 МБ перед
тяжёлыми шагами, nice 10, ffmpeg -threads 2.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

BENCH = ROOT / "var" / "bench"
SRC_DIR = BENCH / "clip_src"
GATE_WALL_SEC = 240.0
GATE_RSS_MB = 1200.0
LOAD_ADMISSION = 3.0  # shared_dev: допуск heavy при load1 < 3,0 (§8.1)


def _ffmpeg(args: list[str]) -> None:
    from autoshorts.pipeline.ffmpeg_exec import run_media

    run_media(["ffmpeg", "-y", *args], timeout_sec=1800, threads=2, nice=10)


def build_source(seconds: float, width: int, height: int) -> tuple[Path, Path, Any, str]:
    """(оригинал, прокси, транскрипт, откуда речь). Кэшируется по параметрам."""
    from autoshorts.clipping.author.voice import synthesize_mock
    from autoshorts.clipping.transcript import Transcript, Word
    from autoshorts.pipeline.proxy import make_proxy

    SRC_DIR.mkdir(parents=True, exist_ok=True)
    tag = f"{width}x{height}_{int(seconds)}"
    video = SRC_DIR / f"src_{tag}.mp4"
    tr_path = SRC_DIR / f"src_{tag}.transcript.json.gz"
    wav = BENCH / "10min_ru.wav"
    gz = BENCH / "10min_ru.gigaam.json.gz"
    speech = "edge_tts_bench_sample" if wav.is_file() and gz.is_file() else "mock_tones"
    if not video.is_file() or not tr_path.is_file():
        if speech == "edge_tts_bench_sample":
            full = Transcript.load(gz)
            words = [w for w in full.words if w.e <= seconds - 0.5]
            audio_args = ["-t", f"{seconds:.3f}", "-i", str(wav)]
        else:
            text = " ".join(
                ["Это синтетическая речь для замера нарезки, с паузами и точками."] * int(seconds / 5)
            )
            take = synthesize_mock("bench_speech", text, SRC_DIR, base_hz=140.0)
            words = [Word(w.w, w.s, w.e) for w in take.words if w.e <= seconds - 0.5]
            audio_args = ["-i", str(take.path)]
        tmp = video.with_name(f".{video.name}.tmp.mp4")
        _ffmpeg(
            [
                "-f",
                "lavfi",
                "-i",
                f"testsrc2=size={width}x{height}:rate=30:duration={seconds:.3f}",
                *audio_args,
                "-af",
                "apad",
                "-t",
                f"{seconds:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "20",
                "-g",
                "60",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                str(tmp),
            ]
        )
        tmp.replace(video)
        Transcript.from_words(words, language="ru", duration=seconds, provider=speech).save(tr_path)
    proxy_dir = SRC_DIR / f"proxy_{tag}"
    proxy = proxy_dir / "proxy_540p.mp4"
    if not proxy.is_file():
        make_proxy(video, proxy_dir, compute_sha=False)
    return video, proxy, Transcript.load(tr_path), speech


def pick_range(transcript: Any, clip_sec: float, *, after: float = 20.0) -> tuple[float, float, int, int]:
    """Фрагмент ≈ clip_sec по границам предложений, начиная не раньше `after` (есть материал для preroll)."""
    sents = transcript.sentences
    i0 = next((i for i, s in enumerate(sents) if s.s >= after), 0)
    best = (i0, i0)
    for j in range(i0, len(sents)):
        if sents[j].e - sents[i0].s <= clip_sec:
            best = (i0, j)
        else:
            break
    a, b = best
    return max(0.0, sents[a].s - 0.2), sents[b].e + 0.3, sents[a].sid, sents[b].sid


class Sampler(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.load_max = 0.0
        self.mem_min: int | None = None

    def run(self) -> None:
        from autoshorts.clipping.source_ingest import mem_available_mb

        while not self.stop.is_set():
            try:
                self.load_max = max(self.load_max, os.getloadavg()[0])
            except OSError:
                pass
            m = mem_available_mb()
            if m is not None:
                self.mem_min = m if self.mem_min is None else min(self.mem_min, m)
            self.stop.wait(1.0)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--clip", type=float, default=45.0, help="длина фрагмента исходника, с")
    p.add_argument("--source-sec", type=float, default=150.0, help="длина синтетического источника, с")
    p.add_argument("--width", type=int, default=1920)
    p.add_argument("--height", type=int, default=1080)
    p.add_argument("--tts", choices=("edge", "mock"), default="edge")
    p.add_argument("--llm", choices=("auto", "mock", "none"), default="auto")
    p.add_argument("--source-dir", type=Path, default=None, help="подготовленный реальный источник")
    p.add_argument("--after", type=float, default=20.0, help="начало поиска фрагмента, с")
    p.add_argument("--ignore-load", action="store_true", help="не включать load1 в итог гейта")
    p.add_argument("--out", type=Path, default=BENCH / "clip_run")
    p.add_argument("--json-out", type=Path, default=None)
    a = p.parse_args(argv)
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass

    from autoshorts.clipping.author.script import make_mock_author_client
    from autoshorts.clipping.llm_client import LLMClient
    from autoshorts.clipping.produce import ClipJobConfig, ClipSource, produce_clip
    from autoshorts.clipping.source_ingest import wait_for_memory

    wait_for_memory(1500)
    t_prep = time.monotonic()
    if a.source_dir:
        from autoshorts.clipping.source_ingest import PreparedSource
        from autoshorts.clipping.transcript import Transcript

        prepared = PreparedSource.load(a.source_dir)
        transcript = Transcript.load(prepared.path("transcript"))
        speech = f"real:{prepared.source_id}"
    else:
        video, proxy, transcript, speech = build_source(a.source_sec, a.width, a.height)
    prep_sec = time.monotonic() - t_prep
    start, end, s_sid, e_sid = pick_range(transcript, a.clip, after=a.after)
    llm = None
    llm_label = "шаблон без LLM"
    if a.llm in ("auto",):
        llm = LLMClient.from_env()
        llm_label = f"LLM {llm.model}" if llm else "шаблон без LLM (нет LLM_API_KEY)"
    if a.llm == "mock" or (a.llm == "auto" and llm is None):
        llm, llm_label = make_mock_author_client(), "mock LLM"
    if a.source_dir:
        source = ClipSource.from_prepared(prepared)
    else:
        source = ClipSource.from_file(
            video,
            analysis_path=proxy,
            title="bench",
            rights={"rights_type": "own", "owner_name": "Umka", "proof_note": "bench footage"},
            watermark={"suspect": False, "severity": "ok"},
        )
    cfg = ClipJobConfig(tts=a.tts, tts_fallback_to_mock=True)
    try:
        load_before = os.getloadavg()[0]
    except OSError:
        load_before = -1.0
    cpu0 = resource.getrusage(resource.RUSAGE_CHILDREN)
    self0 = resource.getrusage(resource.RUSAGE_SELF)
    sampler = Sampler()
    sampler.start()
    t0 = time.monotonic()
    res = produce_clip(
        source,
        transcript,
        start=start,
        end=end,
        start_sid=s_sid,
        end_sid=e_sid,
        workdir=a.out,
        title="Бенч нарезки",
        llm=llm,
        config=cfg,
    )
    wall = time.monotonic() - t0
    sampler.stop.set()
    sampler.join(2.0)
    ru_self = resource.getrusage(resource.RUSAGE_SELF)
    ru_child = resource.getrusage(resource.RUSAGE_CHILDREN)
    rss_self = ru_self.ru_maxrss / 1024
    rss_child = ru_child.ru_maxrss / 1024
    cpu_children = (ru_child.ru_utime - cpu0.ru_utime) + (ru_child.ru_stime - cpu0.ru_stime)
    cpu_self = (ru_self.ru_utime - self0.ru_utime) + (ru_self.ru_stime - self0.ru_stime)
    peak = max(rss_self, rss_child)
    ok_wall = wall <= GATE_WALL_SEC
    ok_rss = peak <= GATE_RSS_MB
    ok_load = sampler.load_max < LOAD_ADMISSION
    result = {
        "gate": {
            "passed": ok_wall and ok_rss and (ok_load or a.ignore_load),
            "wall_ok": ok_wall,
            "rss_ok": ok_rss,
            "load_ok": ok_load,
            "load_counted": not a.ignore_load,
            "wall_max_sec": GATE_WALL_SEC,
            "rss_max_mb": GATE_RSS_MB,
            "load1_max_allowed": LOAD_ADMISSION,
        },
        "clip_source_sec": round(end - start, 2),
        "clip_output_sec": round(res.edl.duration, 2),
        "source": {
            "size": f"{source.width}x{source.height}",
            "fps": source.fps,
            "speech": speech,
            "prep_sec": round(prep_sec, 1),
            "range": [round(start, 2), round(end, 2)],
        },
        "author": res.author.label,
        "llm": llm_label,
        "tts": sorted({t.kind for t in res.voice.takes.values()}),
        "wall_sec": round(wall, 2),
        "steps_sec": {k: round(v, 2) for k, v in res.timings.items()},
        "peak_rss_mb": {"self": round(rss_self), "child_max": round(rss_child)},
        "load1_before": round(load_before, 2),
        "load1_max": round(sampler.load_max, 2),
        "load1_within_admission": ok_load,
        "cpu_sec": {"self": round(cpu_self, 1), "children": round(cpu_children, 1)},
        "cpu_cores_avg": round((cpu_self + cpu_children) / wall, 2) if wall > 0 else None,
        "mem_available_min_mb": sampler.mem_min,
        "reframe_modes": sorted({v.reframe for v in res.edl.video}),
        "qc_passed": res.qc.passed if res.qc else None,
        "qc_failed": res.qc.failed_checks if res.qc else None,
        "author_share": round(res.author_share, 3),
        "originality": res.originality.verdict,
        "final": str(res.final_path),
        "final_mb": round(res.render.size_bytes / 1e6, 2) if res.render else None,
    }
    text = json.dumps(result, ensure_ascii=False, indent=1)
    print(text)
    if a.json_out:
        a.json_out.write_text(text, encoding="utf-8")
    return 0 if result["gate"]["passed"] else 2


def _on_sigterm(signum: int, _frame: Any) -> None:
    """SIGTERM/SIGHUP: ffmpeg живёт в своей сессии (run_media) и пережил бы нас — гасим группы."""
    try:
        from autoshorts.pipeline.ffmpeg_exec import kill_all_active

        kill_all_active(grace_sec=2.0)
    finally:
        raise SystemExit(128 + signum)


if __name__ == "__main__":
    import signal

    try:
        os.nice(10)
    except OSError:
        pass
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.signal(signal.SIGHUP, _on_sigterm)
    sys.exit(main())

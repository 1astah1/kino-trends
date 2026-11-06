"""Local Russian ASR: GigaAM v3 e2e CTC int8 through onnx-asr + Silero VAD (docs/ARCHITECTURE.md §6.3.2).

    provider = GigaamOnnxProvider(threads=2)          # MODELS_DIR from env or var/models
    tr = provider.transcribe("audio16k.wav", "ru", progress=print)
    tr.save("transcript.json.gz")

How it works (RSS stays flat for any source length):

1. Input that is not a 16 kHz mono PCM WAV is converted with ffmpeg into a temporary one.
2. The WAV is read in blocks of `block_sec` (600 s) plus a `lookahead_sec` (30 s) tail.
3. onnx-asr runs Silero VAD on the block and cuts speech into segments <= `max_speech_sec` (20 s),
   recognizes them in batches of `batch_size` with token timestamps and logprobs.
4. Segments that *start* inside the block are kept; the next block starts where the last kept
   segment ended, so no segment is cut by a block boundary.
5. Tokens -> words (`base.words_from_tokens`), words -> sentences (`Transcript.from_words`).

ONNX Runtime runs with intra_op=threads (2) and inter_op=1 (§8.1). Nothing is downloaded at runtime:
missing files raise `AsrUnavailable` with the `scripts/models_fetch.py` hint.

`transcribe_range(path, start, end)` recognizes one slice (for `source_transcribe_chunk` tasks that
split long sources by ~10 minutes, §6.1); the caller concatenates words and builds sentences once.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.providers.asr.base import (
    AsrUnavailable,
    ProgressFn,
    emit_progress,
    ffmpeg_audio,
    normalize_language,
    wav_info,
    words_from_tokens,
)
from autoshorts.providers.asr.models import GIGAAM_DIRNAME, missing_files, models_dir

log = logging.getLogger(__name__)

MODEL_NAME = "gigaam-v3-e2e-ctc"
SAMPLE_RATE = 16_000

# Calibrated on var/bench/10min_ru.wav (scripts/bench_asr.py) against edge-tts WordBoundary timings shifted
# by the measured mp3 priming (+0.13..0.155 s to the real onset): start MAE 35-50 ms with a slight early
# bias (safer lead for cuts), end MAE 40-55 ms. Defaults before calibration gave 68 / 94 ms.
WORD_TIMING = {"start_shift": 0.0, "char_sec": 0.09, "min_tail": 0.18, "max_tail": 0.5, "gap": 0.0}


@dataclass(slots=True)
class RangeResult:
    words: list[Word]
    vad: list[tuple[float, float]] = field(default_factory=list)
    audio_sec: float = 0.0
    wall_sec: float = 0.0
    start: float = 0.0
    stop: float = 0.0
    next_start: float = 0.0  # end of the last speech segment taken (>= stop): where the next chunk starts


class GigaamOnnxProvider:
    provider_key = "gigaam_v3_onnx"
    model_label = f"{MODEL_NAME}-int8"

    def __init__(
        self,
        models_root: str | Path | None = None,
        *,
        threads: int = 2,
        quantization: str | None = "int8",
        block_sec: float = 600.0,
        lookahead_sec: float = 30.0,
        max_speech_sec: float = 20.0,
        min_silence_ms: float = 150.0,
        speech_pad_ms: float = 30.0,
        vad_threshold: float = 0.5,
        batch_size: int = 4,
        check_sha: bool = False,
        tmp_dir: str | Path | None = None,
        word_timing: dict[str, float] | None = None,
    ) -> None:
        self.root = Path(models_root) if models_root else models_dir()
        self.threads = max(1, int(threads))
        self.quantization = quantization
        self.block_sec = float(block_sec)
        self.lookahead_sec = max(float(lookahead_sec), float(max_speech_sec) + 2.0)
        self.vad_options: dict[str, Any] = {
            "max_speech_duration_s": float(max_speech_sec),
            "min_silence_duration_ms": float(min_silence_ms),
            "speech_pad_ms": float(speech_pad_ms),
            "threshold": float(vad_threshold),
            "batch_size": int(batch_size),
        }
        self.tmp_dir = Path(tmp_dir) if tmp_dir else None
        self.word_timing = {**WORD_TIMING, **(word_timing or {})}
        groups = ["asr", "vad"]
        if quantization != "int8":
            raise AsrUnavailable("only the int8 GigaAM files are pinned in providers/asr/models.py")
        bad = missing_files(groups, self.root, check_sha=check_sha)
        if bad:
            raise AsrUnavailable(
                f"GigaAM/Silero model files missing or corrupt under {self.root}: {bad}. "
                "Run: python scripts/models_fetch.py"
            )
        self._adapter: Any = None

    # ------------------------------------------------------------------ model

    def _session_options(self, intra: int) -> Any:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.intra_op_num_threads = intra
        so.inter_op_num_threads = 1
        so.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        so.log_severity_level = 3
        return so

    def _load(self) -> Any:
        if self._adapter is not None:
            return self._adapter
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # never reach the network from the runtime
        import onnx_asr

        cpu = ["CPUExecutionProvider"]
        model = onnx_asr.load_model(
            MODEL_NAME,
            self.root / GIGAAM_DIRNAME,
            quantization=self.quantization,
            sess_options=self._session_options(self.threads),
            providers=cpu,
        )
        vad = onnx_asr.load_vad("silero", self.root, sess_options=self._session_options(1), providers=cpu)
        self._adapter = model.with_vad(vad, **self.vad_options).with_timestamps()
        return self._adapter

    # ------------------------------------------------------------------ public API

    def transcribe(
        self, audio_path: str | Path, language: str = "ru", progress: ProgressFn | None = None
    ) -> Transcript:
        lang = normalize_language(language)
        if lang != "ru":
            log.warning("GigaAM v3 is a Russian model; language=%s will be transcribed as Russian", lang)
        started = time.monotonic()
        with self._as_wav16k(Path(audio_path)) as wav:
            info = wav_info(wav)
            assert info is not None
            res = self._run(wav, 0.0, info.duration, info.duration, progress)
        wall = time.monotonic() - started
        duration = info.duration
        return Transcript.from_words(
            res.words,
            language=lang,
            duration=round(duration, 3),
            provider=self.provider_key,
            model=self.model_label,
            vad=res.vad,
            rtfx=round(duration / wall, 2) if wall > 0 else None,
            cost_usd=0.0,
        )

    def transcribe_range(
        self, audio_path: str | Path, start_sec: float, end_sec: float, progress: ProgressFn | None = None
    ) -> RangeResult:
        """Words and VAD segments whose speech *starts* in [start_sec, end_sec) (absolute times).

        The last segment may end after `end_sec`; `RangeResult.next_start` says where the next chunk
        should begin so that nothing is recognized twice.
        """
        t0 = time.monotonic()
        with self._as_wav16k(Path(audio_path)) as wav:
            info = wav_info(wav)
            assert info is not None
            end = min(float(end_sec), info.duration)
            res = self._run(wav, max(0.0, float(start_sec)), end, info.duration, progress)
        res.wall_sec = time.monotonic() - t0
        return res

    # ------------------------------------------------------------------ internals

    def _run(
        self, wav: Path, start: float, stop: float, total: float, progress: ProgressFn | None
    ) -> RangeResult:
        import soundfile as sf

        adapter = self._load()
        words: list[Word] = []
        vad: list[tuple[float, float]] = []
        pos = start
        covered = start
        span = max(stop - start, 1e-6)
        with sf.SoundFile(str(wav)) as fh:
            while pos < stop - 1e-3:
                main_end = min(pos + self.block_sec, stop)
                read_end = min(main_end + self.lookahead_sec, total)
                fh.seek(int(round(pos * SAMPLE_RATE)))
                n = int(round((read_end - pos) * SAMPLE_RATE))
                block = fh.read(n, dtype="float32", always_2d=False)
                if block.ndim > 1:
                    block = block.mean(axis=1).astype("float32")
                if block.size < SAMPLE_RATE // 10:
                    break
                tail_open = main_end < total - 1e-3  # audio continues after this block / range
                next_pos = main_end
                for seg in adapter.recognize(block, sample_rate=SAMPLE_RATE):
                    seg_start = pos + float(seg.start)
                    seg_end = pos + float(seg.end)
                    if tail_open and seg_start >= main_end - 1e-6:
                        break  # starts in the look-ahead: the next block (or range) takes it
                    vad.append((round(seg_start, 3), round(seg_end, 3)))
                    next_pos = max(next_pos, seg_end)
                    covered = max(covered, seg_end)
                    if seg.tokens and seg.timestamps:
                        words.extend(
                            words_from_tokens(
                                seg.tokens,
                                seg.timestamps,
                                seg.logprobs,
                                offset=seg_start,
                                seg_end=seg_end,
                                **self.word_timing,
                            )
                        )
                pos = next_pos
                emit_progress(progress, (min(pos, stop) - start) / span)
        words.sort(key=lambda w: (w.s, w.e))
        return RangeResult(
            words=words,
            vad=vad,
            audio_sec=stop - start,
            start=start,
            stop=stop,
            next_start=round(max(stop, covered), 3),
        )

    def _as_wav16k(self, path: Path) -> _WavContext:
        if not path.is_file():
            raise AsrUnavailable(f"audio file not found: {path}")
        info = wav_info(path)
        if info and info.sample_rate == SAMPLE_RATE and info.channels == 1 and info.sample_width == 2:
            return _WavContext(path, cleanup=False)
        tmp_root = self.tmp_dir or Path(tempfile.gettempdir())
        tmp_root.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="asr16k_", suffix=".wav", dir=tmp_root)
        os.close(fd)
        ffmpeg_audio(path, tmp_name, ["-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le"])
        return _WavContext(Path(tmp_name), cleanup=True)


class _WavContext:
    def __init__(self, path: Path, *, cleanup: bool) -> None:
        self.path = path
        self.cleanup = cleanup

    def __enter__(self) -> Path:
        return self.path

    def __exit__(self, *exc: object) -> None:
        if self.cleanup:
            self.path.unlink(missing_ok=True)


def transcript_from_ranges(
    results: list[RangeResult], *, duration: float, language: str = "ru", model: str | None = None
) -> Transcript:
    """Join `transcribe_range` results of chunk tasks into one transcript (sentences built once).

    Chunk tasks should run in order and start each chunk at the previous `next_start` (the last speech
    segment of a chunk may run past its nominal end). If nominal ranges were used anyway, words of a
    later chunk that start before the previous chunk's `next_start` are dropped as duplicates.
    """
    words: list[Word] = []
    vad: list[tuple[float, float]] = []
    boundary = float("-inf")
    for r in sorted(results, key=lambda r: r.start):
        words.extend(w for w in r.words if w.s >= boundary)
        vad.extend(seg for seg in r.vad if seg[0] >= boundary)
        boundary = max(boundary, r.next_start)
    words.sort(key=lambda w: (w.s, w.e))
    vad.sort()
    wall = sum(r.wall_sec for r in results)
    return Transcript.from_words(
        words,
        language=normalize_language(language),
        duration=round(duration, 3),
        provider=GigaamOnnxProvider.provider_key,
        model=model or GigaamOnnxProvider.model_label,
        vad=vad,
        rtfx=round(duration / wall, 2) if wall > 0 else None,
        cost_usd=0.0,
    )


def iter_ranges(duration: float, chunk_sec: float = 600.0) -> Iterator[tuple[float, float]]:
    """Nominal [start, end) ranges for `source_transcribe_chunk` tasks (~10 min each, §6.1)."""
    pos = 0.0
    while pos < duration:
        end = min(pos + chunk_sec, duration)
        yield pos, end
        pos = end


__all__ = [
    "MODEL_NAME",
    "WORD_TIMING",
    "GigaamOnnxProvider",
    "RangeResult",
    "iter_ranges",
    "transcript_from_ranges",
]

"""Yandex SpeechKit API v3, asynchronous file recognition (docs/ARCHITECTURE.md §6.3.2 `yandex_speechkit`).

Off by default (OWNER-DECISIONS 2026-09-26: local ASR, SpeechKit disabled). Implemented against the
documented REST API; covered by unit tests with mocked HTTP only.

Flow:
    POST {stt}/stt/v3/recognizeFileAsync          body RecognizeFileRequest -> Operation {id, done}
    GET  {operation}/operations/{id}              until done=true (or error)
    GET  {stt}/stt/v3/getRecognition?operationId  stream of StreamingResponse JSON objects

Auth: `Authorization: Api-Key <YANDEX_SPEECHKIT_API_KEY>` (service account key) or an IAM token
(`iam_token=` -> `Bearer`); `x-folder-id` is sent when a folder id is configured.

Audio: the file is re-encoded to OGG/Opus mono 16 kHz (~7 MB per hour at 16 kbit/s) and sent inline
as base64 `content`. Larger payloads must be uploaded to Object Storage by the caller and passed as
`uri` (`transcribe_uri`). Models: `deferred-general` (~9 ₽/h, results within hours) or `general`
(async, ~36 ₽/h). Results: `final.alternatives[0].words[{text,startTimeMs,endTimeMs}]` give the
timings, `finalRefinement.normalizedText` gives punctuation and case, which is copied onto the
timed words (`attach_punctuation`) so that sentences can be split on `. ? !`.

For task-based use (`source_transcribe_cloud`, light class): `submit()` returns the operation id to
persist, `poll()` / `fetch()` resume later without blocking a worker for hours.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.providers.asr.base import (
    AsrBadResponse,
    AsrTransient,
    AsrUnavailable,
    ProgressFn,
    attach_punctuation,
    emit_progress,
    ffmpeg_audio,
    ffprobe_duration,
    normalize_language,
)

STT_URL = "https://stt.api.cloud.yandex.net:443"
OPERATION_URL = "https://operation.api.cloud.yandex.net"
RUB_PER_HOUR = {"deferred-general": 9.14, "general": 36.36}
DEFAULT_MAX_INLINE_BYTES = 10 * 1024 * 1024
LANG_CODES = {"ru": "ru-RU", "en": "en-US", "kk": "kk-KZ", "uz": "uz-UZ", "tr": "tr-TR", "de": "de-DE"}


def build_request(
    *,
    content_b64: str | None = None,
    uri: str | None = None,
    container: str = "OGG_OPUS",
    model: str = "deferred-general",
    language: str = "ru",
    literature_text: bool = True,
) -> dict[str, Any]:
    """RecognizeFileRequest body (exactly one of content/uri)."""
    if (content_b64 is None) == (uri is None):
        raise ValueError("exactly one of content_b64 or uri is required")
    lang = normalize_language(language)
    body: dict[str, Any] = {"content": content_b64} if content_b64 is not None else {"uri": uri}
    body["recognitionModel"] = {
        "model": model,
        "audioFormat": {"containerAudio": {"containerAudioType": container}},
        "textNormalization": {
            "textNormalization": "TEXT_NORMALIZATION_ENABLED",
            "profanityFilter": False,
            "literatureText": bool(literature_text),
        },
        "languageRestriction": {
            "restrictionType": "WHITELIST",
            "languageCode": [LANG_CODES.get(lang, f"{lang}-{lang.upper()}")],
        },
        "audioProcessingType": "FULL_DATA",
    }
    return body


def iter_events(payload: str | bytes | list[Any] | dict[str, Any]) -> Iterator[dict[str, Any]]:
    """StreamingResponse objects from getRecognition output.

    Accepts newline-delimited JSON, concatenated JSON objects, a JSON array, or an already parsed
    object/list; each item may be wrapped as {"result": {...}} (REST streaming) or bare.
    """
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    items: list[Any]
    if isinstance(payload, str):
        items = []
        dec = json.JSONDecoder()
        i, n = 0, len(payload)
        while i < n:
            while i < n and payload[i] in " \t\r\n,":
                i += 1
            if i >= n:
                break
            obj, i = dec.raw_decode(payload, i)
            items.extend(obj if isinstance(obj, list) else [obj])
    elif isinstance(payload, list):
        items = payload
    else:
        items = [payload]
    for it in items:
        if not isinstance(it, dict):
            continue
        if "error" in it and "result" not in it:
            raise AsrBadResponse(f"SpeechKit stream error: {it['error']}")
        yield it.get("result", it)


def _ms(v: Any) -> float:
    return float(v) / 1000.0


def _alt_words(alt: dict[str, Any]) -> list[Word]:
    out = []
    for w in alt.get("words") or []:
        text = str(w.get("text", "")).strip()
        if not text:
            continue
        conf = w.get("confidence")
        out.append(
            Word(
                text,
                _ms(w.get("startTimeMs", 0)),
                _ms(w.get("endTimeMs", 0)),
                None if conf is None else float(conf),
            )
        )
    return out


def parse_recognition(events: Iterable[dict[str, Any]], channel: str | None = None) -> list[Word]:
    """Timed words with punctuation from SpeechKit v3 StreamingResponse events."""
    finals: dict[tuple[str, int], dict[str, Any]] = {}
    refined: dict[tuple[str, int], dict[str, Any]] = {}
    seq = 0
    for ev in events:
        ch = str(ev.get("channelTag", "0"))
        if "final" in ev:
            fin = ev["final"] or {}
            ch = str(fin.get("channelTag", ch))
            cur = ev.get("audioCursors") or {}
            idx = int(cur.get("finalIndex", seq))
            seq = idx + 1
            alts = fin.get("alternatives") or []
            if alts:
                finals[(ch, idx)] = alts[0]
        if "finalRefinement" in ev:
            fr = ev["finalRefinement"] or {}
            nt = fr.get("normalizedText") or {}
            ch = str(nt.get("channelTag", ch))
            idx = int(fr.get("finalIndex", 0))
            alts = nt.get("alternatives") or []
            if alts:
                refined[(ch, idx)] = alts[0]
    channels = sorted({k[0] for k in finals} | {k[0] for k in refined})
    if not channels:
        return []
    use = channel if channel in channels else channels[0]
    words: list[Word] = []
    for key in sorted(
        set(k for k in finals if k[0] == use) | set(k for k in refined if k[0] == use), key=lambda k: k[1]
    ):
        raw = _alt_words(finals.get(key, {}))
        ref = refined.get(key)
        if raw:
            if ref and ref.get("text"):
                raw = attach_punctuation(raw, str(ref["text"]))
            words.extend(raw)
        elif ref:
            words.extend(_alt_words(ref))
    words.sort(key=lambda w: (w.s, w.e))
    return words


class YandexSpeechKitProvider:
    provider_key = "yandex_speechkit"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        iam_token: str | None = None,
        folder_id: str | None = None,
        model: str = "deferred-general",
        poll_interval_sec: float | None = None,
        timeout_sec: float | None = None,
        max_inline_bytes: int = DEFAULT_MAX_INLINE_BYTES,
        fx_usd_rub: float | None = None,
        client: Any = None,
        tmp_dir: str | Path | None = None,
        stt_url: str = STT_URL,
        operation_url: str = OPERATION_URL,
        sleep: Any = time.sleep,
    ) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("YANDEX_SPEECHKIT_API_KEY")
        self.iam_token = iam_token
        if not (self.api_key or self.iam_token):
            raise AsrUnavailable("YANDEX_SPEECHKIT_API_KEY is not set")
        self.folder_id = folder_id if folder_id is not None else os.environ.get("YANDEX_FOLDER_ID")
        if model not in RUB_PER_HOUR:
            raise ValueError(f"model must be one of {sorted(RUB_PER_HOUR)}")
        self.model = model
        deferred = model == "deferred-general"
        self.poll_interval_sec = poll_interval_sec or (60.0 if deferred else 5.0)
        self.timeout_sec = timeout_sec or (36 * 3600.0 if deferred else 3 * 3600.0)
        self.max_inline_bytes = max_inline_bytes
        self.fx_usd_rub = float(fx_usd_rub or os.environ.get("FX_USD_RUB") or 90.0)
        self._client = client
        self.tmp_dir = Path(tmp_dir) if tmp_dir else None
        self.stt_url = stt_url.rstrip("/")
        self.operation_url = operation_url.rstrip("/")
        self._sleep = sleep

    # ------------------------------------------------------------------ http

    def _http(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.Client(timeout=httpx.Timeout(60.0, connect=15.0))
        return self._client

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Api-Key {self.api_key}" if self.api_key else f"Bearer {self.iam_token}"}
        if self.folder_id:
            h["x-folder-id"] = self.folder_id
        return h

    def _request(self, method: str, url: str, **kw: Any) -> Any:
        import httpx

        try:
            resp = self._http().request(method, url, headers=self._headers(), **kw)
        except (httpx.TransportError, httpx.TimeoutException) as exc:
            raise AsrTransient(f"SpeechKit network error: {exc}") from exc
        if resp.status_code == 429 or resp.status_code >= 500:
            raise AsrTransient(f"SpeechKit HTTP {resp.status_code}: {resp.text[:300]}")
        if resp.status_code >= 400:
            raise AsrBadResponse(f"SpeechKit HTTP {resp.status_code}: {resp.text[:300]}")
        return resp

    # ------------------------------------------------------------------ steps

    def submit(self, body: dict[str, Any]) -> str:
        resp = self._request("POST", f"{self.stt_url}/stt/v3/recognizeFileAsync", json=body)
        op = resp.json()
        op_id = op.get("id")
        if not op_id:
            raise AsrBadResponse(f"SpeechKit: no operation id in {str(op)[:300]}")
        return str(op_id)

    def poll(self, operation_id: str) -> bool:
        """True when the operation is done; raises AsrBadResponse when it finished with an error."""
        op = self._request("GET", f"{self.operation_url}/operations/{operation_id}").json()
        if op.get("error"):
            raise AsrBadResponse(f"SpeechKit operation {operation_id} failed: {op['error']}")
        return bool(op.get("done"))

    def fetch(self, operation_id: str) -> list[Word]:
        resp = self._request(
            "GET", f"{self.stt_url}/stt/v3/getRecognition", params={"operationId": operation_id}
        )
        return parse_recognition(iter_events(resp.text))

    def wait(self, operation_id: str, progress: ProgressFn | None = None) -> None:
        started = time.monotonic()
        while not self.poll(operation_id):
            elapsed = time.monotonic() - started
            if elapsed > self.timeout_sec:
                raise AsrTransient(f"SpeechKit operation {operation_id} not done after {elapsed:.0f}s")
            emit_progress(progress, min(0.9, 0.1 + 0.8 * elapsed / self.timeout_sec))
            self._sleep(self.poll_interval_sec)

    # ------------------------------------------------------------------ public

    def transcribe(
        self, audio_path: str | Path, language: str = "ru", progress: ProgressFn | None = None
    ) -> Transcript:
        path = Path(audio_path)
        if not path.is_file():
            raise AsrUnavailable(f"audio file not found: {path}")
        duration = ffprobe_duration(path) or 0.0
        tmp_root = self.tmp_dir or Path(tempfile.gettempdir())
        tmp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="speechkit_", dir=tmp_root) as td:
            ogg = ffmpeg_audio(
                path, Path(td) / "audio.ogg", ["-ar", "16000", "-c:a", "libopus", "-b:a", "16k"]
            )
            data = ogg.read_bytes()
        if len(data) > self.max_inline_bytes:
            raise AsrUnavailable(
                f"audio is {len(data) / 1e6:.1f} MB after OGG/Opus, "
                f"inline limit {self.max_inline_bytes / 1e6:.0f} MB: "
                "upload it to Object Storage and call transcribe_uri()"
            )
        body = build_request(
            content_b64=base64.b64encode(data).decode("ascii"), model=self.model, language=language
        )
        return self._run(body, duration, language, progress)

    def transcribe_uri(
        self,
        uri: str,
        duration: float,
        language: str = "ru",
        container: str = "OGG_OPUS",
        progress: ProgressFn | None = None,
    ) -> Transcript:
        body = build_request(uri=uri, container=container, model=self.model, language=language)
        return self._run(body, duration, language, progress)

    def _run(
        self, body: dict[str, Any], duration: float, language: str, progress: ProgressFn | None
    ) -> Transcript:
        started = time.monotonic()
        emit_progress(progress, 0.05)
        op_id = self.submit(body)
        self.wait(op_id, progress)
        words = self.fetch(op_id)
        emit_progress(progress, 1.0)
        duration = max(duration, words[-1].e if words else 0.0)
        wall = time.monotonic() - started
        return Transcript.from_words(
            words,
            language=normalize_language(language),
            duration=round(duration, 3),
            provider=self.provider_key,
            model=self.model,
            rtfx=round(duration / wall, 2) if wall > 0 and duration else None,
            cost_usd=round(self.cost_usd(duration), 6),
        )

    def cost_usd(self, duration_sec: float) -> float:
        return duration_sec / 3600.0 * RUB_PER_HOUR[self.model] / self.fx_usd_rub


__all__ = [
    "OPERATION_URL",
    "RUB_PER_HOUR",
    "STT_URL",
    "YandexSpeechKitProvider",
    "build_request",
    "iter_events",
    "parse_recognition",
]

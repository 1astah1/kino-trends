"""Clipping source intake: rights record first, then download/prepare (ARCHITECTURE.md §6.3.1, §1.2, §1.3).

    rights = {"rights_type": "own", "owner": "Umka", "proof_note": "our own stream 12.09"}
    src = ingest_local("stream.mp4", rights, out_root="var/media/sources", asr=provider)
    src = ingest_url("https://youtu.be/...", rights, out_root=...)

**No rights record — no source.** `validate_rights` runs before any file is touched:

* `rights_type`: own | licensed_written | licensed_platform | cc_by | public_domain (DB enum, §4.3);
  `licensed` is accepted as an alias of `licensed_written`;
* `owner_name` (alias `owner`) and `proof_note` (>= 3 chars) are required for every type;
* `attribution_text` is required for everything except `own` (DB CHECK in `source_rights`), which
  covers the CC BY rule "author, title, source, license";
* CC BY-SA, any NC (NonCommercial) and ND (NoDerivatives) licenses are **rejected**: SA and NC are
  incompatible with a promo banner (§0.1 п. 14), ND forbids the adapted clip itself;
* `valid_until` in the past is rejected.
Warnings (not errors) go to the originality report: Content ID allowlist not confirmed for licensed
content, license version unknown, platform license label that does not say Creative Commons, etc.

Preparation (heavy, `nice 10`, 2 threads, after a MemAvailable check): ffprobe + sha256 -> one-pass
540p proxy + audio.m4a + audio16k.wav (`pipeline.proxy`) -> peaks.json (`pipeline.peaks`) ->
storyboard sheets (`pipeline.storyboard`) -> foreign watermark check (`clipping.watermark_check`) ->
optional ASR (`providers.asr`) -> transcript.json.gz, audio16k.wav removed (§8.2).
Everything lands in `<out_root>/<source_id>/` (layout of MEDIA_ROOT/sources/<id>/, §8.2) together with
`rights.json` and `source.json` (the manifest the rest of the clipping track reads).

URL mode is audio-first (§6.3.1): yt-dlp `bestaudio[ext=m4a]/bestaudio`, then a <= 540p avc1 video for
the proxy; 5 MB/s rate limit, one fragment at a time, node as the JS runtime, cookies only when given.
1080p sections of approved candidates are fetched later by `clip_fetch` (not here).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[3]
MANIFEST_NAME = "source.json"
RIGHTS_NAME = "rights.json"
TRANSCRIPT_NAME = "transcript.json.gz"
MANIFEST_VERSION = 1

SOURCE_MAX_HOURS = 3.0  # settings.source_max_hours
UPLOAD_MAX_GB = 6.0  # settings.upload_max_gb
# порог MemAvailable для тяжёлых шагов (ASR, прокси): на общем боксе 1500 МБ, на маленьком стенде задаётся
# переменной AS_DEV_MIN_MEM_MB (например 800 на 2 ГБ VPS), иначе задачи вечно откладываются как mem_low
MIN_MEM_MB_DEFAULT = 1500
MIN_MEM_MB = int((os.environ.get("AS_DEV_MIN_MEM_MB") or "").strip() or MIN_MEM_MB_DEFAULT)


def min_mem_mb_now() -> int:
    """Порог на момент вызова: `.env` подгружается dotenv в config и может оказаться позже импорта
    этого модуля, поэтому константа выше — лишь умолчание, а здесь читается актуальное окружение."""
    raw = (os.environ.get("AS_DEV_MIN_MEM_MB") or "").strip()
    if not raw:
        _ensure_dotenv()
        raw = (os.environ.get("AS_DEV_MIN_MEM_MB") or "").strip()
    return int(raw) if raw.isdigit() else MIN_MEM_MB_DEFAULT


def _ensure_dotenv() -> None:
    """Воркер не импортирует autoshorts.config (там load_dotenv), а стенд не экспортирует .env в окружение."""
    try:
        import autoshorts.config  # noqa: F401, PLC0415 - побочный эффект: load_dotenv()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------- rights


class RightsType(StrEnum):
    OWN = "own"
    LICENSED_WRITTEN = "licensed_written"
    LICENSED_PLATFORM = "licensed_platform"
    CC_BY = "cc_by"
    PUBLIC_DOMAIN = "public_domain"


class AllowlistStatus(StrEnum):
    NOT_NEEDED = "not_needed"
    REQUESTED = "requested"
    ALLOWLISTED = "allowlisted"
    UNKNOWN = "unknown"


RIGHTS_ALIASES: dict[str, str] = {
    "licensed": "licensed_written",
    "license": "licensed_written",
    "licence": "licensed_written",
    "licensed_contract": "licensed_written",
    "platform": "licensed_platform",
    "cc-by": "cc_by",
    "ccby": "cc_by",
    "cc by": "cc_by",
    "public-domain": "public_domain",
    "pd": "public_domain",
}

# SA / NC / ND markers in license names, URLs and Russian labels
_BAD_LICENSE = [
    (
        "share_alike",
        re.compile(
            r"(by[-_ ]?(nc[-_ ]?)?sa\b|share[-_ ]?alike|licenses/by(-nc)?-sa|сохранени\w* услови)", re.I
        ),
    ),
    (
        "non_commercial",
        re.compile(r"(by[-_ ]?nc|cc[-_ ]?nc|non[-_ ]?commercial|licenses/by-nc|некоммерческ)", re.I),
    ),
    (
        "no_derivatives",
        re.compile(
            r"(by[-_ ]?(nc[-_ ]?)?nd\b|cc[-_ ]?nd|no[-_ ]?deriv|licenses/by(-nc)?-nd|без производн)", re.I
        ),
    ),
]
_CC_BY_OK = re.compile(
    r"(cc[-_ ]?by(?![-_ ]?(sa|nc|nd))|creative commons attribution(?! ?-? ?(share|non|no))|licenses/by/)",
    re.I,
)


class RightsError(ValueError):
    """Rights record rejected. `codes`: rights_missing | rights_invalid | attribution_required |
    license_incompatible | rights_expired."""

    def __init__(self, codes: list[str], message: str, details: list[str] | None = None) -> None:
        super().__init__(message)
        self.codes = codes
        self.details = details or []


class RightsRecord(BaseModel):
    """Mirror of `source_rights` (§4.2) plus optional license fields for CC sources."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, str_strip_whitespace=True)

    rights_type: RightsType
    owner_name: str = Field(min_length=1, max_length=300, alias="owner")
    proof_note: str = Field(min_length=3, max_length=4000)
    owner_channel_url: str | None = None
    proof_file_path: str | None = None
    allowlist_status: AllowlistStatus | None = None
    attribution_text: str | None = Field(default=None, max_length=1000)
    license: str | None = Field(default=None, max_length=200)  # e.g. "CC BY 4.0"
    license_url: str | None = None
    valid_until: date | None = None

    @field_validator("rights_type", mode="before")
    @classmethod
    def _alias_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            key = v.strip().lower()
            return RIGHTS_ALIASES.get(key, key.replace("-", "_"))
        return v

    @field_validator("attribution_text", "license", "license_url", "owner_channel_url", "proof_file_path")
    @classmethod
    def _empty_to_none(cls, v: str | None) -> str | None:
        return v or None

    def to_db_dict(self) -> dict[str, Any]:
        d = self.model_dump(mode="json", by_alias=False)
        d["allowlist_status"] = str(self.allowlist_status or AllowlistStatus.UNKNOWN)
        return d


def license_problems(*texts: str | None) -> list[str]:
    """Incompatible license markers (share_alike, non_commercial, no_derivatives) found in texts."""
    found: list[str] = []
    for text in texts:
        if not text:
            continue
        for code, rx in _BAD_LICENSE:
            if rx.search(text) and code not in found:
                found.append(code)
    return found


def validate_rights(
    data: dict[str, Any] | RightsRecord | None, *, today: date | None = None
) -> tuple[RightsRecord, list[str]]:
    """Validate a rights record. Returns (record, warnings); raises RightsError when it must be rejected."""
    if data is None or (isinstance(data, dict) and not data):
        raise RightsError(["rights_missing"], "rights record is required: no source without rights (§1.2)")
    if isinstance(data, RightsRecord):
        rec = data
    else:
        try:
            rec = RightsRecord.model_validate(data)
        except ValidationError as exc:
            details = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            raise RightsError(["rights_invalid"], "invalid rights record", details) from exc
    today = today or date.today()
    codes: list[str] = []
    details: list[str] = []
    warnings: list[str] = []
    rt = rec.rights_type

    if rt != RightsType.OWN and not rec.attribution_text:
        codes.append("attribution_required")
        details.append(f"attribution_text is required for rights_type={rt.value}")

    bad = license_problems(rec.license, rec.license_url, rec.attribution_text)
    if rt == RightsType.CC_BY:
        # the proof note is free text; only explicit license names count there
        bad += [
            b
            for b in license_problems(*re.findall(r"cc[-_ ]?by[-_ a-z]*", rec.proof_note, re.I))
            if b not in bad
        ]
    if bad:
        codes.append("license_incompatible")
        details.append(
            "license not accepted: "
            + ", ".join(bad)
            + " (CC BY-SA and NC conflict with the promo banner, ND forbids the adapted clip)"
        )
    if rt == RightsType.CC_BY and not bad:
        if not rec.license and not rec.license_url:
            warnings.append("license_version_unknown")
        elif not _CC_BY_OK.search(" ".join(filter(None, [rec.license, rec.license_url]))):
            warnings.append("license_not_recognized_as_cc_by")

    if rec.valid_until is not None and rec.valid_until < today:
        codes.append("rights_expired")
        details.append(f"valid_until {rec.valid_until.isoformat()} is in the past")

    if codes:
        raise RightsError(codes, "rights record rejected: " + ", ".join(codes), details)

    if rec.allowlist_status is None:
        rec.allowlist_status = AllowlistStatus.NOT_NEEDED if rt == RightsType.OWN else AllowlistStatus.UNKNOWN
    if rt in (RightsType.LICENSED_WRITTEN, RightsType.LICENSED_PLATFORM) and (
        rec.allowlist_status != AllowlistStatus.ALLOWLISTED
    ):
        warnings.append("content_id_allowlist_not_confirmed")
    if rt != RightsType.OWN:
        warnings.append("author_share_gate_applies")
    if rt == RightsType.CC_BY:
        warnings.append("cc_by_attribution_in_description")
    if rt == RightsType.PUBLIC_DOMAIN:
        warnings.append("public_domain_check_embedded_music")
    if rec.valid_until is not None and (rec.valid_until - today).days <= 30:
        warnings.append("rights_expire_within_30_days")
    return rec, warnings


# ---------------------------------------------------------------------- resources


class SourceRejected(ValueError):
    """Source cannot be accepted (too long, too big, no audio, ...). `code` names the reason."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InsufficientMemory(RuntimeError):
    """MemAvailable stayed below the threshold for the whole wait."""


def mem_available_mb() -> int | None:
    try:
        with open("/proc/meminfo", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def wait_for_memory(
    min_mb: int | None = None,
    *,
    timeout_sec: float = 600.0,
    poll_sec: float = 15.0,
    log: Callable[[str], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int | None:
    """Block until MemAvailable >= min_mb (shared box rule), up to `timeout_sec`. Returns the value seen.
    `min_mb=None` — the policy value resolved at call time (AS_DEV_MIN_MEM_MB or 1500),
    see `min_mem_mb_now`."""
    if min_mb is None:
        min_mb = min_mem_mb_now()
    deadline = time.monotonic() + timeout_sec
    while True:
        avail = mem_available_mb()
        if avail is None or avail >= min_mb:
            return avail
        if time.monotonic() >= deadline:
            raise InsufficientMemory(f"MemAvailable {avail} MB < {min_mb} MB for {timeout_sec:.0f}s")
        if log:
            log(f"MemAvailable {avail} MB < {min_mb} MB, waiting {poll_sec:.0f}s")
        sleep(poll_sec)


# ---------------------------------------------------------------------- manifest


@dataclass(slots=True)
class PreparedSource:
    source_id: str
    dir: Path
    title: str
    origin: str  # "upload" | "url"
    rights: dict[str, Any]
    rights_warnings: list[str]
    probe: dict[str, Any]
    sha256: str | None
    files: dict[str, Any]
    origin_url: str | None = None
    platform: dict[str, Any] = field(default_factory=dict)
    peaks: dict[str, Any] = field(default_factory=dict)
    storyboard: dict[str, Any] = field(default_factory=dict)
    watermark: dict[str, Any] | None = None
    transcript: dict[str, Any] | None = None
    timings: dict[str, float] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    status: str = "prepared"

    @property
    def watermark_suspect(self) -> bool:
        return bool(self.watermark and self.watermark.get("suspect"))

    def path(self, key: str) -> Path | None:
        rel = self.files.get(key)
        if not rel or not isinstance(rel, str):
            return None
        p = Path(rel)
        return p if p.is_absolute() else self.dir / p

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": MANIFEST_VERSION,
            "source_id": self.source_id,
            "title": self.title,
            "origin": self.origin,
            "origin_url": self.origin_url,
            "status": self.status,
            "created_at": self.created_at,
            "rights": self.rights,
            "rights_warnings": self.rights_warnings,
            "probe": self.probe,
            "sha256": self.sha256,
            "platform": self.platform,
            "files": self.files,
            "peaks": self.peaks,
            "storyboard": self.storyboard,
            "watermark": self.watermark,
            "watermark_suspect": self.watermark_suspect,
            "transcript": self.transcript,
            "timings": {k: round(v, 2) for k, v in self.timings.items()},
        }

    def save(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(self.dir / RIGHTS_NAME, self.rights)
        return _atomic_json(self.dir / MANIFEST_NAME, self.to_dict())

    @classmethod
    def load(cls, source_dir: str | Path) -> PreparedSource:
        d = Path(source_dir)
        data = json.loads((d / MANIFEST_NAME).read_text(encoding="utf-8"))
        if int(data.get("version", 1)) > MANIFEST_VERSION:
            raise ValueError("source manifest is newer than this code")
        return cls(
            source_id=data["source_id"],
            dir=d,
            title=data["title"],
            origin=data["origin"],
            origin_url=data.get("origin_url"),
            rights=data["rights"],
            rights_warnings=list(data.get("rights_warnings", [])),
            probe=data["probe"],
            sha256=data.get("sha256"),
            files=data.get("files", {}),
            platform=data.get("platform", {}),
            peaks=data.get("peaks", {}),
            storyboard=data.get("storyboard", {}),
            watermark=data.get("watermark"),
            transcript=data.get("transcript"),
            timings=data.get("timings", {}),
            created_at=data.get("created_at", ""),
            status=data.get("status", "prepared"),
        )


def _atomic_json(path: Path, data: Any) -> Path:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path


def default_sources_root() -> Path:
    raw = os.environ.get("MEDIA_ROOT", "").strip()
    base = Path(raw).expanduser() if raw else PROJECT_ROOT / "var" / "media"
    if not base.is_absolute():
        base = (PROJECT_ROOT / base).resolve()
    return base / "sources"


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,63}$")


# ---------------------------------------------------------------------- yt-dlp


def ytdlp_base_options(*, max_filesize: int | None = None, cookiefile: str | None = None) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "noprogress": True,
        "ratelimit": 5_000_000,
        "concurrent_fragment_downloads": 1,
        "retries": 3,
        "js_runtimes": {"node": {}},
    }
    if max_filesize:
        opts["max_filesize"] = int(max_filesize)
    # явный cookiefile — как передали (свой аккаунт, §6.3.1); из окружения берём только существующий файл
    env_cookie = (os.environ.get("YTDLP_COOKIEFILE") or "").strip()
    if cookiefile:
        opts["cookiefile"] = cookiefile
    elif env_cookie and os.path.isfile(env_cookie):
        opts["cookiefile"] = env_cookie
    return opts


def ytdlp_audio_options(out_dir: Path, **kw: Any) -> dict[str, Any]:
    return {
        **ytdlp_base_options(**kw),
        # завершающий /b — прямой mp4 (generic): отдельной аудиодорожки нет, берём файл целиком
        # русская дорожка первой: у роликов с несколькими дорожками (дубляж YouTube) по умолчанию
        # приходит оригинал — нам нужен русский дубляж, если он есть
        "format": "bestaudio[language^=ru][ext=m4a]/bestaudio[language^=ru]/bestaudio[ext=m4a]/bestaudio/b",
        "outtmpl": str(out_dir / "original_audio.%(ext)s"),
    }


def ytdlp_video_options(out_dir: Path, max_height: int = 540, **kw: Any) -> dict[str, Any]:
    return {
        **ytdlp_base_options(**kw),
        # завершающий /b — прямые ссылки на mp4 без метаданных высоты
        "format": (
            f"bv*[height<={max_height}][vcodec^=avc1]/bv*[height<={max_height}]/b[height<={max_height}]/b"
        ),
        "outtmpl": str(out_dir / "original_video.%(ext)s"),
    }


def _ydl(opts: dict[str, Any]) -> Any:
    import yt_dlp

    return yt_dlp.YoutubeDL(opts)


def fetch_url_info(
    url: str, *, cookiefile: str | None = None, ydl_factory: Callable[[dict], Any] = _ydl
) -> dict:
    opts = {**ytdlp_base_options(cookiefile=cookiefile), "skip_download": True}
    with ydl_factory(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise SourceRejected("url_unreadable", f"yt-dlp returned no info for {url}")
    if info.get("_type") == "playlist":
        raise SourceRejected("playlist", "playlists are not accepted: pass one video URL")
    return info


def _download(url: str, opts: dict[str, Any], out_dir: Path, stem: str, ydl_factory: Callable) -> Path:
    with ydl_factory(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    if path.is_file():
        return path
    cands = sorted(p for p in out_dir.glob(f"{stem}.*") if not p.name.endswith((".part", ".ytdl")))
    if not cands:
        raise SourceRejected("download_failed", f"yt-dlp produced no {stem} file for {url}")
    return cands[0]


def platform_summary(info: dict[str, Any]) -> dict[str, Any]:
    keys = ("id", "extractor_key", "webpage_url", "title", "uploader", "uploader_id", "channel",
            "channel_url", "channel_id", "license", "duration", "upload_date", "availability")  # fmt: skip
    return {k: info.get(k) for k in keys if info.get(k) is not None}


def platform_rights_warnings(rights: RightsRecord, info: dict[str, Any]) -> list[str]:
    """Cross-check the declared rights with what the platform page says."""
    warns: list[str] = []
    lic = str(info.get("license") or "")
    if rights.rights_type == RightsType.CC_BY:
        if not lic:
            warns.append("platform_license_missing")
        elif "creative commons" not in lic.lower():
            warns.append("platform_license_mismatch")
    if rights.owner_channel_url and info.get("channel_url"):
        a = str(rights.owner_channel_url).rstrip("/").lower()
        b = str(info["channel_url"]).rstrip("/").lower()
        if a != b:
            warns.append("owner_channel_differs_from_uploader")
    return warns


# ---------------------------------------------------------------------- preparation


def prepare_source(
    video_path: str | Path,
    out_dir: str | Path,
    *,
    audio_path: str | Path | None = None,
    sha256: str | None = None,
    logos_dir: str | Path | None = None,
    threads: int = 2,
    min_mem_mb: int | None = None,
    storyboard: bool = True,
    watermark: bool = True,
    scenes: bool = True,
    progress: Callable[[str, float], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """Heavy part of `source_prepare`: proxy + audio + peaks + storyboard + watermark check + scene cuts.

    Returns a dict with probe, sha256, files (relative to out_dir), peaks/storyboard meta, watermark
    report and wall timings per step.
    """
    from autoshorts.pipeline.peaks import write_peaks
    from autoshorts.pipeline.proxy import make_proxy, probe_media, sha256_file
    from autoshorts.pipeline.storyboard import make_storyboard

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}

    def step(name: str) -> Callable[[float], None]:
        return lambda f: progress(name, f) if progress else None

    t0 = time.monotonic()
    probe = probe_media(video_path)
    if sha256 is None:
        sha256 = sha256_file(video_path)
    timings["probe_sha"] = time.monotonic() - t0

    wait_for_memory(min_mem_mb)
    t0 = time.monotonic()
    px = make_proxy(
        video_path, out, audio_src=audio_path, probe=probe, compute_sha=False, threads=threads,
        progress_cb=step("proxy"), cancel=cancel, log_path=out / "prepare.log",
    )  # fmt: skip
    timings["proxy"] = time.monotonic() - t0
    files: dict[str, Any] = {}
    for key, p in (("proxy", px.proxy_path), ("audio", px.audio_path), ("audio16k", px.audio16k_path)):
        if p is not None:
            files[key] = p.name

    peaks_meta: dict[str, Any] = {}
    if px.audio16k_path is not None:
        t0 = time.monotonic()
        peaks_meta = write_peaks(px.audio16k_path, out / "peaks.json")
        peaks_meta["path"] = "peaks.json"
        files["peaks"] = "peaks.json"
        timings["peaks"] = time.monotonic() - t0

    sb_meta: dict[str, Any] = {}
    scenes_meta: dict[str, Any] = {}
    wm: dict[str, Any] | None = None
    if px.proxy_path is not None:
        duration = probe.duration or None
        if storyboard:
            t0 = time.monotonic()
            sb = make_storyboard(
                px.proxy_path, out, duration=duration, threads=threads, progress_cb=step("storyboard"),
                cancel=cancel, log_path=out / "prepare.log",
            )  # fmt: skip
            sb_meta = sb.to_dict()
            files["storyboard"] = sb_meta["sheets"]
            timings["storyboard"] = time.monotonic() - t0
        if watermark:
            from autoshorts.clipping.watermark_check import check_watermark

            t0 = time.monotonic()
            wm = check_watermark(px.proxy_path, logos_dir=logos_dir, duration=duration).to_dict()
            timings["watermark"] = time.monotonic() - t0
        if scenes:
            # склейки сцен для отбора моментов (маркеры «смена сцены» и снаппинг границ на монтаж)
            from autoshorts.pipeline.scenes import SCENES_NAME, detect_scene_cuts, write_scenes

            t0 = time.monotonic()
            if cancel is None or not cancel.is_set():
                cuts = detect_scene_cuts(px.proxy_path, duration=duration, threads=threads)
                write_scenes(out, cuts)
                files["scenes"] = SCENES_NAME
                scenes_meta = {"path": SCENES_NAME, "n": len(cuts)}
            timings["scenes"] = time.monotonic() - t0
    return {
        "probe": probe.to_dict(),
        "proxy_probe": {"size": list(px.proxy_size), "fps": px.proxy_fps},
        "sha256": sha256,
        "files": files,
        "peaks": peaks_meta,
        "storyboard": sb_meta,
        "scenes": scenes_meta,
        "watermark": wm,
        "timings": timings,
    }


def _transcribe_into(
    src: PreparedSource,
    asr: Any,
    language: str,
    keep_audio16k: bool,
    min_mem_mb: int,
    progress: Callable[[str, float], None] | None = None,
) -> None:
    wav = src.path("audio16k")
    if wav is None or not wav.is_file():
        raise SourceRejected("no_audio", "source has no audio track: nothing to transcribe")
    wait_for_memory(min_mem_mb)
    t0 = time.monotonic()
    tr = asr.transcribe(wav, language, progress=(lambda f: progress("asr", f)) if progress else None)
    out = tr.save(src.dir / TRANSCRIPT_NAME)
    src.timings["asr"] = time.monotonic() - t0
    src.files["transcript"] = out.name
    src.transcript = {
        "path": out.name,
        "provider": tr.provider,
        "model": tr.model,
        "language": tr.language,
        "words": len(tr.words),
        "sentences": len(tr.sentences),
        "duration": tr.duration,
        "rtfx": tr.rtfx,
        "cost_usd": tr.cost_usd,
    }
    if not keep_audio16k:
        wav.unlink(missing_ok=True)  # §8.2: audio16k.wav is removed right after ASR
        src.files.pop("audio16k", None)


def transcribe_prepared(
    src: PreparedSource,
    asr: Any,
    *,
    language: str = "ru",
    keep_audio16k: bool = False,
    min_mem_mb: int | None = None,
    progress: Callable[[str, float], None] | None = None,
) -> PreparedSource:
    """ASR for an already prepared source (e.g. prepared without `asr`, or re-run with another
    provider). audio16k.wav is rebuilt from the original (or the downloaded audio) if it was
    already removed — the same input as the proxy pass, so a re-run keeps the sentence ids."""
    wav = src.path("audio16k")
    if wav is None or not wav.is_file():
        from autoshorts.pipeline.ffmpeg_exec import run_media

        # Тот же вход, что у прохода прокси (оригинал / скачанный звук), а не audio.m4a: для не-AAC
        # источников audio.m4a — AAC 64k, и повторный ASR по нему даёт другой текст и сдвиг sid
        # (замер: 2476 → 2479 слов, 235 → 232 предложения на 16-минутном докладе).
        audio = next(
            (
                p
                for p in (src.path("original"), src.path("original_audio"), src.path("audio"))
                if p is not None and p.is_file()
            ),
            None,
        )
        if audio is None:
            raise SourceRejected("no_audio", "source has no audio file to transcribe")
        wav = src.dir / "audio16k.wav"
        tmp = src.dir / ".audio16k.tmp.wav"
        run_media(
            ["ffmpeg", "-y", "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
             str(tmp)],
            timeout_sec=3600, threads=2, nice=10,
        )  # fmt: skip
        os.replace(tmp, wav)
        src.files["audio16k"] = wav.name
    _transcribe_into(src, asr, language, keep_audio16k, min_mem_mb, progress)
    src.save()
    return src


def _check_limits(probe_duration: float, file_bytes: int, max_hours: float, max_gb: float) -> None:
    if probe_duration > max_hours * 3600.0:
        raise SourceRejected("too_long", f"source is {probe_duration / 3600:.2f} h, limit {max_hours:g} h")
    if file_bytes > max_gb * 1e9:
        raise SourceRejected("too_big", f"source is {file_bytes / 1e9:.2f} GB, limit {max_gb:g} GB")


def _link_original(path: Path, out_dir: Path) -> str:
    """Hard-link the upload as original.<ext> (no extra disk); fall back to referencing it in place."""
    dst = out_dir / f"original{path.suffix.lower()}"
    if dst.exists():
        return dst.name
    try:
        os.link(path, dst)
        return dst.name
    except OSError:
        return str(path.resolve())


def ingest_local(
    path: str | Path,
    rights: dict[str, Any] | RightsRecord | None,
    *,
    title: str | None = None,
    out_root: str | Path | None = None,
    source_id: str | None = None,
    asr: Any = None,
    language: str = "ru",
    keep_audio16k: bool = False,
    logos_dir: str | Path | None = None,
    threads: int = 2,
    min_mem_mb: int | None = None,
    max_hours: float = SOURCE_MAX_HOURS,
    max_gb: float = UPLOAD_MAX_GB,
    progress: Callable[[str, float], None] | None = None,
    cancel: threading.Event | None = None,
) -> PreparedSource:
    """Accept a local file with its rights record and prepare it (see module docstring)."""
    from autoshorts.pipeline.proxy import probe_media, sha256_file

    rec, warns = validate_rights(rights)  # before touching any file
    src_path = Path(path)
    if not src_path.is_file():
        raise SourceRejected("not_found", f"file not found: {src_path}")
    probe = probe_media(src_path)
    _check_limits(probe.duration, probe.file_bytes, max_hours, max_gb)
    if not probe.has_video:
        raise SourceRejected("no_video", "clipping needs a video source")
    digest = sha256_file(src_path)
    sid = source_id or f"src_{digest[:12]}"
    if not _SAFE_ID.match(sid):
        raise ValueError(f"unsafe source_id {sid!r}")
    out_dir = Path(out_root) if out_root else default_sources_root()
    out_dir = out_dir / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    original = _link_original(src_path, out_dir)

    prep = prepare_source(
        src_path, out_dir, sha256=digest, logos_dir=logos_dir, threads=threads, min_mem_mb=min_mem_mb,
        progress=progress, cancel=cancel,
    )  # fmt: skip
    src = PreparedSource(
        source_id=sid,
        dir=out_dir,
        title=title or src_path.stem,
        origin="upload",
        rights=rec.to_db_dict(),
        rights_warnings=warns,
        probe=prep["probe"],
        sha256=digest,
        files={"original": original, **prep["files"]},
        peaks=prep["peaks"],
        storyboard=prep["storyboard"],
        watermark=prep["watermark"],
        timings=prep["timings"],
    )
    if asr is not None:
        _transcribe_into(src, asr, language, keep_audio16k, min_mem_mb, progress)
    src.save()
    return src


def ingest_url(
    url: str,
    rights: dict[str, Any] | RightsRecord | None,
    *,
    title: str | None = None,
    out_root: str | Path | None = None,
    source_id: str | None = None,
    asr: Any = None,
    language: str = "ru",
    keep_audio16k: bool = False,
    logos_dir: str | Path | None = None,
    cookiefile: str | None = None,
    threads: int = 2,
    min_mem_mb: int | None = None,
    max_hours: float = SOURCE_MAX_HOURS,
    max_gb: float = UPLOAD_MAX_GB,
    ydl_factory: Callable[[dict], Any] = _ydl,
    progress: Callable[[str, float], None] | None = None,
    cancel: threading.Event | None = None,
) -> PreparedSource:
    """Accept an own/licensed video by URL: audio-first download, <= 540p video, then prepare."""
    rec, warns = validate_rights(rights)  # before any network access
    info = fetch_url_info(url, cookiefile=cookiefile, ydl_factory=ydl_factory)
    duration = float(info.get("duration") or 0.0)
    _check_limits(duration, 0, max_hours, max_gb)
    warns = warns + platform_rights_warnings(rec, info)
    vid = re.sub(r"[^A-Za-z0-9_-]", "_", str(info.get("id") or "url"))[:40]
    sid = source_id or f"url_{vid}"
    out_dir = (Path(out_root) if out_root else default_sources_root()) / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = int(max_gb * 1e9)

    t0 = time.monotonic()
    a_opts = ytdlp_audio_options(out_dir, max_filesize=max_bytes, cookiefile=cookiefile)
    audio = _download(url, a_opts, out_dir, "original_audio", ydl_factory)
    v_opts = ytdlp_video_options(out_dir, max_filesize=max_bytes, cookiefile=cookiefile)
    video = _download(url, v_opts, out_dir, "original_video", ydl_factory)
    t_download = time.monotonic() - t0

    prep = prepare_source(
        video, out_dir, audio_path=audio, logos_dir=logos_dir, threads=threads, min_mem_mb=min_mem_mb,
        progress=progress, cancel=cancel,
    )  # fmt: skip
    src = PreparedSource(
        source_id=sid,
        dir=out_dir,
        title=title or str(info.get("title") or sid),
        origin="url",
        origin_url=url,
        rights=rec.to_db_dict(),
        rights_warnings=warns,
        probe=prep["probe"],
        sha256=prep["sha256"],
        files={"original_audio": audio.name, "original_video": video.name, **prep["files"]},
        platform=platform_summary(info),
        peaks=prep["peaks"],
        storyboard=prep["storyboard"],
        watermark=prep["watermark"],
        timings={"download": t_download, **prep["timings"]},
    )
    if asr is not None:
        _transcribe_into(src, asr, language, keep_audio16k, min_mem_mb, progress)
    src.save()
    return src


def remove_source(source_dir: str | Path) -> None:
    """Delete a prepared source directory (hard-linked original stays in place at its old path)."""
    shutil.rmtree(Path(source_dir), ignore_errors=True)


__all__ = [
    "AllowlistStatus",
    "InsufficientMemory",
    "PreparedSource",
    "RightsError",
    "RightsRecord",
    "RightsType",
    "SourceRejected",
    "fetch_url_info",
    "ingest_local",
    "ingest_url",
    "transcribe_prepared",
    "license_problems",
    "mem_available_mb",
    "platform_rights_warnings",
    "prepare_source",
    "validate_rights",
    "wait_for_memory",
    "ytdlp_audio_options",
    "ytdlp_video_options",
]

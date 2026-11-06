#!/usr/bin/env python3
"""Нарезка без БД, очереди и API: источник → ASR → отбор → авторский слой → рендер → QC → отчёт.

    # локальный файл (права обязательны)
    python scripts/clip_local.py --video talk.mp4 --rights own --owner "Umka" --top 3

    # ссылка на своё или лицензированное видео (CC BY: лицензия и атрибуция обязательны)
    python scripts/clip_local.py --url "https://…" --rights cc_by --owner "Канал автора" \\
        --license "CC BY 3.0" --license-url https://creativecommons.org/licenses/by/3.0/ \\
        --attribution "«Название», Автор, ссылка, CC BY 3.0" --proof "лицензия на странице, 27.09.2026"

    # уже подготовленный источник (source_ingest: прокси, транскрипт, права, водяные знаки)
    python scripts/clip_local.py --source-dir var/media/sources/src_ab12 --top 2

    # свой фрагмент и свой сценарий автора (оператор), своя запись голоса, баннер
    python scripts/clip_local.py --source-dir DIR --clip 84.1:121.9 --author-script my.json \\
        --voice-file cold_open=open.m4a --banner creative.png --banner-slot offer

Шаги:
1. права проверяются до любых файлов и сети (`source_ingest.validate_rights`);
2. источник: `ingest_local` / загрузка по ссылке → прокси 540p, peaks, раскадровка, проверка чужих
   логотипов; подготовленный источник переиспользуется (`--sources-root`/<source_id>);
3. ASR (`--asr`, по умолчанию GigaAM локально) → transcript.json.gz; распознавание идёт в
   дочернем процессе, чтобы модель (~0,6 ГБ) не висела в памяти до конца рендера;
4. отбор: LLM, если задан LLM_API_KEY, иначе эвристика — режим подписан в выводе и в файлах;
5. на каждый из `--top N` кандидатов: сценарий автора (LLM / оператор / шаблон с пометкой), голос,
   EDL, рефрейм, субтитры, звук, рендер, QC, отчёт об оригинальности.

В каталоге `--out`:
    clip_01.mp4, clip_01.originality.json, clip_01.originality.md (по-русски), …
    candidates.json   — все кандидаты отбора и какие взяты в работу
    run.log           — ход работы и тайминги стадий
    summary.json      — сводка прогона (пути, тайминги, пиковый RSS)
    work/clip_01/     — промежуточные файлы (edl.json, clip.json, captions.ass, mix.m4a, …)

История нарезки источника (`<source_dir>/clipped.json`): какие предложения уже нарезаны, реплики
автора и отпечатки роликов. Отчёт об оригинальности сравнивает новый клип с ней (повтор момента,
повтор реплик, похожесть картинки). `--supersede ID` — клип заменяет прежний (переснятый дубль).

LLM (OpenAI-совместимый, по умолчанию Umka): LLM_BASE_URL, LLM_API_KEY, LLM_MODEL. Без ключа
отбор идёт эвристикой, авторский текст — шаблонным черновиком; оба помечены, отчёт об оригинальности
требует переписать черновик. `--llm mock` — детерминированная заглушка для проверки.

Ресурсы общего бокса: перед тяжёлыми шагами ждём MemAvailable ≥ 1500 МБ (до 10 мин), процесс и
ffmpeg — nice 10, ffmpeg -threads 2, ORT intra 2 / inter 1. Маскировки чужого контента нет.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import resource
import shutil
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

HISTORY_NAME = "clipped.json"
BANNER_SLOTS = {
    # зона оффера по умолчанию (research §7.2): x 65–890, y 1120–1370
    "offer": (65, 1120, 825, 190),
    # запасная верхняя полоса (§6.3.6, alt_position): y 230–480
    "top": (65, 230, 825, 190),
}


# ---------------------------------------------------------------------- журнал


class RunLog:
    """Печать в консоль и в run.log с отметкой времени от старта; тайминги стадий."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.t0 = time.monotonic()
        self.timings: dict[str, float] = {}
        path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = path.open("a", encoding="utf-8")
        self._fh.write(f"\n===== run {datetime.now(UTC).isoformat(timespec='seconds')} =====\n")

    def __call__(self, msg: str = "") -> None:
        stamp = f"[{time.monotonic() - self.t0:7.1f}s] "
        for line in str(msg).splitlines() or [""]:
            print(line, flush=True)
            self._fh.write(stamp + line + "\n")
        self._fh.flush()

    def timed(self, name: str) -> _Timer:
        return _Timer(self, name)

    def close(self) -> None:
        self._fh.close()


class _Timer:
    def __init__(self, log: RunLog, name: str) -> None:
        self.log, self.name = log, name

    def __enter__(self) -> _Timer:
        self.t = time.monotonic()
        return self

    def __exit__(self, *exc: Any) -> None:
        dt = time.monotonic() - self.t
        self.log.timings[self.name] = self.log.timings.get(self.name, 0.0) + dt
        self.log(f"  ⏱ {self.name}: {dt:.1f} с")


def _box_state() -> str:
    from autoshorts.clipping.source_ingest import mem_available_mb

    try:
        load = os.getloadavg()[0]
    except OSError:
        load = -1.0
    return f"MemAvailable {mem_available_mb()} МБ, load1 {load:.2f}"


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------- LLM


def _llm(kind: str) -> tuple[Any, Any, str]:
    """(клиент отбора, клиент автора, метка режима)."""
    from autoshorts.clipping.author.script import make_mock_author_client
    from autoshorts.clipping.llm_client import LLMClient
    from autoshorts.clipping.llm_select import make_mock_client

    if kind == "mock":
        return make_mock_client(), make_mock_author_client(), "mock LLM (заглушка, не для публикации)"
    if kind == "none":
        return None, None, "без LLM: эвристика отбора и шаблонный черновик автора"
    client = LLMClient.from_env()
    if client is None:
        if kind == "llm":
            raise SystemExit("LLM_API_KEY не задан, а --llm llm требует настоящую модель")
        return None, None, "без LLM (нет LLM_API_KEY): эвристика отбора и шаблонный черновик автора"
    return client, client, f"LLM {client.provider}/{client.model} @ {client.base_url}"


# ---------------------------------------------------------------------- источник


def _rights_raw(a: argparse.Namespace) -> dict[str, Any]:
    raw = {
        "rights_type": a.rights,
        "owner_name": a.owner,
        "proof_note": a.proof or ("own footage" if a.rights == "own" else None),
        "owner_channel_url": a.owner_channel,
        "attribution_text": a.attribution,
        "allowlist_status": a.allowlist,
        "license": a.license,
        "license_url": a.license_url,
        "valid_until": a.valid_until,
    }
    return {k: v for k, v in raw.items() if v not in (None, "")}


def _check_rights(a: argparse.Namespace, log: RunLog) -> tuple[Any, list[str]]:
    from autoshorts.clipping.source_ingest import RightsError, validate_rights

    if not a.rights or not a.owner:
        raise SystemExit("без записи о правах источник не принимается: --rights и --owner обязательны")
    try:
        rec, warns = validate_rights(_rights_raw(a))
    except RightsError as exc:
        log(f"ПРАВА ОТКЛОНЕНЫ: {', '.join(exc.codes)}")
        for d in exc.details:
            log(f"  - {d}")
        raise SystemExit(2) from exc
    log(f"Права: {rec.rights_type.value}, {rec.owner_name}; предупреждения: {', '.join(warns) or 'нет'}")
    return rec, warns


def _progress(log: RunLog) -> Any:
    last: dict[str, float] = {}

    def cb(step: str, f: float) -> None:
        prev = last.get(step, -1.0)
        if f >= 1.0 and prev >= 1.0:
            return
        if f <= 0.0 or f >= 1.0 or f - prev >= 0.25:
            last[step] = f
            log(f"    {step} {f * 100:3.0f}%")

    return cb


def _existing_source(root: Path, sid: str) -> Any:
    from autoshorts.clipping.source_ingest import PreparedSource

    d = root / sid
    if (d / "source.json").is_file():
        try:
            return PreparedSource.load(d)
        except Exception:  # noqa: BLE001
            return None
    return None


def _source_from_video(a: argparse.Namespace, log: RunLog) -> Any:
    from autoshorts.clipping.source_ingest import ingest_local
    from autoshorts.pipeline.proxy import sha256_file

    rec, _ = _check_rights(a, log)
    video = a.video.resolve()
    if not video.is_file():
        raise SystemExit(f"файл не найден: {video}")
    with log.timed("source_sha256"):
        sid = a.source_id or f"src_{sha256_file(video)[:12]}"
    prepared = _existing_source(a.sources_root, sid) if not a.reprepare else None
    if prepared is not None:
        log(f"Источник уже подготовлен: {prepared.dir} (переиспользую; --reprepare — заново)")
        _refresh_rights(prepared, rec, a, log)
        return prepared
    log(f"Подготовка источника {video.name} → {a.sources_root / sid}  ({_box_state()})")
    with log.timed("source_prepare"):
        prepared = ingest_local(
            video,
            rec.to_db_dict(),
            title=a.title or video.stem,
            out_root=a.sources_root,
            source_id=sid,
            progress=_progress(log),
        )
    return prepared


def _refresh_rights(prepared: Any, rec: Any, a: argparse.Namespace, log: RunLog) -> None:
    """Права из командной строки важнее сохранённых (например, уточнили атрибуцию)."""
    from autoshorts.clipping.source_ingest import platform_rights_warnings, validate_rights

    new = rec.to_db_dict()
    if new == prepared.rights:
        return
    _, warns = validate_rights(new)
    if prepared.platform:
        warns = warns + platform_rights_warnings(rec, prepared.platform)
    prepared.rights, prepared.rights_warnings = new, warns
    prepared.save()
    log("  запись о правах обновлена из аргументов")


def _source_from_url(a: argparse.Namespace, log: RunLog) -> Any:
    """Ссылка → один файл ≤ --max-height (с загрузкой звука и видео вместе) → ingest_local.

    Режим `--url-mode audio-first` — как в сервисе (§6.3.1): звук + видео ≤540p, рендер идёт с
    540p (для превью и отладки); полноценный рендер в сервисе берёт 1080p-секции (clip_fetch).
    """
    from autoshorts.clipping.source_ingest import (
        UPLOAD_MAX_GB,
        SourceRejected,
        fetch_url_info,
        ingest_local,
        ingest_url,
        platform_rights_warnings,
        platform_summary,
        ytdlp_base_options,
    )

    rec, rights_warns = _check_rights(a, log)  # до любой сети
    with log.timed("url_info"):
        info = fetch_url_info(a.url, cookiefile=a.cookies)
    plat = platform_summary(info)
    pwarn = platform_rights_warnings(rec, info)
    log(
        f"Ссылка: {plat.get('title')!r}, {plat.get('duration')} с, площадка {plat.get('extractor_key')}, "
        f"лицензия на странице: {plat.get('license') or 'не указана'}"
    )
    if pwarn:
        log(f"  расхождения с площадкой: {', '.join(pwarn)}")
    vid = re.sub(r"[^A-Za-z0-9_-]", "_", str(info.get("id") or "url"))[:40]
    sid = a.source_id or f"url_{vid}"
    prepared = _existing_source(a.sources_root, sid) if not a.reprepare else None
    if prepared is not None:
        log(f"Источник уже подготовлен: {prepared.dir} (переиспользую; --reprepare — заново)")
        _refresh_rights(prepared, rec, a, log)
        return prepared

    if a.url_mode == "audio-first":
        log(f"Загрузка audio-first (звук + видео ≤540p) и подготовка → {a.sources_root / sid}")
        with log.timed("source_download_prepare"):
            return ingest_url(
                a.url,
                rec.to_db_dict(),
                title=a.title,
                out_root=a.sources_root,
                source_id=sid,
                cookiefile=a.cookies,
                progress=_progress(log),
            )

    out_dir = a.sources_root / sid
    out_dir.mkdir(parents=True, exist_ok=True)
    h = int(a.max_height)
    opts = {
        **ytdlp_base_options(max_filesize=int(UPLOAD_MAX_GB * 1e9), cookiefile=a.cookies),
        "format": (f"bv*[height<={h}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={h}]+ba/b[height<={h}]/b"),
        "merge_output_format": "mp4/mkv",
        "outtmpl": str(out_dir / "download.%(ext)s"),
    }
    log(f"Загрузка ≤{h}p одним файлом → {out_dir}  ({_box_state()})")
    import yt_dlp

    with log.timed("source_download"), yt_dlp.YoutubeDL(opts) as ydl:
        dl_info = ydl.extract_info(a.url, download=True)
        path = Path(ydl.prepare_filename(dl_info))
    if not path.is_file():
        cands = sorted(p for p in out_dir.glob("download.*") if not p.name.endswith((".part", ".ytdl")))
        if not cands:
            raise SourceRejected("download_failed", f"yt-dlp не сохранил файл для {a.url}")
        path = cands[0]
    log(f"  скачано {path.stat().st_size / 1e6:.1f} МБ")
    with log.timed("source_prepare"):
        prepared = ingest_local(
            path,
            rec.to_db_dict(),
            title=a.title or plat.get("title") or sid,
            out_root=a.sources_root,
            source_id=sid,
            progress=_progress(log),
        )
    # оригинал уже захардлинкан как original.<ext>; временное имя убираем
    if prepared.path("original") is not None and prepared.path("original") != path:
        path.unlink(missing_ok=True)
    prepared.origin = "url"
    prepared.origin_url = a.url
    prepared.platform = plat
    prepared.rights_warnings = list(dict.fromkeys(rights_warns + pwarn))
    prepared.save()
    return prepared


_ASR_CHILD = """
import sys
sys.path.insert(0, sys.argv[1])
from autoshorts.clipping.source_ingest import PreparedSource, transcribe_prepared
from autoshorts.providers.asr import get_asr_provider

def progress(step, f):
    print(f"@@progress {f:.3f}", flush=True)

src = PreparedSource.load(sys.argv[2])
transcribe_prepared(src, get_asr_provider(sys.argv[3]), language=sys.argv[4], progress=progress)
"""


def _asr_in_child(prepared: Any, a: argparse.Namespace, log: RunLog) -> None:
    """ASR в отдельном процессе: модель (~0,6 ГБ) освобождается сразу после распознавания, а
    основной процесс не держит её до конца рендера (общий бокс, §8.1)."""
    import subprocess

    cmd = [sys.executable, "-c", _ASR_CHILD, str(ROOT / "src"), str(prepared.dir), a.asr, a.language]
    try:
        cur = os.nice(0)
    except OSError:
        cur = 10
    if cur < 10:
        cmd = ["nice", "-n", str(10 - cur), *cmd]
    prog = _progress(log)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip()
        if line.startswith("@@progress "):
            prog("asr", float(line.split()[1]))
        elif line:
            tail = (tail + [line])[-30:]
            if "WARN" not in line:
                log(f"    [asr] {line}")
    rc = proc.wait()
    if rc != 0:
        log("ASR упал:\n" + "\n".join(tail))
        raise SystemExit(f"ASR {a.asr} завершился с кодом {rc}")


def _ensure_transcript(prepared: Any, a: argparse.Namespace, log: RunLog) -> tuple[Any, Any]:
    """(транскрипт, подготовленный источник — перечитанный, если ASR его обновил)."""
    from autoshorts.clipping.source_ingest import PreparedSource, transcribe_prepared
    from autoshorts.clipping.transcript import Transcript

    if a.transcript:
        return Transcript.load(a.transcript), prepared
    tpath = prepared.path("transcript")
    if tpath is not None and tpath.is_file() and not a.reasr:
        tr = Transcript.load(tpath)
        log(
            f"Транскрипт уже есть: {tr.provider}/{tr.model}, {len(tr.words)} слов, "
            f"{len(tr.sentences)} предложений"
        )
        return tr, prepared
    log(f"ASR {a.asr} ({_box_state()}){' в этом процессе' if a.asr_inprocess else ''}")
    with log.timed("asr"):
        if a.asr_inprocess:
            from autoshorts.providers.asr import get_asr_provider

            transcribe_prepared(
                prepared, get_asr_provider(a.asr), language=a.language, progress=_progress(log)
            )
        else:
            _asr_in_child(prepared, a, log)
            prepared = PreparedSource.load(prepared.dir)
    tr = Transcript.load(prepared.path("transcript"))
    rtfx = f"RTFx {tr.rtfx:.1f}" if tr.rtfx else ""
    log(f"  {len(tr.words)} слов, {len(tr.sentences)} предложений, {tr.duration:.0f} с аудио {rtfx}")
    return tr, prepared


# ---------------------------------------------------------------------- история источника


def _load_history(prepared: Any) -> list[dict[str, Any]]:
    p = Path(prepared.dir) / HISTORY_NAME
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return list(data.get("clips") or [])
    except (OSError, ValueError):
        return []


def _save_history(prepared: Any, clips: list[dict[str, Any]]) -> None:
    p = Path(prepared.dir) / HISTORY_NAME
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(
            {"version": 1, "source_id": prepared.source_id, "clips": clips}, ensure_ascii=False, indent=1
        ),
        encoding="utf-8",
    )
    os.replace(tmp, p)


# ---------------------------------------------------------------------- баннер


def _banner_factory(a: argparse.Namespace, out: Path, log: RunLog) -> Any:
    """Фабрика плана баннера для produce_clip (длительность известна после раскладки)."""
    try:
        from autoshorts.domain.banner_schema import BannerLayerKind, BannerSlot, default_banner_variant
        from autoshorts.pipeline import banner_assets as ba
        from autoshorts.pipeline import banner_overlay as bo
        from autoshorts.pipeline import banner_place as bp
    except Exception as exc:  # noqa: BLE001
        log(f"Баннерный модуль недоступен ({type(exc).__name__}: {exc}); рендер без баннера")
        return None
    media_root = out / "banner_media"
    creative = ba.ingest_creative(a.banner, media_root)
    variant = default_banner_variant(
        creative_sha256=creative.sha256, erid=a.erid, ad_marking_required=bool(a.erid)
    )
    slot_spec = a.banner_slot
    if slot_spec in BANNER_SLOTS:
        x, y, w, h = BANNER_SLOTS[slot_spec]
    else:
        try:
            x, y, w, h = (int(v) for v in slot_spec.split(","))
        except ValueError as exc:
            raise SystemExit(f"--banner-slot: offer | top | x,y,w,h (получено {slot_spec!r})") from exc
    layers = []
    for la in variant.layers:
        if la.kind in (BannerLayerKind.CREATIVE, BannerLayerKind.FALLBACK_PLATE) and la.slot is not None:
            la = la.model_copy(
                update={"slot": BannerSlot(**{**la.slot.model_dump(), "x": x, "y": y, "w": w, "h": h})}
            )
        layers.append(la)
    variant = variant.model_copy(update={"layers": layers})
    profile = bp.load_safe_zone_catalog().get()
    log(
        f"Баннер: {a.banner.name} ({creative.kind.value}, {creative.width}×{creative.height}), слот "
        f"{x},{y},{w}×{h}, код {a.banner_code}{', erid ' + a.erid if a.erid else ''}"
    )

    def factory(*, duration_sec: float, fps: float, out_size: tuple[int, int], workdir: Path) -> Any:
        return bo.prepare_banner_plan(
            variant,
            {creative.sha256: creative},
            media_root=media_root,
            duration_sec=duration_sec,
            out_size=out_size,
            profile=profile,
            cache_dir=Path(workdir) / "banner_cache",
            fps=fps,
            code=a.banner_code,
            erid=a.erid,
        )

    return factory


def _parse_index_set(spec: str | None, n: int) -> set[int]:
    if not spec or spec == "all":
        return set(range(1, n + 1))
    return {int(x) for x in spec.split(",") if x.strip()}


# ---------------------------------------------------------------------- библиотека


def _library_from_paths(paths: list[Path], log: RunLog) -> list[tuple[str, Any]]:
    if not paths:
        return []
    from autoshorts.pipeline import similarity as sim

    lib: list[tuple[str, Any]] = []
    files: list[Path] = []
    for p in paths:
        files += sorted(p.glob("*.mp4")) if p.is_dir() else [p]
    for f in files:
        try:
            lib.append((f.name, sim.fingerprint_video(f)))
        except Exception as exc:  # noqa: BLE001
            log(f"  библиотека: пропуск {f.name}: {type(exc).__name__}")
    log(f"Своя библиотека для сравнения: {len(lib)} роликов")
    return lib


def _fingerprint_json(res: Any) -> Any:
    fp = (res.edl.meta or {}).get("fingerprint")
    if fp is not None:
        return fp
    if res.final_path is None:
        return None
    try:
        from autoshorts.pipeline import similarity as sim

        return sim.fingerprint_video(res.final_path).to_json()
    except Exception:  # noqa: BLE001
        return None


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.unlink(missing_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# ---------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--video", type=Path, help="локальный файл видео")
    src.add_argument("--url", help="ссылка на своё или лицензированное видео (yt-dlp)")
    src.add_argument("--source-dir", type=Path, help="каталог подготовленного источника (source.json)")
    g = p.add_argument_group("права (обязательны для --video/--url)")
    g.add_argument("--rights", help="own | licensed_written | licensed_platform | cc_by | public_domain")
    g.add_argument("--owner", help="правообладатель")
    g.add_argument("--owner-channel", default=None, help="URL канала правообладателя")
    g.add_argument("--proof", default=None, help="чем подтверждены права (договор, лицензия на странице)")
    g.add_argument("--attribution", default=None, help="текст атрибуции (обязателен, кроме own)")
    g.add_argument("--license", default=None, help='например "CC BY 4.0"')
    g.add_argument("--license-url", default=None)
    g.add_argument("--allowlist", default=None, help="not_needed | requested | allowlisted | unknown")
    g.add_argument("--valid-until", default=None, help="YYYY-MM-DD")
    s = p.add_argument_group("источник")
    s.add_argument("--title", default=None)
    s.add_argument("--source-id", default=None)
    s.add_argument("--sources-root", type=Path, default=None, help="по умолчанию MEDIA_ROOT/sources")
    s.add_argument("--reprepare", action="store_true", help="подготовить источник заново")
    s.add_argument("--url-mode", choices=("full", "audio-first"), default="full")
    s.add_argument("--max-height", type=int, default=1080, help="потолок высоты кадра при загрузке по ссылке")
    s.add_argument("--cookies", default=None, help="cookies только своего аккаунта")
    s.add_argument("--transcript", type=Path, help="готовый transcript.json.gz вместо ASR")
    s.add_argument(
        "--asr", default="gigaam_v3_onnx", help="gigaam_v3_onnx | yandex_speechkit | openai_compat_asr | mock"
    )
    s.add_argument("--reasr", action="store_true", help="распознать заново, даже если транскрипт есть")
    s.add_argument("--asr-inprocess", action="store_true", help="ASR в этом процессе")
    s.add_argument("--language", default="ru")
    c = p.add_argument_group("отбор и авторский слой")
    c.add_argument("--niche", default=None, help="id ниши (config/niches/<id>.yaml)")
    c.add_argument("--top", type=int, default=3, help="сколько лучших кандидатов отрендерить")
    c.add_argument("--clip", action="append", default=[], help="свой фрагмент START:END, с (можно несколько)")
    c.add_argument("--target-sec", type=float, default=30.0, help="целевая длина фрагмента исходника")
    c.add_argument("--llm", choices=("auto", "llm", "mock", "none"), default="auto")
    c.add_argument(
        "--author-script", action="append", type=Path, default=[],
        help="JSON сценария автора (§6.3.8) — по одному на клип, по порядку",
    )  # fmt: skip
    c.add_argument(
        "--voice-file", action="append", default=[],
        help="[N:]role=path — запись оператора (role: cold_open, insert_0, insert_1, outro)",
    )  # fmt: skip
    c.add_argument("--tts", choices=("edge", "mock"), default="edge")
    c.add_argument("--voice", default=None, help="голос edge-tts, по умолчанию ru-RU-DmitryNeural")
    c.add_argument("--reframe", choices=("auto", "track", "center", "fit_blur"), default="auto")
    c.add_argument("--library", action="append", type=Path, default=[], help="свои ролики (mp4 или каталог)")
    c.add_argument("--history", choices=("use", "ignore"), default="use", help="история нарезки источника")
    c.add_argument(
        "--supersede", action="append", default=[], help="id прежнего клипа, который этот заменяет"
    )
    b = p.add_argument_group("баннер (необязательно)")
    b.add_argument("--banner", type=Path, default=None, help="креатив PNG/JPG/WebP/GIF/WebM")
    b.add_argument("--banner-slot", default="offer", help="offer | top | x,y,w,h (канва 1080×1920)")
    b.add_argument("--banner-clips", default="all", help="номера клипов с баннером: 1,3 | all")
    b.add_argument("--banner-code", default="demo1", help="короткий код ролика на баннере")
    b.add_argument("--erid", default=None, help="erid рекламной маркировки (если это реклама)")
    o = p.add_argument_group("вывод")
    o.add_argument("--no-render", action="store_true", help="только отбор, сценарий, голос и EDL")
    o.add_argument("--out", type=Path, default=ROOT / "var" / "tmp" / "clip_local")
    return p


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    a.argv = list(sys.argv[1:] if argv is None else argv)
    a.out = a.out.resolve()
    a.out.mkdir(parents=True, exist_ok=True)
    log = RunLog(a.out / "run.log")
    try:
        return _run(a, log)
    except SystemExit:
        raise
    except BaseException:
        log("ОШИБКА:\n" + traceback.format_exc())
        raise
    finally:
        log.close()


def _run(a: argparse.Namespace, log: RunLog) -> int:
    _load_env()
    from autoshorts.clipping.produce import ClipJobConfig, ClipSource, produce_clip
    from autoshorts.clipping.selection import SelectionConfig, select_clips
    from autoshorts.clipping.source_ingest import PreparedSource, default_sources_root

    t_start = time.monotonic()
    log(f"clip_local: {' '.join(a.argv)}")
    log(f"Бокс: {_box_state()}")
    a.sources_root = (a.sources_root or default_sources_root()).resolve()

    # 1–2. источник
    if a.source_dir:
        prepared = PreparedSource.load(a.source_dir)
        log(f"Источник: {prepared.dir}")
    elif a.url:
        prepared = _source_from_url(a, log)
    else:
        prepared = _source_from_video(a, log)
    for k, v in (prepared.timings or {}).items():
        log.timings.setdefault(f"prepare.{k}", float(v))
    wm = prepared.watermark or {}
    log(
        f"Подготовка: прокси {prepared.files.get('proxy')}, раскадровка "
        f"{len(prepared.files.get('storyboard') or [])} листов, "
        f"водяной знак: {wm.get('severity', 'не проверялся')}"
    )

    # 3. транскрипт
    transcript, prepared = _ensure_transcript(prepared, a, log)
    source = ClipSource.from_prepared(prepared)
    niche = None
    if a.niche:
        from autoshorts.clipping.scoring import load_niche_profile

        niche = load_niche_profile(a.niche)

    sel_llm, author_llm, llm_label = _llm(a.llm)
    log(
        f"Источник: {source.render_path.name}, {source.duration:.0f} с, {source.width}×{source.height}; "
        f"права: {source.rights.get('rights_type')} ({source.rights.get('owner_name')})"
    )
    log(f"Режим: {llm_label}; голос автора: {a.tts}")

    # 4. отбор
    candidates_doc: dict[str, Any]
    if a.clip:
        chosen = []
        for spec in a.clip:
            s_, e_ = (float(x) for x in spec.split(":"))
            chosen.append(
                {"start": s_, "end": e_, "title": "", "hook_text": "", "keep": None, "origin": "manual"}
            )
        candidates_doc = {"mode": "manual", "label": "фрагменты заданы вручную (--clip)", "clips": chosen}
    else:
        cfg = SelectionConfig(target_clip_sec=a.target_sec, target_count=max(a.top, 3))
        with log.timed("selection"):
            res = select_clips(transcript, llm=sel_llm, niche=niche, config=cfg)
        log(
            f"Отбор: {res.label}; кандидатов {len(res.candidates)}, запросов LLM {res.n_requests}, "
            f"${res.cost_usd:.4f}"
        )
        for w in res.warnings:
            log(f"  ! {w}")
        ok = [c for c in res.candidates if not c.blocked][: a.top]
        chosen = [
            {
                "start": c.start_sec,
                "end": c.end_sec,
                "start_sid": c.start_sid,
                "end_sid": c.end_sid,
                "title": c.title,
                "hook_text": c.hook_text,
                "keep": c.keep_segments,
                "origin": c.origin,
                "score": c.final_score,
            }
            for c in ok
        ]
        cand_rows = []
        for rank, c in enumerate(res.candidates, 1):
            d = c.to_dict()
            d["rank"] = rank
            d["chosen_as"] = next(
                (f"clip_{i + 1:02d}" for i, ch in enumerate(chosen) if ch["start"] == c.start_sec), None
            )
            cand_rows.append(d)
            log(
                f"  #{rank} {c.start_sec:7.1f}–{c.end_sec:7.1f} ({c.duration:4.1f} с) "
                f"score {c.final_score:.3f}"
                f"{' БЛОК ' + ','.join(c.block_reasons) if c.blocked else ''}  {c.title[:70]}"
            )
        candidates_doc = {
            "mode": res.mode,
            "label": res.label,
            "n_requests": res.n_requests,
            "cost_usd": res.cost_usd,
            "wall_ms": res.wall_ms,
            "warnings": res.warnings,
            "dropped": res.dropped,
            "candidates": cand_rows,
        }
    candidates_doc["source"] = {
        "source_id": prepared.source_id,
        "title": prepared.title,
        "origin_url": prepared.origin_url,
        "rights": prepared.rights,
    }
    candidates_doc["llm"] = llm_label
    (a.out / "candidates.json").write_text(
        json.dumps(candidates_doc, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    if not chosen:
        log("Нет подходящих кандидатов — рендерить нечего")
        return 1

    # сценарии оператора
    op_scripts: list[Any] = [json.loads(p.read_text(encoding="utf-8")) for p in a.author_script]
    if op_scripts and len(op_scripts) != len(chosen):
        raise SystemExit(
            f"--author-script: {len(op_scripts)} сценария на {len(chosen)} клипов — "
            "нужен свой текст на каждый клип"
        )
    op_voice: dict[int, dict[str, str]] = {}
    for spec in a.voice_file:
        m = re.match(r"^(?:(\d+):)?([a-z_0-9]+)=(.+)$", spec)
        if not m:
            raise SystemExit(f"--voice-file: [N:]role=path (получено {spec!r})")
        op_voice.setdefault(int(m.group(1) or 1), {})[m.group(2)] = m.group(3)

    # история и библиотека
    history = _load_history(prepared) if a.history == "use" else []
    superseded = set(a.supersede)
    history = [h for h in history if h.get("id") not in superseded]
    run_prefix = a.out.name
    ids_this_run = {f"{run_prefix}/clip_{i:02d}" for i in range(1, len(chosen) + 1)}
    history = [h for h in history if h.get("id") not in ids_this_run]  # перезапуск той же папки
    if history:
        log(f"История источника: уже нарезано {len(history)} клипов — сравниваю с ними")
    library = _library_from_paths(a.library, log)
    library += [(h["id"], h["fingerprint"]) for h in history if h.get("fingerprint")]
    previous = [{"id": h["id"], "sids": h.get("sids") or []} for h in history]
    prev_texts: list[tuple[str, Any]] = [
        (h["id"], h.get("author_parts") or h.get("author_text") or "")
        for h in history
        if h.get("author_parts") or h.get("author_text")
    ]

    banner_set = _parse_index_set(a.banner_clips, len(chosen)) if a.banner else set()
    factory = _banner_factory(a, a.out, log) if a.banner else None

    cfg_job = ClipJobConfig(tts=a.tts, voice=a.voice, reframe_mode=a.reframe)
    produced: list[dict[str, Any]] = []
    all_history = _load_history(prepared)
    for k, c in enumerate(chosen, 1):
        name = f"clip_{k:02d}"
        clip_id = f"{run_prefix}/{name}"
        wd = a.out / "work" / name
        log(f"\n[{k}/{len(chosen)}] {name}: {c['start']:.1f}–{c['end']:.1f} с  {c.get('title') or ''}")
        log(f"  {_box_state()}")
        t_clip = time.monotonic()
        step_t: dict[str, float] = {}

        def prog(step: str, f: float, _st: dict[str, float] = step_t) -> None:
            if f == 0.0:
                _st[step] = time.monotonic()
                log(f"    {step}…")
            elif f >= 1.0 and step in _st:
                log(f"    {step} готово за {time.monotonic() - _st[step]:.1f} с")

        op_script = op_scripts[k - 1] if op_scripts else None
        r = produce_clip(
            source,
            transcript,
            start=c["start"],
            end=c["end"],
            start_sid=c.get("start_sid"),
            end_sid=c.get("end_sid"),
            title=c.get("title", ""),
            hook_text=c.get("hook_text", ""),
            keep_segments=c.get("keep"),
            workdir=wd,
            llm=author_llm,
            author_script=op_script,
            operator_edited=op_script is not None,
            operator_voice=op_voice.get(k) or None,
            niche=niche,
            previous_clips=previous,
            library=library,
            previous_author_texts=prev_texts,
            banner_plan=factory if k in banner_set else None,
            config=cfg_job,
            render=not a.no_render,
            progress=prog,
        )
        wall_clip = time.monotonic() - t_clip
        for step, dt in r.timings.items():
            log.timings[f"clip.{step}"] = log.timings.get(f"clip.{step}", 0.0) + dt
        log.timings[f"{name}.total"] = wall_clip

        log(f"  сценарий: {r.author.label}")
        for role, text in r.author.script.vo_parts():
            log(f"    {role}: {text}")
        if r.author.script.on_screen_title:
            log(f"    заголовок: {r.author.script.on_screen_title}")
        for w in r.warnings:
            log(f"  ! {w}")
        files: dict[str, str] = {}
        qc_line = None
        if r.final_path:
            dst = a.out / f"{name}.mp4"
            _link_or_copy(r.final_path, dst)
            files["video"] = dst.name
            qc = r.qc.to_dict() if r.qc else {}
            qc_line = "OK" if qc.get("passed") else f"FAIL {', '.join(qc.get('failed_checks') or [])}"
            log(
                f"  файл: {dst}  ({r.edl.duration:.1f} с, {dst.stat().st_size / 1e6:.1f} МБ, QC {qc_line}, "
                f"рефрейм {sorted({v.reframe for v in r.edl.video if v.reframe})})"
            )
        clip_ctx = {
            "file": files.get("video") or "(не рендерился)",
            "source": f"{prepared.title} ({prepared.origin_url or prepared.source_id})",
            "range": f"{c['start']:.1f}–{c['end']:.1f} с исходника, в ролике {r.edl.duration:.1f} с",
            "on_screen_title": r.author.script.on_screen_title,
            "author_label": r.author.label,
            "vo_parts": r.author.script.vo_parts(),
            "qc": qc_line,
        }
        rep = r.originality.to_dict()
        rep["clip"] = {k2: v for k2, v in clip_ctx.items() if k2 != "vo_parts"}
        rep["clip"]["id"] = clip_id
        (a.out / f"{name}.originality.json").write_text(
            json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        (a.out / f"{name}.originality.md").write_text(
            r.originality.to_markdown(heading=f"Отчёт об оригинальности — {name}", clip=clip_ctx),
            encoding="utf-8",
        )
        files["originality_json"] = f"{name}.originality.json"
        files["originality_md"] = f"{name}.originality.md"
        log("  " + r.originality.to_text().replace("\n", "\n  "))
        log(f"  {name} за {wall_clip:.1f} с")

        # история: этот клип становится «уже нарезанным» для следующих
        parts = [[role, text] for role, text in r.author.script.vo_parts()]
        author_text = " ".join(t for _, t in parts)
        entry = {
            "id": clip_id,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "start": round(c["start"], 3),
            "end": round(c["end"], 3),
            "sids": r.edl.clip.get("sids") or [],
            "author_text": author_text,
            "author_parts": parts,
            "author_mode": r.author.mode,
            "fingerprint": _fingerprint_json(r),
            "verdict": r.originality.verdict,
            "file": str((a.out / files["video"]).resolve()) if files.get("video") else None,
        }
        previous.append({"id": clip_id, "sids": entry["sids"]})
        prev_texts.append((clip_id, parts))
        if entry["fingerprint"] is not None:
            library.append((clip_id, entry["fingerprint"]))
        if not a.no_render:
            all_history = [h for h in all_history if h.get("id") != clip_id and h.get("id") not in superseded]
            all_history.append(entry)
            _save_history(prepared, all_history)
        produced.append(
            {
                "name": name,
                "id": clip_id,
                "files": files,
                "workdir": str(wd),
                "wall_sec": round(wall_clip, 2),
                "banner": k in banner_set,
                **{k2: v for k2, v in r.to_dict().items() if k2 not in ("edl", "workdir")},
            }
        )

    wall = time.monotonic() - t_start
    rss_self = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    rss_child = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024
    summary = {
        "source": {**source.info(), "title": prepared.title, "origin_url": prepared.origin_url},
        "source_dir": str(prepared.dir),
        "rights": prepared.rights,
        "rights_warnings": prepared.rights_warnings,
        "llm": llm_label,
        "selection_mode": candidates_doc.get("mode"),
        "clips": produced,
        "timings_sec": {k: round(v, 2) for k, v in log.timings.items()},
        "wall_sec": round(wall, 2),
        "peak_rss_mb": {"self": rss_self, "child_max": rss_child},
    }
    (a.out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    log("\nТайминги (с):")
    for k2, v in log.timings.items():
        log(f"  {k2:32s} {v:8.1f}")
    log(f"Итого {wall:.0f} с; пиковый RSS: процесс {rss_self} МБ, самый тяжёлый дочерний {rss_child} МБ")
    verdicts = ", ".join(f"{p['name']}: {p['originality']['verdict']}" for p in produced)
    log(f"Оригинальность: {verdicts}")
    log(f"Каталог: {a.out}")
    return 0


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

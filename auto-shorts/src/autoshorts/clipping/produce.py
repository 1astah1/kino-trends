"""Один утверждённый клип от кандидата до готового файла и отчёта (§6.3.6, стадии clip-job).

    src = ClipSource.from_prepared(PreparedSource.load("var/media/sources/src_ab12"))
    res = produce_clip(src, transcript, start=84.1, end=121.9, start_sid=57, end_sid=66,
                       title="…", workdir=Path("var/tmp/clip_1"), llm=LLMClient.from_env())
    res.final_path, res.originality.verdict, res.originality.suggestions

Порядок (как стадии clip-job, но в одном процессе — для `scripts/clip_local.py`, бенча и
будущих обработчиков стадий):

1. `author_script` — сценарий автора (LLM / оператор / шаблон с пометкой);
2. `author_voice` — озвучка реплик (edge-tts / запись оператора / mock);
3. раскладка EDL (`edl.layout_clip`);
4. `clip_analyze` — рефрейм на прокси в нужном отрезке (склейки, лица, стратегии), crop_NNN.cmd;
5. субтитры ASS (Source + Author + заголовок и тезисы);
6. звук: премикс с приглушением и двухпроходный loudnorm → mix.m4a;
7. `render` — одно кодирование (баннер через хук, если передан план);
8. `qc` — один проход ffmpeg + author_share + водяной знак;
9. отчёт об оригинальности (`originality.py`).

Перед тяжёлыми шагами — ожидание MemAvailable ≥ 1500 МБ (до 10 минут), ffmpeg — nice 10,
2 потока. Промежуточные файлы — в `workdir`.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from autoshorts.clipping.author.script import AuthorScript, AuthorScriptResult, generate_author_script
from autoshorts.clipping.author.voice import VoiceResult, voice_script
from autoshorts.clipping.captions import CaptionLayout, banner_zones, captions_from_edl
from autoshorts.clipping.edl import EDL, LayoutConfig, apply_reframe, layout_clip, source_range, words_json
from autoshorts.clipping.layout_override import (
    LayoutSpec,
    apply_caption_override,
    caption_zones_json,
    layout_conflicts,
)
from autoshorts.clipping.originality import (
    OriginalityReport,
    build_originality_report,
    library_similarity_for,
)
from autoshorts.clipping.qc_clip import QCCheck, QCReport, run_clip_qc
from autoshorts.clipping.render_clip import RenderConfig, RenderResult, banner_graph_from_plan, render_clip
from autoshorts.clipping.transcript import Transcript


@dataclass(slots=True)
class ClipSource:
    render_path: Path  # оригинал загрузки или 1080p-секция
    analysis_path: Path  # прокси 540p (или тот же файл)
    width: int
    height: int
    duration: float
    fps: float = 30.0
    has_audio: bool = True
    source_id: str | None = None
    title: str = ""
    rights: dict[str, Any] = field(default_factory=dict)
    rights_warnings: list[str] = field(default_factory=list)
    watermark: dict[str, Any] | None = None
    src_offset: float = 0.0  # момент источника, с которого начинается render_path
    media_range: tuple[float, float] | None = None
    # Звук исходника, если он лежит отдельно от видео (URL-источник: видео ≤540p без звука +
    # audio.m4a из source_prepare). None — звук берётся из render_path.
    audio_path: Path | None = None
    audio_offset: float = 0.0  # момент источника, с которого начинается audio_path

    @property
    def mix_audio_path(self) -> Path:
        return self.audio_path or self.render_path

    @property
    def mix_audio_offset(self) -> float:
        return self.audio_offset if self.audio_path is not None else self.src_offset

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        *,
        rights: Mapping[str, Any] | None = None,
        analysis_path: str | Path | None = None,
        source_id: str | None = None,
        title: str = "",
        watermark: Mapping[str, Any] | None = None,
        rights_warnings: Sequence[str] = (),
    ) -> ClipSource:
        from autoshorts.pipeline.proxy import probe_media

        pr = probe_media(path)
        w, h = pr.display_size
        return cls(
            render_path=Path(path),
            analysis_path=Path(analysis_path or path),
            width=int(w),
            height=int(h),
            duration=float(pr.duration),
            fps=float(pr.fps or 30.0),
            has_audio=bool(pr.has_audio),
            source_id=source_id,
            title=title,
            rights=dict(rights or {}),
            rights_warnings=list(rights_warnings),
            watermark=dict(watermark) if watermark else None,
        )

    @classmethod
    def from_prepared(cls, prepared: Any) -> ClipSource:
        """Из `source_ingest.PreparedSource` (manifest source.json)."""
        render = prepared.path("original") or prepared.path("original_video")
        proxy = prepared.path("proxy") or render
        if render is None:
            raise FileNotFoundError(
                "prepared source has no original video (URL sources need clip_fetch sections)"
            )
        probe = prepared.probe or {}
        w = int(probe.get("width") or 0)
        h = int(probe.get("height") or 0)
        if int(probe.get("rotation") or 0) in (90, 270, -90):
            w, h = h, w
        has_audio = bool(probe.get("has_audio", True))
        audio: Path | None = None
        if prepared.path("original") is None:
            # URL-источник: original_video — только видео, звук скачан отдельно; audio.m4a из
            # source_prepare выровнен с прокси и транскриптом (время источника с нуля).
            for key in ("audio", "original_audio"):
                cand = prepared.path(key)
                if cand is not None and cand.is_file():
                    audio = cand
                    break
            has_audio = audio is not None or has_audio
        return cls(
            render_path=render,
            analysis_path=proxy,
            width=w,
            height=h,
            duration=float(probe.get("duration") or 0.0),
            fps=float(probe.get("fps") or 30.0),
            has_audio=has_audio,
            source_id=prepared.source_id,
            title=prepared.title,
            rights=dict(prepared.rights),
            rights_warnings=list(prepared.rights_warnings),
            watermark=prepared.watermark,
            audio_path=audio,
        )

    def info(self) -> dict[str, Any]:
        return {
            "path": str(self.render_path),
            "width": self.width,
            "height": self.height,
            "duration": self.duration,
            "fps": self.fps,
            "has_audio": self.has_audio,
            "source_id": self.source_id,
            "audio_path": str(self.audio_path) if self.audio_path else None,
        }


@dataclass(slots=True)
class ClipJobConfig:
    layout: LayoutConfig = field(default_factory=LayoutConfig)
    render: RenderConfig = field(default_factory=RenderConfig)
    tts: str = "edge"  # edge | mock
    voice: str | None = None
    tts_fallback_to_mock: bool = False
    reframe_mode: str = "auto"  # auto | track | center | fit_blur
    detect_faces: bool = True
    detect_scenes: bool = True
    balance_audio: bool = True
    run_qc: bool = True
    min_share: float = 0.30  # settings.clip_author_share_min
    gate_for_own: bool = False  # settings.clip_author_share_gate_for_own
    min_mem_mb: int = 1500
    mem_wait_sec: float = 600.0
    duration_range: tuple[float, float] = (20.0, 58.0)
    captions_mode: str = "both"  # author_layer.captions: both | source | author | none
    # стиль канала «кадр в фигуре на живом фоне» (clipping/frame_style.py); None — прежний вид
    frame_style: Mapping[str, Any] | None = None
    # набор стилей текста: None — прежний; "edit" — кино-эдит: крупные слова по центру, заголовок без плашки
    caption_preset: str | None = None


def edit_caption_styles() -> dict[str, Any]:
    """Кино-эдит (разбор растущих кино-каналов 01.11.2026): 1–3 крупных слова по центру, текущее слово жёлтым,
    толстая обводка; заголовок-история — крупно, без плашки (на первые 3 с)."""
    from autoshorts.clipping.captions import CaptionStyle

    return {
        "source_style": CaptionStyle("Source", 96, "#FFFFFF", "#FFD400", outline=8, outline_color="#000000",
                                     border_style=1, shadow=4),
        "author_style": CaptionStyle("Author", 88, "#FFFFFF", "#FFD400", outline=8, outline_color="#000000",
                                     border_style=1, shadow=4),
        "title_style": CaptionStyle("Title", 74, "#FFFFFF", "#FFFFFF", outline=8, outline_color="#000000",
                                    border_style=1, shadow=4),
        "onscreen_style": CaptionStyle("OnScreen", 60, "#FFD400", "#FFD400", outline=6, outline_color="#000000",
                                       border_style=1, shadow=3),
    }


@dataclass(slots=True)
class ClipResult:
    workdir: Path
    final_path: Path | None
    edl: EDL
    author: AuthorScriptResult
    voice: VoiceResult
    reframe: dict[str, Any]
    mix: dict[str, Any]
    render: RenderResult | None
    qc: QCReport | None
    originality: OriginalityReport
    timings: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def author_share(self) -> float:
        return float(self.originality.summary.get("author_share", 0.0))

    @property
    def cost_usd(self) -> float:
        return float(self.author.cost_usd) + sum(t.cost_usd for t in self.voice.takes.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "workdir": str(self.workdir),
            "final_path": str(self.final_path) if self.final_path else None,
            "duration_sec": round(self.edl.duration, 3),
            "author_share": round(self.author_share, 3),
            "cost_usd": round(self.cost_usd, 6),
            "author": self.author.to_dict(),
            "voice": self.voice.to_dict(),
            "reframe": self.reframe,
            "mix": self.mix,
            "render": self.render.to_dict() if self.render else None,
            "qc": self.qc.to_dict() if self.qc else None,
            "originality": self.originality.to_dict(),
            "timings": {k: round(v, 3) for k, v in self.timings.items()},
            "warnings": list(self.warnings),
            "edl": "edl.json",
        }

    def save(self, path: str | Path | None = None) -> Path:
        p = Path(path) if path else self.workdir / "clip.json"
        tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
        tmp.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8"
        )
        os.replace(tmp, p)
        return p


def mix_spec_from_edl(edl: EDL, source: ClipSource) -> Any:
    from autoshorts.pipeline.audio_mix import GapPiece, MixSpec, SrcPiece, VoClip

    pieces: list[Any] = []
    offset = source.mix_audio_offset
    for v in edl.video:
        if v.type == "src":
            pieces.append(SrcPiece(max(0.0, float(v.in_ or 0.0) - offset), v.dur))
        else:
            pieces.append(GapPiece(v.dur))
    return MixSpec(
        source=source.mix_audio_path,
        pieces=pieces,
        duration=edl.duration,
        duck=[(d.start, d.end, d.gain_db) for d in edl.src_duck],
        vo=[VoClip(Path(v.file), v.at) for v in edl.vo],
        source_has_audio=source.has_audio,
        src_gain_db=edl.src_gain_db,
    )


def fit_panel_bottom_for_banner(banner: Mapping[str, Any] | None, canvas_h: int, gap: int = 16) -> int | None:
    """Низ кадра fit_blur над нижней плашкой баннера (верх самой верхней из нижних зон − gap).

    Кадр 16:9 по центру (y 656–1264 на 1080×1920) иначе уходит под оффер y 1120–1370.
    """
    rects = (banner or {}).get("rects") or {}
    tops = [
        int(r.get("y1", 0))
        for r in rects.values()
        if isinstance(r, Mapping) and int(r.get("y1", 0)) > canvas_h / 2
    ]
    return (min(tops) - gap) if tops else None


def _safe_rect_of_plan(banner_plan: Any) -> Any:
    """Safe zone профиля из плана баннера (`placement.profile_id`); без плана — common_organic."""
    placement = getattr(banner_plan, "placement", None)
    profile_id = getattr(placement, "profile_id", None)
    if not isinstance(profile_id, str):
        return None
    try:
        from autoshorts.pipeline.banner_place import load_safe_zone_catalog

        return load_safe_zone_catalog().get(profile_id).safe_rect
    except Exception:  # noqa: BLE001 - профиль пропал из каталога: границы по умолчанию
        return None


def _layout_conflicts_of(
    banner: Any, rects: Sequence[Any], layout: CaptionLayout, *, empty_zones: Sequence[str] = ()
) -> list[dict[str, Any]]:
    """Пересечения текстовых зон с прямоугольниками баннера (имена слоёв — из `edl.banner.rects`)."""
    from autoshorts.domain.banner_schema import Rect

    named: dict[str, Rect] = {}
    groups: dict[str, str] = {}
    raw = (banner or {}).get("rects") if isinstance(banner, Mapping) else None
    if isinstance(raw, Mapping) and raw:
        for key, r in raw.items():
            try:
                named[str(key)] = Rect.from_edges(r["x1"], r["y1"], r["x2"], r["y2"])
                groups[str(key)] = "banner"
            except (KeyError, TypeError, ValueError):
                continue
    else:
        for i, z in enumerate(rects):
            named[f"banner_{i}"] = Rect.from_edges(z.x1, z.y1, z.x2, z.y2)
            groups[f"banner_{i}"] = "banner"
    for name, zone in (
        ("captions", layout.source_zone),
        ("title", layout.title_zone),
        ("onscreen", layout.onscreen_zone),
    ):
        named[name] = Rect.from_edges(zone.x1, zone.y1, zone.x2, zone.y2)
    return layout_conflicts(named, groups=groups, skip=empty_zones)


def _wait_mem(cfg: ClipJobConfig) -> None:
    from autoshorts.clipping.source_ingest import wait_for_memory

    wait_for_memory(cfg.min_mem_mb, timeout_sec=cfg.mem_wait_sec)


def originality_qc_checks(
    report: OriginalityReport, share: Any, author: AuthorScriptResult, *, min_share: float
) -> list[QCCheck]:
    """Проверки QC из отчёта об оригинальности (гейт author_share, запреты, водяной знак, похожесть).

    Те же, что `produce_clip` добавляет к одному проходу ffmpeg; стадия qc clip-job зовёт их сама.
    """
    gate_chk = report.check("author_share")
    vb = report.check("voice_balance")
    gate_fails = [c for c in (gate_chk, vb) if c is not None and c.status == "fail"]
    extra = [
        QCCheck(
            "author_share",
            not gate_fails,
            f"{share.value:.2f} (голос {share.voice_share:.2f})",
            f"≥ {min_share:.2f} при чужих правах, голос и стоп-кадры ≥ 0.15",
            "; ".join(c.detail for c in gate_fails) or (gate_chk.detail if gate_chk else ""),
        ),
    ]
    if author.blocked:
        errs = [i for i in author.issues if i.severity == "error"]
        extra.append(
            QCCheck(
                "forbidden_phrase",
                False,
                ",".join(sorted({i.code for i in errs})),
                "нет запретов ниши, гарантий и ссылок в авторском тексте",
                "; ".join(i.detail for i in errs),
            )
        )
    wm = report.check("foreign_watermark")
    if wm is not None and wm.status in ("fail", "warn"):
        extra.append(
            QCCheck(
                "foreign_watermark",
                wm.status != "fail",
                str(wm.value),
                "нет чужих логотипов",
                wm.detail,
                "fail" if wm.status == "fail" else "warn",
            )
        )
    ls = report.check("library_similarity")
    if ls is not None and ls.status == "warn":
        extra.append(
            QCCheck("library_similarity", False, str(ls.value), str(ls.threshold), ls.detail, "warn")
        )
    return extra


def produce_clip(
    source: ClipSource,
    transcript: Transcript,
    *,
    start: float,
    end: float,
    workdir: str | Path,
    start_sid: int | None = None,
    end_sid: int | None = None,
    title: str = "",
    hook_text: str = "",
    keep_segments: Sequence[Sequence[float]] | None = None,
    llm: Any = None,
    author_script: AuthorScript | Mapping[str, Any] | None = None,
    operator_edited: bool = False,
    operator_voice: Mapping[str, str | Path] | None = None,
    asr: Any = None,
    niche: Any = None,
    previous_clips: Sequence[Mapping[str, Any]] = (),
    library: Sequence[tuple[Any, Any]] = (),
    previous_author_texts: Sequence[tuple[Any, Any]] = (),  # (id, текст | [(role, text), …])
    banner_plan: Any = None,
    caption_layout: CaptionLayout | None = None,
    layout_override: Any = None,
    config: ClipJobConfig | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    progress: Callable[[str, float], None] | None = None,
    cancel: threading.Event | None = None,
    render: bool = True,
    author: AuthorScriptResult | None = None,
    voice: VoiceResult | None = None,
    reframe_plan: Any = None,
) -> ClipResult:
    """Полный цикл одного клипа. `render=False` — всё до рендера (EDL, звук не сводится).

    `author`, `voice`, `reframe_plan` — готовые итоги стадий clip-job (author_script, author_voice,
    clip_analyze): если переданы, шаг не повторяется (стадия render очереди).

    `banner_plan` — готовый `banner_overlay.BannerPlan` или фабрика
    `(duration_sec=, fps=, out_size=, workdir=) -> BannerPlan`: длительность ролика известна только
    после раскладки EDL. Ошибка фабрики не роняет клип (рендер без баннера + предупреждение).
    С баннером кадр fit_blur поднимается над плашкой (`edl.meta["fit_panel_bottom"]`), субтитры
    встают в `captions_zone` плана, а свой заголовок и тезисы уводятся из-под прямоугольников
    баннера (`CaptionLayout.avoiding`; сдвиги — в `edl.meta["caption_zones_moved"]`). Если места
    нет, в предупреждениях будет `captions_overlap_banner:…`.
    `layout_override` — ручная раскладка оператора (`layout_override.LayoutSpec` или JSON
    `author_layer.layout`): низ субтитров, верх заголовка и тезисов ставятся до `avoiding`; итог —
    в `edl.meta["caption_zones"]`, `edl.meta["layout_manual"]`, пересечения — `edl.meta["layout_conflicts"]`.
    Сдвиг самого баннера применяет фабрика плана (`services/clipping.banner_setup`).
    `previous_author_texts` — [(id, текст | [(role, text), …])] прошлых роликов канала.
    """
    cfg = config or ClipJobConfig()
    wd = Path(workdir).resolve()
    wd.mkdir(parents=True, exist_ok=True)
    timings: dict[str, float] = {}
    warnings: list[str] = []

    def step(name: str) -> Callable[[], None]:
        t0 = time.monotonic()
        if progress:
            progress(name, 0.0)

        def done() -> None:
            timings[name] = time.monotonic() - t0
            if progress:
                progress(name, 1.0)

        return done

    sents = [s for s in transcript.sentences if s.e > start + 0.05 and s.s < end - 0.05]
    if start_sid is None and sents:
        start_sid = sents[0].sid
    if end_sid is None and sents:
        end_sid = sents[-1].sid
    if start_sid is None or end_sid is None:
        raise ValueError("clip range has no sentences")
    rights = source.rights or {}

    # 1. сценарий автора
    done = step("author_script")
    if author is None:
        author = generate_author_script(
            transcript,
            start_sid,
            end_sid,
            llm=llm,
            operator_script=author_script,
            title=title,
            hook_text=hook_text,
            niche=niche,
            owner=str(rights.get("owner_name") or rights.get("owner") or ""),
            rights_type=rights.get("rights_type"),
            on_event=on_event,
        )
    warnings += author.warnings
    if author.blocked:
        # отчёт об оригинальности поставит fail (author_text), QC — forbidden_phrase
        warnings.append(
            "author_script_blocked:"
            + ",".join(sorted({i.code for i in author.issues if i.severity == "error"}))
        )
    done()

    # 2. голос
    done = step("author_voice")
    if voice is None:
        voice = voice_script(
            author.script.vo_parts(),
            wd / "voice",
            tts=cfg.tts,
            voice=cfg.voice,  # type: ignore[arg-type]
            operator_files=operator_voice,
            asr=asr,
            fallback_to_mock=cfg.tts_fallback_to_mock,
        )
    warnings += voice.warnings
    done()

    # 3. раскладка
    done = step("layout")
    edl = layout_clip(
        transcript,
        start=start,
        end=end,
        start_sid=start_sid,
        end_sid=end_sid,
        script=author.script,
        takes=voice.takes,
        source=source.info(),
        keep_segments=keep_segments,
        media_range=source.media_range or (source.src_offset, source.src_offset + source.duration),
        config=cfg.layout,
        meta={
            "author_mode": author.mode,
            "attribution": rights.get("attribution_text"),
            "source_title": source.title,
            "rights_type": rights.get("rights_type"),
        },
    )
    warnings += edl.warnings
    done()

    # 4. рефрейм
    done = step("clip_analyze")
    from autoshorts.clipping.reframe import plan_reframe

    plan = reframe_plan
    if plan is None:
        a, b = source_range(edl)
        if cfg.detect_faces or cfg.detect_scenes:
            _wait_mem(cfg)
        plan = plan_reframe(
            source.analysis_path,
            a,
            b,
            src_w=source.width,
            src_h=source.height,
            fps=edl.fps,
            mode=cfg.reframe_mode,
            detect_scenes=cfg.detect_scenes,
            detect_faces=cfg.detect_faces,
        )
    apply_reframe(edl, plan, workdir=wd)
    warnings += plan.warnings
    from autoshorts.clipping import frame_style as fstyle

    fs = fstyle.FrameStyle.coerce(cfg.frame_style) if cfg.frame_style else None
    if fs is not None and not (fs.bg_path and Path(fs.bg_path).is_file()):
        warnings.append("frame_style_without_background: фон канала не найден, ролик в прежнем виде")
        fs = None
    if fs is not None:
        fs = fstyle.choose_shape(fs, getattr(plan, "shots", None))  # auto: круг или прямоугольник по лицам
        # чёрные полосы широкоэкранного кино (letterbox/pillarbox) в рамку не берём: кроп внутри картинки
        box = None
        try:
            ra, rb = source_range(edl)
            box = fstyle.detect_content_box(
                source.render_path, ra - source.src_offset, rb - source.src_offset, int(source.width), int(source.height)
            )
        except Exception as exc:  # noqa: BLE001 - без полос ролик всё равно соберётся
            warnings.append(f"content_box_failed: {type(exc).__name__}: {exc}"[:200])
        if box is not None:
            warnings.append(f"letterbox_cropped: картинка {box['w']}×{box['h']} в кадре {source.width}×{source.height}")
        fstyle.apply_to_edl(edl, fs, wd, int(source.width), int(source.height), box=box)
    reframe_info = plan.to_dict()
    done()

    # 5. субтитры
    done = step("captions")
    if banner_plan is not None and callable(banner_plan) and not hasattr(banner_plan, "items"):
        # Фабрика плана баннера: длительность ролика известна только после раскладки EDL.
        try:
            banner_plan = banner_plan(
                duration_sec=edl.duration,
                fps=edl.fps,
                out_size=(int(edl.canvas.get("w", 1080)), int(edl.canvas.get("h", 1920))),
                workdir=wd,
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"banner_plan_failed_rendered_without_banner: {type(exc).__name__}: {exc}"[:300])
            banner_plan = None
    if banner_plan is not None:
        to_json = getattr(banner_plan, "to_json", None)
        if callable(to_json):
            try:
                edl.banner = to_json()
            except Exception:  # noqa: BLE001
                pass
        bottom = fit_panel_bottom_for_banner(edl.banner, int(edl.canvas.get("h", 1920)))
        if bottom is not None and any(v.reframe == "fit_blur" for v in edl.video):
            edl.meta["fit_panel_bottom"] = bottom
    layout = caption_layout
    if fs is not None and layout is None:
        layout = fstyle.caption_layout(fs)  # субтитры — внутри фигуры, заголовок и тезисы — над ней
    if layout is None and banner_plan is not None and getattr(banner_plan, "captions_zone", None) is not None:
        r = banner_plan.captions_zone.rect
        layout = CaptionLayout.from_rect(r.x, r.y, r.x2, r.y2)
    layout = layout or CaptionLayout()
    spec = LayoutSpec.coerce(layout_override)
    layout_manual = spec is not None and spec.is_manual
    if layout_manual and spec is not None:
        # Ручная раскладка оператора: позиции зон — предпочтение, дальше те же проверки, что в авто.
        layout, applied = apply_caption_override(layout, spec, safe=_safe_rect_of_plan(banner_plan))
        if applied:
            edl.meta["layout_applied"] = applied
    rects: list[Any] = []
    if banner_plan is not None:
        # Баннер рисуется поверх ASS: заголовок и тезисы уводим из-под его реальных прямоугольников
        # (верхний слот y 230–480 иначе закрывает свой заголовок, засчитанный в author_share).
        canvas = (int(edl.canvas.get("w", 1080)), int(edl.canvas.get("h", 1920)))
        rects = banner_zones(edl.banner if edl.banner is not None else banner_plan, canvas=canvas)
        layout, moved = layout.avoiding(rects, block_captions=cfg.captions_mode != "none")
        if moved:
            edl.meta["caption_zones_moved"] = moved
        bad = layout.check_clear(rects)
        if bad:
            warnings.append(f"captions_overlap_banner:{','.join(bad)}")
    else:
        bad = layout.check_banner_clear()
        if bad:
            warnings.append(f"captions_overlap_banner_area:{','.join(bad)}")
    edl.meta["layout_manual"] = layout_manual
    if spec is not None:
        edl.meta["layout_override"] = spec.to_json()
    edl.meta["caption_zones"] = caption_zones_json(layout)
    kinds = {t.kind for t in edl.texts if t.text.strip()}
    empty_zones = [z for z, k in (("title", "title"), ("onscreen", "on_screen")) if k not in kinds]
    conflicts = _layout_conflicts_of(edl.banner, rects, layout, empty_zones=empty_zones)
    if conflicts:
        edl.meta["layout_conflicts"] = conflicts
    (wd / "src_words.json").write_text(
        json.dumps(words_json(edl.source_words(transcript.words)), ensure_ascii=False), encoding="utf-8"
    )
    (wd / "vo_words.json").write_text(
        json.dumps(words_json(edl.author_words()), ensure_ascii=False), encoding="utf-8"
    )
    captions_from_edl(
        edl, transcript.words, wd / "captions.ass", layout=layout, mode=cfg.captions_mode,
        styles=fstyle.caption_styles(fs) if fs is not None else (edit_caption_styles() if cfg.caption_preset == "edit" else None),
    )
    done()

    edl.save(wd / "edl.json")
    mix_info: dict[str, Any] = {}
    render_res: RenderResult | None = None
    qc: QCReport | None = None
    final: Path | None = None
    if render:
        # 6. звук
        done = step("audio_mix")
        from autoshorts.pipeline.audio_mix import mix_audio

        mix = mix_audio(
            mix_spec_from_edl(edl, source),
            wd / "mix.m4a",
            workdir=wd / "mix",
            balance=cfg.balance_audio,
            cancel=cancel,
        )
        mix_info = mix.to_dict()
        done()

        # 7. рендер
        done = step("render")
        bg = None
        if banner_plan is not None:
            bg = banner_graph_from_plan(banner_plan)
            if bg is None:
                warnings.append("banner_module_unavailable_rendered_without_banner")
        rcfg = cfg.render
        rcfg.min_mem_mb = cfg.min_mem_mb
        rcfg.mem_wait_sec = cfg.mem_wait_sec
        render_res = render_clip(
            edl,
            src_path=source.render_path,
            audio_path=mix.path,
            out_path=wd / "final.mp4",
            workdir=wd,
            ass_path="captions.ass",
            cfg=rcfg,
            banner_graph=bg,
            cancel=cancel,
            src_offset=source.src_offset,
        )
        final = render_res.path
        warnings += render_res.warnings
        done()

    # 9 (раньше QC, чтобы author_share попал в QC). author_share и похожесть
    done = step("originality")
    share = edl.author_share()
    lib_sim, lib_note = None, None
    if final is not None and library:
        lib_sim, lib_note, fp = library_similarity_for(final, library)
        if fp is not None:
            edl.meta["fingerprint"] = fp.to_json()
    elif not library:
        lib_note = "библиотека своих роликов не передана"
    author_texts = [t for _, t in author.script.vo_parts()]
    report = build_originality_report(
        share=share,
        rights=rights,
        rights_warnings=source.rights_warnings,
        watermark=source.watermark,
        edl=edl,
        transcript=transcript,
        previous_clips=previous_clips,
        library_similarity=lib_sim,
        library_note=lib_note,
        author_mode=author.mode,
        operator_edited=operator_edited,
        script_issues=[i.to_dict() for i in author.issues],
        author_texts=author_texts,
        author_parts=author.script.vo_parts(),
        previous_author_texts=previous_author_texts,
        voice_kinds=voice.kinds,
        min_share=cfg.min_share,
        gate_for_own=cfg.gate_for_own,
    )
    done()

    # 8. QC
    if final is not None and cfg.run_qc:
        done = step("qc")
        _wait_mem(cfg)
        extra = originality_qc_checks(report, share, author, min_share=cfg.min_share)
        qc = run_clip_qc(
            final,
            expected_static=edl.expected_static,
            duration_range=cfg.duration_range,
            extra_checks=extra,
            workdir=wd,
            cancel=cancel,
        )
        done()

    edl.meta["author_share"] = round(share.value, 3)
    edl.save(wd / "edl.json")
    res = ClipResult(
        wd, final, edl, author, voice, reframe_info, mix_info, render_res, qc, report, timings, warnings
    )
    res.save()
    (wd / "originality.json").write_text(
        json.dumps(report.to_dict(), ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    return res


__all__ = [
    "ClipJobConfig",
    "ClipResult",
    "ClipSource",
    "fit_panel_bottom_for_banner",
    "mix_spec_from_edl",
    "originality_qc_checks",
    "produce_clip",
]

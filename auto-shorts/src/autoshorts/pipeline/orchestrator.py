"""
Оркестратор стадий: queued -> script_ready -> voiced -> rendered -> qc_passed/qc_failed -> pending_review.
Каждая стадия — отдельная функция, читает job.stage_payload, пишет результат туда же (ТЗ §9:
идемпотентность через детерминированные ключи, здесь — упрощённо через JSONB одной строки задачи).

Публикация — РУЧНАЯ (решение владельца). Конвейер доводит job до ready_to_publish и отдаёт
человеку "пакет к заливке" через Telegram-бота; API-загрузки нет.
"""
from __future__ import annotations

import time
import uuid
from hashlib import blake2b
from pathlib import Path

from autoshorts import db
from autoshorts.config import SETTINGS, load_niche, load_banner_config
from autoshorts.models import JobStatus
from autoshorts.pipeline import qc as qc_module
from autoshorts.pipeline.render import (
    build_scene_clip, concat_scenes, two_pass_loudnorm, render_final_video,
    render_preview, caption_margin_v_above_banner,
)
from autoshorts.pipeline.subtitles import build_karaoke_ass
from autoshorts.pipeline.voice import mock_synthesize_voiceover, synthesize_voiceover
from autoshorts.providers.llm import generate_scenario
from autoshorts.pipeline.trend_mimicry import TrendBrief, build_scenario_from_brief
from autoshorts.pipeline.audio_strategy import resolve_audio_strategy
from autoshorts.pipeline.beat_sync import analyze_beats, align_scene_cuts_to_beats
from autoshorts.pipeline.hashtags import generate_hashtags, format_hashtag_line
from autoshorts.providers.images import generate_scene_image


def scene_image_seed(video_uid: str, index: int) -> int:
    """Seed картинки сцены, стабильный между процессами (ARCHITECTURE §3.2, §3.3 п.4).

    Встроенный hash() от строки солится PYTHONHASHSEED и меняется при каждом запуске процесса,
    поэтому ретрай стадии рисовал другие картинки. blake2b от "video_uid:index" детерминирован.
    """
    return int(blake2b(f"{video_uid}:{index}".encode("utf-8")).hexdigest()[:8], 16)


def _job_dir(video_uid: str) -> Path:
    d = SETTINGS.render_tmp_dir / video_uid
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_stage_script(job: dict, topic: str, offline: bool = False,
                     trend_brief: TrendBrief | None = None) -> None:
    """
    trend_brief задан -> сценарий строится под МЕХАНИКУ залетевшего чужого ролика
    (хук, темп монтажа, длительность), а не под дефолт ниши. См. pipeline/trend_mimicry.py:
    это и есть "видео как трендовые" без копирования самого контента (ТЗ §8).
    """
    niche = load_niche(job["niche"])

    if trend_brief is not None:
        scenario = build_scenario_from_brief(niche, topic, trend_brief, generate_voiceover_fn=None)
        latency_ms = 0
        provider_label = "trend_mimicry"
    else:
        scenario, latency_ms = generate_scenario(niche, topic)
        provider_label = "mock" if offline else SETTINGS.llm_provider

    db.record_generation_event(
        job_id=job["id"], step_name="script", provider=provider_label,
        model=None, units=None, unit_price_usd=None, cost_usd=0.0, latency_ms=latency_ms,
    )
    stage_payload = {
        "topic": topic,
        "voiceover_text": scenario.voiceover_text,
        "scene_count": len(scenario.scenes),
    }
    if trend_brief is not None:
        stage_payload["source_trend_video_id"] = trend_brief.external_video_id
        stage_payload["source_trend_outlier_score"] = trend_brief.outlier_score

    db.update_job_status(job["id"], JobStatus.SCRIPT_READY, stage_payload_patch=stage_payload)


def run_stage_voice(job: dict, offline: bool = True) -> None:
    """offline=True — mock-заглушка без сети (для локальной разработки/тестов).
    В проде offline=False использует edge-tts (см. ТЗ: снимает Whisper полностью)."""
    niche = load_niche(job["niche"])
    voiceover_text = job["stage_payload"]["voiceover_text"]
    job_dir = _job_dir(job["video_uid"])

    t0 = time.monotonic()
    if offline:
        _, words, _ = mock_synthesize_voiceover(voiceover_text, job_dir, job["video_uid"])
    else:
        _, words, _ = synthesize_voiceover(voiceover_text, job_dir, job["video_uid"])
    latency_ms = int((time.monotonic() - t0) * 1000)

    db.record_generation_event(
        job_id=job["id"], step_name="tts", provider="edge_tts_wordboundary", model=None,
        units=len(voiceover_text), unit_price_usd=0.0, cost_usd=0.0, latency_ms=latency_ms,
    )
    words_payload = [{"word": w.word, "start_sec": w.start_sec, "end_sec": w.end_sec} for w in words]
    db.update_job_status(job["id"], JobStatus.VOICED, stage_payload_patch={"words": words_payload})


def run_stage_render(job: dict, platform: str = "youtube", test_scene_dir: Path | None = None) -> None:
    """
    Собирает финальный mp4: сцены с crop-based Ken Burns + двухслойный баннер + караоке-субтитры.
    test_scene_dir — для прогона без реальной генерации изображений (см. tests/).
    """
    from autoshorts.models import Scene, WordTiming

    niche = load_niche(job["niche"])
    banner_cfg = load_banner_config(platform)
    job_dir = _job_dir(job["video_uid"])
    video_uid = job["video_uid"]

    scene_count = job["stage_payload"]["scene_count"]
    target_dur = sum(niche.duration_range) / 2

    audio_strategy = resolve_audio_strategy(platform, channel_subscriber_count=job.get("channel_subscribers", 0))

    # Хэштеги — генерируются здесь, попадают в пакет к ручной заливке (ТЗ §6)
    topic = job["stage_payload"].get("topic", "")
    hashtags = generate_hashtags(niche, topic)
    hashtag_line = format_hashtag_line(hashtags)

    per_scene = round(target_dur / scene_count, 2)  # дефолт — переопределяется ниже при beat_sync

    if test_scene_dir is not None:
        # Локальный прогон без генерации (тесты, обкатка шаблона) — заготовленные кадры.
        source_images = sorted(test_scene_dir.glob("scene_*.png"))
        if not source_images:
            raise RuntimeError(f"Нет исходных изображений в {test_scene_dir}")
        image_gen_cost = 0.0
    else:
        # Реальная генерация: flux-schnell через fal (или mock, если FAL_API_KEY пуст — см.
        # providers/images.py). Бюджетный предохранитель ПЕРЕД каждым платным вызовом (ТЗ §9).
        images_dir = job_dir / "scenes_src"
        source_images = []
        image_gen_cost = 0.0
        style = niche.visuals.get("style", "")
        for i in range(scene_count):
            est_cost = 0.003 if SETTINGS.fal_api_key else 0.0  # flux-schnell лист-цена
            if not db.check_and_reserve_budget(est_cost):
                raise RuntimeError(
                    "Дневной бюджет исчерпан (budget_state.paused) — платная генерация "
                    "приостановлена, см. ТЗ §9. Рендер продолжится, когда бюджет обнулится."
                )
            prompt = f"{style}, scene {i+1}, {job['stage_payload'].get('topic', '')}"
            img_path, latency_ms, cost = generate_scene_image(
                prompt, images_dir / f"scene_{i:02d}.png", seed=scene_image_seed(video_uid, i),
            )
            source_images.append(img_path)
            image_gen_cost += cost
            db.record_generation_event(
                job_id=job["id"], step_name="image", provider="fal:flux-schnell" if SETTINGS.fal_api_key else "mock",
                model=None, units=1, unit_price_usd=cost, cost_usd=cost, latency_ms=int(latency_ms * 1000),
            )

    voice_audio_candidates = list(job_dir.glob(f"{video_uid}_voice.*"))
    if not voice_audio_candidates:
        raise RuntimeError("Аудио озвучки не найдено — стадия voice не выполнена")
    raw_audio = voice_audio_candidates[0]
    # mock-заглушка voice.py пишет .txt — для полного прогона без сети синтезируем тон нужной длины
    if raw_audio.suffix == ".txt":
        import subprocess
        wav_path = job_dir / f"{video_uid}_mock_audio.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={target_dur}", str(wav_path)],
            check=True, capture_output=True,
        )
        raw_audio = wav_path

    normed_audio = two_pass_loudnorm(raw_audio, job_dir / f"{video_uid}_normed.m4a")

    # Beat-sync — только когда audio_strategy это включает (по умолчанию VK-ветка, см. ТЗ:
    # YouTube <50k подписчиков живёт на original sound bonus и своей озвучке, там beat-sync
    # применять некому — трендовый трек туда не вшивается на этапе рендера вообще).
    scene_durations = [per_scene] * scene_count
    if audio_strategy.beat_sync_enabled:
        try:
            beat_map = analyze_beats(normed_audio)
            scene_durations = align_scene_cuts_to_beats(scene_count, beat_map)
        except Exception:
            pass  # деградация на равномерное деление — беззвучный fallback уже встроен в align_scene_cuts_to_beats

    scenes = [
        Scene(index=i, image_path=str(source_images[i % len(source_images)]),
              duration_sec=scene_durations[i] if i < len(scene_durations) else per_scene,
              pan_direction=["in", "out", "left", "right"][i % 4])
        for i in range(scene_count)
    ]
    clips = [build_scene_clip(s, job_dir, 1080, 1920) for s in scenes]
    scenes_video = concat_scenes(clips, job_dir, video_uid)

    words_raw = job["stage_payload"]["words"]
    words = [WordTiming(**w) for w in words_raw]
    margin_v = caption_margin_v_above_banner(1920, banner_cfg)
    ass_path = build_karaoke_ass(
        words, job_dir / f"{video_uid}.ass",
        highlight_color=niche.captions["highlight_color"],
        base_color=niche.captions["base_color"],
        margin_v=margin_v,
    )

    final_path = job_dir / f"{video_uid}_final.mp4"
    result = render_final_video(
        scenes_video=scenes_video, audio_path=normed_audio, ass_subtitle_path=ass_path,
        banner_cfg=banner_cfg, offer_text="umka.chat", out_path=final_path,
        fonts_dir=SETTINGS.fonts_dir,
    )

    preview_path = job_dir / f"{video_uid}_preview.mp4"
    render_preview(preview_path, final_path)

    db.record_generation_event(
        job_id=job["id"], step_name="render", provider="ffmpeg_local", model=None,
        units=result.duration_sec, unit_price_usd=0.0, cost_usd=0.0,
    )
    db.update_job_status(
        job["id"], JobStatus.RENDERED,
        stage_payload_patch={
            "final_path": str(final_path), "preview_path": str(preview_path),
            "duration_sec": result.duration_sec,
            "hashtags": hashtags,
            "beat_sync_used": audio_strategy.beat_sync_enabled,
            "audio_strategy_note": audio_strategy.trending_sound_note,
        },
    )


def run_stage_qc(job: dict) -> None:
    niche = load_niche(job["niche"])
    final_path = Path(job["stage_payload"]["final_path"])
    qc_result = qc_module.run_preflight_qc(final_path, target_duration_range=niche.duration_range)

    status = JobStatus.QC_PASSED if qc_result.passed else JobStatus.QC_FAILED
    reject_reason = None if qc_result.passed else "_".join(qc_result.failed_checks)[:64]

    db.update_job_status(
        job["id"], status,
        stage_payload_patch={"qc_checks": qc_result.checks, "qc_details": qc_result.details},
        reject_reason=reject_reason,
    )

    if qc_result.passed:
        db.update_job_status(job["id"], JobStatus.PENDING_REVIEW)


def new_video_uid() -> str:
    return uuid.uuid4().hex[:12]

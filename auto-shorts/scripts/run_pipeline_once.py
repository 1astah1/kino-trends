#!/usr/bin/env python3
"""
Прогон одного ролика от темы до готового mp4+QC без очереди и без Postgres —
для локальной обкатки шаблона на этапе разработки (соответствует Этапу 1 плана запуска, ТЗ §12).
Использование: python scripts/run_pipeline_once.py --niche taro --topic "отношения"
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoshorts.config import SETTINGS, load_banner_config, load_niche
from autoshorts.models import Scene, WordTiming
from autoshorts.pipeline.audio_strategy import resolve_audio_strategy
from autoshorts.pipeline.beat_sync import analyze_beats, align_scene_cuts_to_beats
from autoshorts.pipeline.hashtags import format_hashtag_line, generate_hashtags
from autoshorts.pipeline.qc import run_preflight_qc
from autoshorts.pipeline.render import (
    build_scene_clip, caption_margin_v_above_banner, concat_scenes,
    render_final_video, render_preview, two_pass_loudnorm,
)
from autoshorts.pipeline.subtitles import build_karaoke_ass
from autoshorts.pipeline.voice import mock_synthesize_voiceover
from autoshorts.providers.llm import generate_scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--niche", default="taro", choices=["taro", "astro"])
    parser.add_argument("--topic", default="отношения")
    parser.add_argument("--platform", default="youtube", choices=["youtube", "vk", "tiktok"])
    parser.add_argument("--test-scenes-dir", default="render_tmp/test_scenes",
                         help="Директория с заготовленными PNG вместо реальной генерации fal.ai")
    parser.add_argument("--subscribers", type=int, default=1000,
                         help="Число подписчиков канала — влияет на audio_strategy (original sound bonus < 50k на YouTube)")
    args = parser.parse_args()

    job_uid = f"local_{int(time.time())}"
    job_dir = SETTINGS.render_tmp_dir / job_uid
    job_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== job {job_uid} | ниша={args.niche} | тема='{args.topic}' | площадка={args.platform} ===")

    niche = load_niche(args.niche)
    banner_cfg = load_banner_config(args.platform)

    print("[1/8] Генерация сценария...")
    scenario, _ = generate_scenario(niche, args.topic)
    print(f"      voiceover: {scenario.voiceover_text}")

    print("[2/8] Озвучка + пословные тайминги (offline mock)...")
    _, words, _ = mock_synthesize_voiceover(scenario.voiceover_text, job_dir, job_uid)
    print(f"      слов с таймингом: {len(words)}")

    print("[3/8] Определение аудио-стратегии по площадке...")
    audio_strategy = resolve_audio_strategy(args.platform, channel_subscriber_count=args.subscribers)
    print(f"      own_voiceover={audio_strategy.use_own_voiceover} | trending={audio_strategy.use_trending_sound} | beat_sync={audio_strategy.beat_sync_enabled}")
    print(f"      {audio_strategy.trending_sound_note}")

    print("[4/8] Аудио (озвучка -> loudnorm)...")
    target_dur = sum(niche.duration_range) / 2
    scene_count = len(scenario.scenes)
    import subprocess
    raw_audio = job_dir / f"{job_uid}_raw.wav"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=220:duration={target_dur}",
                     str(raw_audio)], check=True, capture_output=True)
    normed_audio = two_pass_loudnorm(raw_audio, job_dir / f"{job_uid}_normed.m4a")

    print("[5/8] Сцены с crop-based Ken Burns (НЕ zoompan)" +
          (" + beat-sync тайминг" if audio_strategy.beat_sync_enabled else "") + "...")
    scene_dir = Path(args.test_scenes_dir)
    source_images = sorted(scene_dir.glob("scene_*.png"))
    if not source_images:
        print(f"      ОШИБКА: нет тестовых изображений в {scene_dir}")
        print(f"      Создай их: ffmpeg -f lavfi -i testsrc2=s=1080x1920:d=1,format=yuv420p -frames:v 1 {scene_dir}/scene_0.png")
        sys.exit(1)

    per_scene = round(target_dur / scene_count, 2)
    scene_durations = [per_scene] * scene_count
    if audio_strategy.beat_sync_enabled:
        try:
            beat_map = analyze_beats(normed_audio)
            scene_durations = align_scene_cuts_to_beats(scene_count, beat_map)
            print(f"      темп аудио: {beat_map.tempo_bpm} BPM, найдено {len(beat_map.beat_times_sec)} битов")
        except Exception as e:
            print(f"      beat-sync не удался ({e}), откат на равномерное деление")

    scenes = [
        Scene(index=i, image_path=str(source_images[i % len(source_images)]),
              duration_sec=scene_durations[i] if i < len(scene_durations) else per_scene,
              pan_direction=["in", "out", "left", "right"][i % 4])
        for i in range(scene_count)
    ]
    clips = [build_scene_clip(s, job_dir, 1080, 1920) for s in scenes]
    scenes_video = concat_scenes(clips, job_dir, job_uid)
    print(f"      {len(clips)} сцен, длительности: {[round(d,2) for d in scene_durations]}")

    print("[6/8] Караоке-субтитры (margin_v рассчитан над зоной баннера)...")
    margin_v = caption_margin_v_above_banner(1920, banner_cfg)
    ass_path = build_karaoke_ass(
        words, job_dir / f"{job_uid}.ass",
        highlight_color=niche.captions["highlight_color"],
        base_color=niche.captions["base_color"],
        margin_v=margin_v,
    )

    hashtags = generate_hashtags(niche, args.topic)
    print(f"      хэштеги: {format_hashtag_line(hashtags)}")

    print("[7/8] Финальный рендер с баннером...")
    final_path = job_dir / f"{job_uid}_final.mp4"
    result = render_final_video(
        scenes_video=scenes_video, audio_path=normed_audio, ass_subtitle_path=ass_path,
        banner_cfg=banner_cfg, offer_text="umka.chat", out_path=final_path,
        fonts_dir=SETTINGS.fonts_dir,
    )
    preview_path = job_dir / f"{job_uid}_preview.mp4"
    render_preview(preview_path, final_path)
    print(f"      готово: {final_path} ({result.duration_sec:.1f}с)")

    print("[8/8] Preflight-QC...")
    qc = run_preflight_qc(final_path, target_duration_range=niche.duration_range)
    status = "✅ PASSED" if qc.passed else "❌ FAILED"
    print(f"      {status}")
    for check, ok in qc.checks.items():
        mark = "OK " if ok else "FAIL"
        print(f"        [{mark}] {check}: {qc.details[check]}")

    print()
    print(f"=== Готово: {final_path} ===")
    print(f"Превью для Telegram: {preview_path}")


if __name__ == "__main__":
    main()

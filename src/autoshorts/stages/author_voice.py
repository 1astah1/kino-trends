"""Стадия clip-job `author_voice` (light, §6.3.6): голос автора на каждую реплику сценария.

Готовая запись оператора (`POST /jobs/{id}/author-voice` → задача `author_voice_prepare`: тишина
по краям срезана, пословные тайминги ASR) важнее синтеза; остальные реплики — edge-tts с
таймингами WordBoundary (`CLIP_TTS=mock` — детерминированная заглушка для тестов, в prod
запрещена). Запись ещё обрабатывается — стадия откладывается. Артефакты `author_voice`
idx 0..3 (cold_open, insert_0, insert_1, outro), тайминги — `stage_payload.clip_voice`.
"""

from __future__ import annotations

from autoshorts.stages.base import NewArtifact, StageContext, StageResult, input_hash, register_stage


@register_stage("clip", "author_voice", "light", 3, False)
def author_voice(ctx: StageContext) -> StageResult:
    from autoshorts.services import clipping

    out = clipping.stage_author_voice(ctx)
    voice = out["voice"]
    artifacts = [
        NewArtifact(
            "author_voice",
            take.path,
            input_hash(ctx.video_uid, "author_voice", role, take.text, int(ctx.task["id"])),
            idx=clipping.SEGMENT_IDX[role],
        )
        for role, take in out["tts_takes"].items()
    ]
    kinds = voice.kinds
    provider = "mock_tts" if "mock" in kinds else "edge_tts" if "tts" in kinds else "operator_recording"
    ctx.progress(1.0)
    return StageResult(
        artifacts=artifacts,
        payload_patch={
            "clip_voice": {
                "takes": {role: clipping.take_to_dict(t) for role, t in voice.takes.items()},
                "kinds": kinds,
                "warnings": list(voice.warnings),
            },
            "voice": {"provider": provider, "kinds": kinds},
            "tts_mock": "mock" in kinds,
        },
    )

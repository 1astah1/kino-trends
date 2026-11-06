"""Стадия clip-job `author_script` (light, платно: LLM, §6.3.8).

Текст авторского слоя: текст оператора (`PUT /jobs/{id}/author-script` или `author_layer.script`)
→ черновик задачи `author_script_draft` → черновик LLM (umka по умолчанию) → без ключа шаблонный
черновик с пометкой «перепишите своими словами». Запрещённые фразы ниши, обещания результата и
ссылки блокируют: черновик LLM перегенерируется до 2 раз, дальше ролик падает с понятной ошибкой
`author_script_blocked` (оператор правит текст и перезапускает со стадии author_script).
Итог — `jobs.author_script` (+ версия), артефакт `script_json`.
"""

from __future__ import annotations

import json

from autoshorts.stages.base import NewArtifact, StageContext, StageResult, input_hash, register_stage


@register_stage("clip", "author_script", "light", 3, True)
def author_script(ctx: StageContext) -> StageResult:
    from autoshorts.services import clipping

    out = clipping.stage_author_script(ctx)
    res = out["result"]
    data = out["data"]
    path = ctx.workdir / f"author_script_{int(ctx.task['id'])}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    parts = res.script.vo_parts()
    texts = dict(parts)
    ctx.progress(1.0)
    return StageResult(
        artifacts=[
            NewArtifact("script_json", path, input_hash(ctx.video_uid, "author_script", data.get("script")))
        ],
        payload_patch={
            "clip_author": {
                "mode": res.mode,
                "label": res.label,
                "blocked": res.blocked,
                "needs_operator_edit": res.needs_operator_edit,
                "source": data.get("source"),
            },
            "script": {
                "hook_text": texts.get("cold_open") or None,
                "voiceover_text": " ".join(t for _, t in parts) or None,
                "cta_text": texts.get("outro") or None,
                "body_text": res.script.on_screen_title or None,
            },
        },
    )

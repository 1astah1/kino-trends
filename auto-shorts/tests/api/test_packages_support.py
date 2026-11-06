"""Помощники тестов M3 (пакеты, публикации, публичный контур). Тестов здесь нет.

    from tests.api.test_packages_support import approved_job, seed_m3, run_package_task

`seed_m3(db)` — ниша taro с хэштегами и запретами, каналы YouTube/VK, кампания umka с вариантом A
(маркировка рекламы с erid) и B (без маркировки). `approved_job(db, media_root, …)` — одобренный
ролик с финальным файлом. `clip_job(…)` — нарезка из источника с правами и отчётом оригинальности.
"""

from __future__ import annotations

import secrets
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from psycopg.types.json import Jsonb

from tests.api.test_jobs_support import add_artifact, make_job

NICHE_CONFIG = {
    "label": "Таро",
    "hashtags": {
        "pool": ["#таро", "#расклад", "#эзотерика", "#гадание"],
        "per_video": 2,
        "fixed": ["#shorts"],
    },
    "script": {"forbidden_phrases": ["гарантирую"]},
}
AD_MARKING = {"required": True, "erid": "2VtzqwXyZ12", "advertiser_text": "ООО «Умка»"}


def seed_m3(db: Any) -> dict[str, Any]:
    db.execute(
        "INSERT INTO niches (id, label, status, current_version, config) "
        "VALUES ('taro', 'Таро', 'active', 1, %s)",
        (Jsonb(NICHE_CONFIG),),
    )
    db.execute("INSERT INTO clusters (id, title) VALUES ('main', 'Основной')")
    db.execute(
        """INSERT INTO channels (id, niche_id, platform, cluster, title, status, default_product_id, url,
                                 platform_channel_ref)
           VALUES ('taro_main', 'taro', 'youtube', 'main', 'Таро · основной', 'active', 'umka',
                   'https://www.youtube.com/@taro_main', 'UCabcdefghijklmnopqrstuv'),
                  ('taro_second', 'taro', 'youtube', 'main', 'Таро · второй', 'active', 'umka',
                   'https://www.youtube.com/@taro_second', NULL),
                  ('taro_vk', 'taro', 'vk', 'main', 'Таро · VK', 'active', 'umka', NULL, 'club123')"""
    )
    campaign = db.execute(
        "INSERT INTO campaigns (product_id, name, status) VALUES ('umka', 'Осень', 'active') RETURNING id"
    ).fetchone()["id"]
    var_a = db.execute(
        """INSERT INTO banner_variants (campaign_id, code, name, status, weight, layers, ad_marking,
                                        pinned_comment_template, description_cta_template)
           VALUES (%s, 'A', 'С маркировкой', 'active', 50, '[]', %s,
                   '{code_url} — бесплатный расклад (ссылка в шапке канала)',
                   'Бесплатный расклад: {code_url} (ссылка в шапке канала)') RETURNING id""",
        (campaign, Jsonb(AD_MARKING)),
    ).fetchone()["id"]
    var_b = db.execute(
        """INSERT INTO banner_variants (campaign_id, code, name, status, weight, layers)
           VALUES (%s, 'B', 'Без маркировки', 'active', 50, '[]') RETURNING id""",
        (campaign,),
    ).fetchone()["id"]
    return {"campaign_id": campaign, "variant_a": var_a, "variant_b": var_b}


def approved_job(
    db: Any,
    media_root: Path | str,
    *,
    content: bytes = b"\x00\x00\x00\x18ftypmp42" + b"v" * 4096,
    **fields: Any,
) -> dict[str, Any]:
    values = {
        "status": "approved",
        "stage": "qc",
        "stage_payload": {
            "script": {
                "hook_text": "Три карты скажут, что он думает о тебе прямо сейчас",
                "body_text": "Выбери карту и узнай ответ. Расклад на отношения за минуту. Досмотри до конца.",
                "voiceover_text": "Выбери карту и узнай ответ.",
            }
        },
    }
    values.update(fields)
    job = make_job(db, **values)
    add_artifact(db, media_root, job, "final", content=content, stage="render")
    return job


def clip_job(
    db: Any,
    media_root: Path | str,
    *,
    rights_type: str = "cc_by",
    valid_until: date | None = None,
    allowlist_status: str = "unknown",
    verdict: str = "pass",
    gate_applies: bool = True,
    voice_sources: tuple[str, ...] = ("tts",),
    **fields: Any,
) -> dict[str, Any]:
    source = db.execute(
        """INSERT INTO sources (title, origin, origin_url, status, niche_id)
           VALUES ('Лекция о картах', 'url', 'https://www.youtube.com/watch?v=abcdefghijk', 'ready', 'taro')
           RETURNING id"""
    ).fetchone()["id"]
    attribution = (
        None
        if rights_type == "own"
        else "«Лекция о картах», Иван Иванов, youtube.com/watch?v=abcdefghijk, CC BY 4.0"
    )
    db.execute(
        """INSERT INTO source_rights (source_id, rights_type, owner_name, proof_note, allowlist_status,
                                      attribution_text, valid_until, license)
           VALUES (%s, %s, 'Иван Иванов', 'лицензия на странице ролика', %s, %s, %s, %s)""",
        (
            source,
            rights_type,
            allowlist_status,
            attribution,
            valid_until,
            "CC BY 4.0" if rights_type == "cc_by" else None,
        ),
    )
    cand = db.execute(
        """INSERT INTO clip_candidates (source_id, start_sec, end_sec, title, hook_text, transcript_excerpt)
           VALUES (%s, 10, 40, 'Почему карты врут', 'Карты не врут никогда?', 'Сегодня разберём...')
           RETURNING id""",
        (source,),
    ).fetchone()["id"]
    originality = {
        "verdict": verdict,
        "summary": {
            "author_share": 0.41 if verdict != "fail" else 0.12,
            "gate_applies": gate_applies,
            "gate_passed": verdict != "fail",
        },
        "fails": ["author_share"] if verdict == "fail" else [],
        "warns": [],
        "checks": [],
        "suggestions": ["Добавьте вставку-комментарий после sid 12"] if verdict == "fail" else [],
        "author_share": {"value": 0.41},
        "rights": {"rights_type": rights_type},
        "notes": [],
    }
    author_script = {
        "script": {
            "cold_open": {
                "vo_text": "Вот что никто не говорит про расклады.",
                "on_screen": "",
                "duck_source": True,
            },
            "inserts": [
                {
                    "after_sid": 3,
                    "mode": "freeze_zoom",
                    "vo_text": "Обратите внимание на слово «всегда».",
                    "on_screen": "",
                }
            ],
            "outro": {"vo_text": "А вы верите картам? Напишите в комментариях.", "cta_style": "question"},
            "on_screen_title": "Карты не врут?",
        },
        "mode": "llm",
        "label": "LLM",
    }
    segments = {
        name: {"source": src}
        for name, src in zip(("cold_open", "insert_0", "outro"), voice_sources, strict=False)
    }
    values = {
        "kind": "clip",
        "status": "approved",
        "stage": "qc",
        "source_id": source,
        "clip_candidate_id": cand,
        "clip_start_sec": 10,
        "clip_end_sec": 40,
        "reframe_mode": "auto",
        "author_layer": {"mode": "llm_draft", "voice": "tts", "captions": "both"},
        "topic": None,
    }
    values.update(fields)
    job = make_job(db, **values)
    db.execute(
        """UPDATE jobs SET author_script = %s, author_voice = %s, originality = %s, originality_verdict = %s
           WHERE id = %s""",
        (Jsonb(author_script), Jsonb({"segments": segments}), Jsonb(originality), verdict, job["id"]),
    )
    add_artifact(db, media_root, job, "final", content=b"clip-final" * 100, stage="render")
    return {**job, "source_id": source, "candidate_id": cand}


def run_package_task(job_id: int, *, budget: Any = None) -> dict[str, Any]:
    """Выполнить обработчик `package_build` как воркер: обработчик, затем хуки в одной транзакции."""
    from autoshorts.db.pool import connection
    from autoshorts.stages.package_build import package_build

    hooks: list[Any] = []
    ctx = SimpleNamespace(
        job_id=job_id,
        payload={"job_id": job_id},
        budget=budget,
        check_cancelled=lambda: None,
        progress=lambda _p: None,
        on_commit=hooks.append,
    )
    result = package_build(ctx)  # type: ignore[arg-type]
    with connection() as conn:
        for hook in hooks:
            hook(conn, {"id": 0})
    return result


def package_row(db: Any, job_id: int) -> dict[str, Any] | None:
    return db.execute(
        "SELECT * FROM upload_packages WHERE job_id = %s AND status <> 'superseded' ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()


def media_root_of(app_env: Any) -> Path:
    return Path(app_env.media_root)


def unique_uid() -> str:
    return secrets.token_hex(6)


def days_ago(n: int) -> date:
    return date.today() - timedelta(days=n)

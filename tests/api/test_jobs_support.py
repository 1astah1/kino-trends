"""Общие фикстуры и помощники тестов роликов и приёмки (M2-C). Тестов здесь нет.

Подключение в тестовом модуле:

    from tests.api.test_jobs_support import add_artifact, make_job, seed_catalog

    @pytest.fixture
    def catalog(db): return seed_catalog(db)

`seed_catalog(db)` — ниша, куст, каналы YouTube и VK, кампания продукта umka с вариантами A и B.
`make_job(db, …)` — ролик сразу в нужном статусе (SQL в обход сервиса), `add_artifact` — файл
в MEDIA_ROOT и строка `artifacts`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

NICHE_CONFIG = {"label": "Таро", "duration_target_sec": [15, 25]}
LAYERS = [
    {
        "id": "creative",
        "kind": "creative",
        "show": "with_creative",
        "z": 10,
        "creative_sha256": "a" * 64,
        "slot": {"x": 65, "y": 1120, "w": 825, "h": 190, "anchor": "bottom_center"},
    },
    {
        "id": "code",
        "kind": "text_code",
        "show": "with_creative",
        "z": 20,
        "text": "{code_url}",
        "slot": {"x": 65, "y": 1316, "w": 825, "h": 50, "anchor": "top_center"},
    },
    {
        "id": "micro_brand",
        "kind": "micro_brand",
        "show": "persistent",
        "z": 0,
        "text": "umka.chat",
        "slot": {"x": 65, "y": 200, "w": 440, "h": 58, "anchor": "top_left"},
    },
]
BANNER_PLAN = {
    "v": 1,
    "out_size": [1080, 1920],
    "duration_sec": 21.4,
    "windows": {
        "persistent": [{"start_sec": 0.0, "end_sec": 21.4}],
        "with_creative": [{"start_sec": 8.6, "end_sec": 13.6}, {"start_sec": 18.9, "end_sec": 21.4}],
    },
    "rects": {
        "creative": {"x1": 65, "y1": 1120, "x2": 890, "y2": 1310},
        "code": {"x1": 65, "y1": 1316, "x2": 890, "y2": 1366},
        "micro_brand": {"x1": 65, "y1": 200, "x2": 505, "y2": 258},
    },
    "captions_zone": {"x1": 60, "y1": 700, "x2": 1020, "y2": 1100},
    "warnings": [
        {
            "code": "creative_in_bottom_dead_zone",
            "severity": "warn",
            "layer_id": "creative",
            "profile_id": "youtube_shorts",
            "message": "Креатив заходит в нижнюю мёртвую зону",
            "details": {},
        }
    ],
    "items": [],
}


def seed_catalog(db: Any) -> dict[str, Any]:
    """Справочники для роликов: ниша taro, куст main, каналы taro_main (YouTube) и taro_vk (VK)."""
    db.execute(
        "INSERT INTO niches (id, label, status, current_version, config) "
        "VALUES ('taro', 'Таро', 'active', 3, %s)",
        (Jsonb(NICHE_CONFIG),),
    )
    db.execute("INSERT INTO niches (id, label, status) VALUES ('astro', 'Астро', 'active')")
    db.execute("INSERT INTO clusters (id, title) VALUES ('main', 'Основной')")
    db.execute(
        """INSERT INTO channels (id, niche_id, platform, cluster, title, status, default_product_id)
           VALUES ('taro_main', 'taro', 'youtube', 'main', 'Таро · основной', 'active', 'umka'),
                  ('taro_vk', 'taro', 'vk', 'main', 'Таро · VK', 'active', 'umka'),
                  ('astro_yt', 'astro', 'youtube', 'main', 'Астро', 'active', NULL)"""
    )
    campaign = db.execute(
        "INSERT INTO campaigns (product_id, name, status) VALUES ('umka', 'Осень', 'active') RETURNING id"
    ).fetchone()["id"]
    other = db.execute(
        "INSERT INTO campaigns (product_id, name, status) VALUES ('umka', 'Другая', 'active') RETURNING id"
    ).fetchone()["id"]
    var_a = db.execute(
        """INSERT INTO banner_variants (campaign_id, code, name, status, weight, layers, ad_marking)
           VALUES (%s, 'A', 'Домен + расклад', 'active', 50, %s, %s) RETURNING id""",
        (campaign, Jsonb(LAYERS), Jsonb({"required": False, "erid": None, "advertiser_text": None})),
    ).fetchone()["id"]
    var_b = db.execute(
        """INSERT INTO banner_variants (campaign_id, code, name, status, weight, layers)
           VALUES (%s, 'B', 'Второй', 'active', 50, '[]') RETURNING id""",
        (campaign,),
    ).fetchone()["id"]
    var_x = db.execute(
        """INSERT INTO banner_variants (campaign_id, code, name, status, weight, layers)
           VALUES (%s, 'X', 'Чужой', 'archived', 50, '[]') RETURNING id""",
        (other,),
    ).fetchone()["id"]
    return {
        "campaign_id": campaign,
        "other_campaign_id": other,
        "variant_a": var_a,
        "variant_b": var_b,
        "variant_archived": var_x,
    }


def make_job(db: Any, **fields: Any) -> dict[str, Any]:
    """Ролик SQL-ом. По умолчанию generate, pending_review на стадии qc, канал taro_main."""
    import secrets

    values: dict[str, Any] = {
        "video_uid": secrets.token_hex(6),
        "channel_id": "taro_main",
        "niche_id": "taro",
        "template_version": "v1",
        "kind": "generate",
        "status": "pending_review",
        "stage": "qc",
        "product_id": "umka",
        "duration_sec": 21.4,
        "topic": "отношения",
    }
    values.update(fields)
    for key in ("stage_payload", "config_snapshot", "qc_warnings", "fingerprint", "author_layer"):
        if key in values and values[key] is not None and not isinstance(values[key], Jsonb):
            values[key] = Jsonb(values[key])
    names = sorted(values)
    row = db.execute(
        f"INSERT INTO jobs ({', '.join(names)}) "
        f"VALUES ({', '.join('%(' + n + ')s' for n in names)}) RETURNING *",
        values,
    ).fetchone()
    return row


def add_artifact(
    db: Any,
    media_root: Path | str,
    job: dict[str, Any],
    kind: str,
    *,
    idx: int = 0,
    stage: str | None = None,
    content: bytes | str = b"data",
    ext: str | None = None,
    input_hash: str | None = None,
) -> dict[str, Any]:
    exts = {"final": ".mp4", "preview": ".mp4", "poster": ".jpg", "voice": ".mp3", "scene_image": ".png",
            "banner_png": ".png", "words_json": ".json", "script_json": ".json"}  # fmt: skip
    rel = f"jobs/{job['video_uid']}/{kind}_{idx}{ext or exts.get(kind, '.bin')}"
    path = Path(media_root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = content.encode() if isinstance(content, str) else content
    path.write_bytes(data)
    return db.execute(
        """INSERT INTO artifacts (job_id, kind, idx, path, bytes, input_hash, stage)
           VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *""",
        (job["id"], kind, idx, rel, len(data), input_hash or f"h-{kind}-{idx}", stage),
    ).fetchone()


def job_row(db: Any, job_id: int) -> dict[str, Any]:
    return db.execute("SELECT * FROM jobs WHERE id = %s", (job_id,)).fetchone()


def job_tasks(db: Any, job_id: int, *, kind: str | None = None) -> list[dict[str, Any]]:
    rows = db.execute("SELECT * FROM tasks WHERE job_id = %s ORDER BY id", (job_id,)).fetchall()
    return [r for r in rows if kind is None or r["kind"] == kind]


def words_json(words: list[tuple[str, float, float]]) -> str:
    return json.dumps([{"word": w, "start_sec": s, "end_sec": e} for w, s, e in words], ensure_ascii=False)


def err(resp: Any) -> dict[str, Any]:
    body = resp.json()
    assert "error" in body, body
    return body["error"]

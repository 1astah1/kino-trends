"""Публичная атрибуция в описании пакета: без служебных ссылок и дубля «Источник:»,
«Изменения» только при CC BY."""

from __future__ import annotations

from typing import Any

from autoshorts.services.packages import (
    PackageInputs,
    attribution_block,
    clean_hashtags,
    hashtags_for,
    sanitize_attribution,
)


def _inp(rights_type: str, attribution: str | None, **job: Any) -> PackageInputs:
    base = {
        "id": 1,
        "kind": "clip",
        "platform": "youtube",
        "video_uid": "v1",
        "niche_id": "kino",
        "author_script": {},
    }
    base.update(job)
    return PackageInputs(
        job=base,
        product_id="p",
        product=None,
        niche={"label": "Кино", "config": {}},
        variant=None,
        rights={"rights_type": rights_type, "attribution_text": attribution},
        candidate=None,
        previous=None,
    )


def test_storage_link_and_duplicate_prefix_are_removed() -> None:
    raw = "Источник: Источник: Марс Медиа (http://192.0.2.10/files/sample-t34.mp4)"
    assert sanitize_attribution(raw, "licensed_platform") == "Марс Медиа"
    block = attribution_block(_inp("licensed_platform", raw))
    assert block == "Источник: Марс Медиа"
    assert "Изменения" not in block and "http" not in block


def test_film_attribution_from_kino_trends_has_no_prefix_and_no_changes() -> None:
    block = attribution_block(_inp("licensed_platform", "фрагмент фильма «Брестская крепость» (2010)"))
    assert block == "Фрагмент фильма «Брестская крепость» (2010)"


def test_cc_by_keeps_youtube_link_and_changes_line() -> None:
    raw = "автор Иван Иванов (https://www.youtube.com/watch?v=abcdefghijk), лицензия CC BY"
    assert sanitize_attribution(raw, "cc_by") == raw
    block = attribution_block(_inp("cc_by", raw))
    assert block.startswith(
        "Автор Иван Иванов (https://www.youtube.com/watch?v=abcdefghijk), лицензия CC BY\n"
    )
    assert "Изменения: вертикальный кадр 9:16" in block


def test_non_youtube_link_is_dropped_even_for_cc_by() -> None:
    assert sanitize_attribution("автор X (http://192.0.2.10/files/a.mp4), лицензия CC BY", "cc_by") == (
        "автор X, лицензия CC BY"
    )


def test_own_rights_have_no_attribution() -> None:
    assert attribution_block(_inp("own", "что угодно")) is None


def test_clean_hashtags_normalises_and_dedupes() -> None:
    tags = clean_hashtags(["#Т34", "т 34", "#т34", "## кино", "#shorts", "https://x.ru", "#"])
    assert tags == ["#Т34", "#кино", "#shorts"]


def test_hashtags_for_merges_fixed_llm_and_pool() -> None:
    inp = _inp("licensed_platform", None)
    inp.niche["config"] = {
        "hashtags": {"fixed": ["#shorts", "#кино"], "pool": ["#фильм", "#сцена"], "per_video": 1}
    }
    tags = hashtags_for(inp, ["#Т34", "#кино"])
    assert tags[:3] == ["#shorts", "#кино", "#Т34"]
    assert len(tags) == 4 and tags[3] in ("#фильм", "#сцена")
    # когда LLM дал три и больше тегов под ролик, из пула добавляется только один общий
    inp.niche["config"]["hashtags"]["per_video"] = 2
    many = hashtags_for(inp, ["#Т34", "#танки", "#война"])
    assert many[:5] == ["#shorts", "#кино", "#Т34", "#танки", "#война"] and len(many) == 6

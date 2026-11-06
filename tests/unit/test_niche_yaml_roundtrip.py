"""Конфиг ниши: YAML → схема → YAML без потерь (M2 критерий), ошибки по путям, новые секции."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from autoshorts.domain.niche_schema import (
    NicheConfig,
    config_warnings,
    dump_config,
    dump_yaml,
    issue_path,
    load_yaml,
    validate_config,
)

ROOT = Path(__file__).resolve().parents[2]
NICHE_FILES = sorted((ROOT / "config" / "niches").glob("*.yaml"))


def test_niche_files_exist() -> None:
    assert {p.stem for p in NICHE_FILES} >= {"taro", "astro"}


@pytest.mark.parametrize("path", NICHE_FILES, ids=lambda p: p.stem)
def test_yaml_schema_yaml_is_lossless(path: Path) -> None:
    original = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg, issues = validate_config(original)
    assert cfg is not None, issues
    stored = dump_config(cfg)  # что ляжет в niches.config
    assert stored == original  # JSON для БД — ровно то, что было в YAML
    text = dump_yaml(stored, niche_id=original["id"], label=original["label"])
    again = load_yaml(text)
    assert again == original
    # и вторая итерация ничего не меняет
    assert dump_yaml(dump_config(NicheConfig.model_validate(again))) == text


@pytest.mark.parametrize("path", NICHE_FILES, ids=lambda p: p.stem)
def test_defaults_are_available_but_not_stored(path: Path) -> None:
    raw = load_yaml(path.read_text(encoding="utf-8"))
    cfg = NicheConfig.model_validate(raw)
    if "hashtags" not in raw:
        # ниша без своего пула хэштегов: умолчания доступны, но в конфиг не попадают
        assert cfg.hashtags.per_video == 3 and cfg.hashtags.pool == []
        assert "hashtags" not in dump_config(cfg)
    else:
        assert cfg.hashtags.pool or cfg.hashtags.fixed
    assert cfg.prompts.system is None
    assert "prompts" not in dump_config(cfg)
    assert cfg.duration_range[0] <= cfg.duration_range[1]
    assert cfg.forbidden_phrases


def test_hashtags_and_prompts_roundtrip() -> None:
    data = {
        "id": "taro",
        "label": "Таро",
        "hashtags": {"pool": ["#таро", "#расклад", "#эзотерика"], "per_video": 2, "fixed": ["#shorts"]},
        "prompts": {"system": "Ты пишешь сценарии про таро.", "body_hint": "Три карты, без обещаний"},
    }
    cfg, issues = validate_config(data)
    assert cfg is not None, issues
    assert load_yaml(dump_yaml(dump_config(cfg))) == data
    assert not [w for w in config_warnings(cfg) if w.path.startswith("hashtags")]


def test_errors_have_paths() -> None:
    data = {
        "script": {
            "hooks": [{"pattern": "direct_address", "template": "ok"}, {"pattern": "Плохой", "template": ""}]
        },
        "duration": {"target_sec": [30, 10]},
        "captions": {"highlight_color": "yellow"},
        "unknown_section": {},
    }
    cfg, issues = validate_config(data)
    assert cfg is None
    paths = {i.path for i in issues}
    assert "script.hooks[1].pattern" in paths
    assert "script.hooks[1].template" in paths
    assert "duration.target_sec" in paths
    assert "captions.highlight_color" in paths
    assert "unknown_section" in paths
    assert all(p.isascii() for p in paths)


def test_forbidden_phrase_inside_hook_is_an_error() -> None:
    data = {
        "script": {
            "hooks": [{"pattern": "stakes", "template": "Это гарантированно сбудется"}],
            "forbidden_phrases": ["гарантированно"],
        }
    }
    cfg, issues = validate_config(data)
    assert cfg is None and "гарантированно" in issues[0].msg


def test_bad_hashtag_rejected() -> None:
    cfg, issues = validate_config({"hashtags": {"pool": ["таро без решётки"]}})
    assert cfg is None and issues[0].path == "hashtags.pool"


def test_empty_config_is_valid_with_warnings() -> None:
    cfg, issues = validate_config({})
    assert cfg is not None and not issues
    assert dump_config(cfg) == {}
    assert {w.path for w in config_warnings(cfg)} >= {
        "script.hooks",
        "script.forbidden_phrases",
        "hashtags.pool",
    }


def test_issue_path_format() -> None:
    assert issue_path(("script", "hooks", 2, "template")) == "script.hooks[2].template"
    assert issue_path(()) == "$"


def test_load_yaml_rejects_non_mapping() -> None:
    with pytest.raises(ValueError):
        load_yaml("- a\n- b\n")
    with pytest.raises(ValueError):
        load_yaml("id: [unclosed")

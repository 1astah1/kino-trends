"""M0: seed картинок стабилен между процессами (ARCHITECTURE §3.2, §3.3 п.4, §10.2 M0)."""
import dataclasses
import json
import os
import subprocess
import sys
from hashlib import blake2b
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from autoshorts.pipeline import orchestrator  # noqa: E402

VIDEO_UID = "a1b2c3d4e5f6"
SCENES = 6

_CHILD = """
import json, sys
sys.path.insert(0, {src!r})
from autoshorts.pipeline.orchestrator import scene_image_seed
uid, n = sys.argv[1], int(sys.argv[2])
print(json.dumps({{
    "seeds": [scene_image_seed(uid, i) for i in range(n)],
    "builtin_hash": [hash(f"{{uid}}_{{i}}") % 10_000 for i in range(n)],
}}))
"""


def _run_child(hashseed: str, tmp_path: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = hashseed
    # config.py при импорте создаёт каталоги — уводим их во временную папку
    env["RENDER_TMP_DIR"] = str(tmp_path / "render_tmp")
    env["MASTER_ARCHIVE_DIR"] = str(tmp_path / "master_archive")
    out = subprocess.run(
        [sys.executable, "-c", _CHILD.format(src=str(ROOT / "src")), VIDEO_UID, str(SCENES)],
        env=env, cwd=str(tmp_path), capture_output=True, text=True, check=True, timeout=120,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_seed_same_under_different_pythonhashseed(tmp_path):
    a = _run_child("1", tmp_path)
    b = _run_child("2", tmp_path)
    assert a["seeds"] == b["seeds"]
    # Контроль: старый способ hash() действительно плывёт между процессами — тест ловит регрессию.
    assert a["builtin_hash"] != b["builtin_hash"]


def test_seed_matches_documented_formula():
    for i in range(SCENES):
        expected = int(blake2b(f"{VIDEO_UID}:{i}".encode()).hexdigest()[:8], 16)
        assert orchestrator.scene_image_seed(VIDEO_UID, i) == expected
        assert 0 <= expected < 2**32


def test_seeds_differ_between_scenes_and_videos():
    seeds = {orchestrator.scene_image_seed(VIDEO_UID, i) for i in range(SCENES)}
    assert len(seeds) == SCENES
    assert orchestrator.scene_image_seed(VIDEO_UID, 0) != orchestrator.scene_image_seed("ffffffffffff", 0)


class _StopAfterFirstImage(Exception):
    pass


def test_render_stage_passes_blake2b_seed(monkeypatch, tmp_path):
    """run_stage_render отдаёт в генератор картинок именно scene_image_seed, а не hash()."""
    captured = []

    def fake_generate_scene_image(prompt, out_path, seed, provider_id=None):
        captured.append(seed)
        raise _StopAfterFirstImage

    monkeypatch.setattr(orchestrator, "generate_scene_image", fake_generate_scene_image)
    monkeypatch.setattr(orchestrator.db, "check_and_reserve_budget", lambda cost: True)
    monkeypatch.setattr(
        orchestrator, "SETTINGS",
        dataclasses.replace(orchestrator.SETTINGS, render_tmp_dir=tmp_path, fal_api_key=""),
    )
    job = {"id": 1, "video_uid": VIDEO_UID, "niche": "taro",
           "stage_payload": {"scene_count": SCENES, "topic": "отношения"}}
    with pytest.raises(_StopAfterFirstImage):
        orchestrator.run_stage_render(job)
    assert captured == [orchestrator.scene_image_seed(VIDEO_UID, 0)]

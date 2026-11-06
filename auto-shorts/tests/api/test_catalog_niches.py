"""Каталог M2-A: ниши (версии, 409, YAML импорт/экспорт, проверка) и критерий M2 import-niches."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
NICHES = sorted(str(p) for p in (ROOT / "config" / "niches").glob("*.yaml"))


def _admin_cli(app_env: Any, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": app_env.database_url}
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from autoshorts.cli import main; sys.exit(main(sys.argv[1:]))",
            *args,
        ],
        env=env,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_m2_criterion_import_niches_and_banner(app_env, login_as, db) -> None:
    # в legacy-базе ниша могла появиться из каналов со статусом needs_import — импорт её заполняет
    db.execute("INSERT INTO niches (id, label, status) VALUES ('taro', 'taro', 'needs_import')")
    res = _admin_cli(app_env, "import-niches", *NICHES)
    assert res.returncode == 0, res.stderr
    assert "taro: created" in res.stdout and "astro: created" in res.stdout
    res = _admin_cli(app_env, "import-banner", str(ROOT / "config" / "banner.yaml"), "--name", "default")
    assert res.returncode == 0, res.stderr
    assert "created" in res.stdout

    viewer = login_as("viewer")
    page = viewer.get("/niches").json()
    assert page["total"] == 2
    assert {(n["id"], n["status"]) for n in page["items"]} == {("taro", "active"), ("astro", "active")}
    taro = viewer.get("/niches/taro").json()
    assert taro["label"] == "Таро — выбери карту" and taro["current_version"] == 1
    assert taro["config"]["script"]["format"] == "pick_a_card"
    templates = viewer.get("/banner-templates").json()["items"]
    assert [t["name"] for t in templates] == ["default"]
    layers = {la["id"]: la for la in templates[0]["layout"]["layers"]}
    assert layers["micro_brand"]["text"] == "umka.chat" and layers["fallback_plate"]["enabled"] is True
    assert templates[0]["layout"]["timing"]["hook_silence_sec"] == 2.0

    # повторный импорт тех же файлов ничего не меняет
    again = _admin_cli(app_env, "import-niches", *NICHES)
    assert again.returncode == 0 and "unchanged" in again.stdout
    again = _admin_cli(app_env, "import-banner", str(ROOT / "config" / "banner.yaml"), "--name", "default")
    assert again.returncode == 0 and "unchanged" in again.stdout
    assert viewer.get("/niches/taro").json()["current_version"] == 1

    versions = db.execute("SELECT niche_id, version, source FROM niche_versions ORDER BY niche_id").fetchall()
    assert [(v["niche_id"], v["version"], v["source"]) for v in versions] == [
        ("astro", 1, "yaml_import"),
        ("taro", 1, "yaml_import"),
    ]


def test_import_cli_reports_bad_file(app_env, tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("id: bad_niche\nscript:\n  word_count: [90, 10]\n", encoding="utf-8")
    res = _admin_cli(app_env, "import-niches", str(bad))
    assert res.returncode == 1
    assert "script.word_count" in res.stderr


def test_niche_versions_and_conflict(login_as, db) -> None:
    op = login_as("operator")
    created = op.post(
        "/niches",
        json={"id": "numerology", "label": "Нумерология", "config": {"script": {"format": "numerology"}}},
    )
    assert created.status_code == 201, created.text
    niche = created.json()
    assert niche["current_version"] == 1 and niche["status"] == "active" and niche["used_by_channels"] == []

    put = op.put(
        "/niches/numerology",
        json={"config": {"script": {"format": "numerology", "tone": "спокойный"}}, "expected_version": 1},
    )
    assert put.status_code == 200, put.text
    assert put.json()["current_version"] == 2 and put.json()["config"]["script"]["tone"] == "спокойный"

    stale = op.put("/niches/numerology", json={"config": {}, "expected_version": 1})
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"
    assert stale.json()["error"]["details"]["current_version"] == 2

    bad = op.put(
        "/niches/numerology", json={"config": {"duration": {"target_sec": [90, 10]}}, "expected_version": 2}
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["details"]["config_errors"][0]["path"] == "duration.target_sec"

    versions = op.get("/niches/numerology/versions").json()["items"]
    assert [v["version"] for v in versions] == [2, 1] and versions[0]["source"] == "ui"
    v1 = op.get("/niches/numerology/versions/1").json()
    assert v1 == {"version": 1, "config": {"script": {"format": "numerology"}}}
    assert op.get("/niches/numerology/versions/9").status_code == 404

    patched = op.patch("/niches/numerology", json={"label": "Числа судьбы", "status": "archived"})
    assert patched.status_code == 200 and patched.json()["status"] == "archived"
    assert op.get("/niches", params={"status": "archived"}).json()["total"] == 1

    dup = op.post("/niches", json={"id": "numerology", "label": "x"})
    assert dup.status_code == 422 and dup.json()["error"]["details"]["errors"][0]["loc"] == ["body", "id"]

    clone = op.post("/niches", json={"id": "numerology2", "label": "Копия", "clone_from": "numerology"})
    assert clone.status_code == 201 and clone.json()["config"]["script"]["tone"] == "спокойный"

    audit = db.execute("SELECT count(*) AS n FROM audit_log WHERE entity_type = 'niche'").fetchone()
    assert audit["n"] >= 4


def test_niche_yaml_import_export_and_validate(login_as) -> None:
    op = login_as("operator")
    text = (ROOT / "config" / "niches" / "taro.yaml").read_text(encoding="utf-8")
    created = op.post("/niches/import-yaml", files={"file": ("taro.yaml", text.encode(), "text/yaml")})
    assert created.status_code == 201, created.text
    again = op.post("/niches/import-yaml", files={"file": ("taro.yaml", text.encode(), "text/yaml")})
    assert again.status_code == 422  # mode=create по умолчанию

    exported = op.get("/niches/taro/export-yaml")
    assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/yaml")
    assert yaml.safe_load(exported.text) == yaml.safe_load(text)

    changed = text.replace("pacing: medium", "pacing: fast")
    replaced = op.post(
        "/niches/import-yaml",
        files={"file": ("taro.yaml", changed.encode(), "text/yaml")},
        data={"mode": "replace"},
    )
    assert replaced.status_code == 201 and replaced.json()["current_version"] == 2
    assert replaced.json()["config"]["script"]["pacing"] == "fast"

    broken = op.post("/niches/import-yaml", files={"file": ("x.yaml", b"id: [oops", "text/yaml")})
    assert broken.status_code == 422

    # alias bomb: ссылки *alias отвергаются до обхода (иначе str(label) раздувается до гигабайт)
    bomb = ['a0: &a0 "lol"'] + [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 10)}]" for i in range(1, 10)]
    bomb_text = "\n".join([*bomb, "id: bomb", "label: *a9"]).encode()
    r = op.post("/niches/import-yaml", files={"file": ("bomb.yaml", bomb_text, "text/yaml")})
    assert r.status_code == 422 and r.json()["error"]["details"]["errors"][0]["type"] == "yaml_invalid"
    r = op.post(
        "/niches/import-yaml", files={"file": ("l.yaml", b"id: labeled\nlabel: {a: 1}\n", "text/yaml")}
    )
    assert r.status_code == 422 and r.json()["error"]["details"]["errors"][0]["loc"][-1] == "label"

    ok = op.post("/niches/taro/validate", json={"config": {"script": {"hooks": []}}})
    assert ok.status_code == 200 and ok.json()["ok"] is True
    assert any(w["path"] == "script.hooks" for w in ok.json()["warnings"])
    bad = op.post(
        "/niches/taro/validate", json={"config": {"id": "astro", "captions": {"base_color": "white"}}}
    )
    assert bad.json()["ok"] is False
    assert {e["path"] for e in bad.json()["errors"]} == {"captions.base_color"}


def test_niche_roles(login_as) -> None:
    viewer = login_as("viewer")
    assert viewer.get("/niches").status_code == 200
    denied = viewer.post("/niches", json={"id": "abc", "label": "x"})
    assert denied.status_code == 403
    assert viewer.get("/niches/nope").status_code == 404
    assert viewer.get("/niches/nope").json()["error"]["code"] == "niche_not_found"

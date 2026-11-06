"""Пакеты «К заливке» (M3-A): сборка задачей package_build, тексты LLM/шаблон, атрибуция и «Изменения»
для нарезки, чек-лист (AI-метка, маркировка, Content ID), трекинг-код и ссылки, файлы, правки."""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

import pytest

from autoshorts.api.schemas.packages import UploadPackage
from tests.api.test_packages_support import (
    AD_MARKING,
    approved_job,
    clip_job,
    days_ago,
    package_row,
    run_package_task,
    seed_m3,
)


@pytest.fixture
def m3(db: Any) -> dict[str, Any]:
    return seed_m3(db)


@pytest.fixture
def media_root(app_env: Any) -> Path:
    return Path(app_env.media_root)


@pytest.fixture
def operator(login_as: Any) -> Any:
    return login_as("operator")


@pytest.fixture
def no_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    from autoshorts.services import packages

    monkeypatch.setattr(packages, "make_llm_client", lambda: None)


def _mock_llm(monkeypatch: pytest.MonkeyPatch, reply: dict[str, Any]) -> list[dict[str, Any]]:
    from autoshorts.clipping.llm_client import LLMClient
    from autoshorts.services import packages

    calls: list[dict[str, Any]] = []

    def responder(messages: list[dict[str, Any]], mode: str) -> str:
        calls.append({"messages": messages, "mode": mode})
        return json.dumps(reply, ensure_ascii=False)

    monkeypatch.setattr(packages, "make_llm_client", lambda: LLMClient.mock(responder, model="mock-texts"))
    return calls


def _checklist_codes(row: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {i["code"]: i for i in row["checklist"]}


# ---------------------------------------------------------------- сборка


def test_package_task_builds_generate_package_with_template_texts(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    job = approved_job(db, media_root, banner_variant_id=m3["variant_a"], campaign_id=m3["campaign_id"])
    result = run_package_task(int(job["id"]))
    assert result["texts_mode"] == "template"
    row = package_row(db, int(job["id"]))
    assert row is not None and row["status"] == "ready"
    link = db.execute("SELECT * FROM tracking_links WHERE id = %s", (row["tracking_link_id"],)).fetchone()
    assert link["level"] == "package" and link["job_id"] == job["id"] and link["product_id"] == "umka"
    code = link["code"]
    assert re.fullmatch(r"[23456789abcdefghjkmnpqrstvwxyz]{5}", code)

    resp = operator.get(f"/jobs/{job['id']}/package")
    assert resp.status_code == 200, resp.text
    pkg = UploadPackage.model_validate(resp.json())
    assert pkg.texts.texts_mode == "template"
    assert "без LLM" in pkg.texts.texts_label
    assert pkg.texts.title.startswith("Три карты")
    # CTA варианта с кодом, маркировка рекламы с erid
    assert f"u.umka.chat/{code}" in pkg.texts.description
    assert "Реклама." in pkg.texts.description and f"erid: {AD_MARKING['erid']}" in pkg.texts.description
    assert AD_MARKING["advertiser_text"] in pkg.texts.description
    assert pkg.texts.pinned_comment == f"u.umka.chat/{code} — бесплатный расклад (ссылка в шапке канала)"
    # хэштеги ниши: fixed + 2 из пула, детерминированно
    assert pkg.texts.hashtags[0] == "#shorts" and len(pkg.texts.hashtags) == 3
    assert pkg.texts.attribution_text is None
    # ссылки: домен-редирект, tg://resolve и https-ссылка на бота с кодом
    assert pkg.tracking.code == code
    assert pkg.tracking.redirect_url == f"https://u.umka.chat/{code}"
    assert pkg.tracking.telegram_url == f"tg://resolve?domain=umka_bot&start={code}"
    assert pkg.tracking.telegram_https_url == f"https://telegram.me/umka_bot?start={code}"
    assert pkg.tracking.site_url and f"utm_campaign={code}" in pkg.tracking.site_url
    # генерация: AI-метка обязательна
    assert pkg.ai_disclosure_required is True
    items = {i.code.value: i for i in pkg.checklist}
    assert items["ai_disclosure"].required is True
    assert items["related_video"].required is False
    assert pkg.file.filename.startswith("taro_main_") and pkg.file.filename.endswith(f"_{code}.mp4")
    assert pkg.originality is None
    # в БД хранится и пункт маркировки (в ответ — когда код появится в ChecklistCode)
    assert "ad_marking" in _checklist_codes(row)

    # повтор задачи на тот же финал ничего не создаёт
    again = run_package_task(int(job["id"]))
    assert again["skipped"] == "package_exists"
    n = db.execute("SELECT count(*) AS n FROM upload_packages WHERE job_id = %s", (job["id"],)).fetchone()[
        "n"
    ]
    assert n == 1


def test_package_reuses_code_reserved_by_render(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, no_llm: None
) -> None:
    job = approved_job(db, media_root)
    db.execute(
        """INSERT INTO tracking_links (code, product_id, level, job_id, channel_id, platform)
           VALUES ('abcde', 'umka', 'package', %s, 'taro_main', 'youtube')""",
        (job["id"],),
    )
    run_package_task(int(job["id"]))
    row = package_row(db, int(job["id"]))
    code = db.execute("SELECT code FROM tracking_links WHERE id = %s", (row["tracking_link_id"],)).fetchone()
    assert code["code"] == "abcde"
    assert "u.umka.chat/abcde" in row["description"]


def test_llm_texts_are_used_and_labelled(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _mock_llm(
        monkeypatch,
        {"title": "Что он думает о тебе: три карты", "description": "Короткий расклад на отношения."},
    )
    job = approved_job(db, media_root, banner_variant_id=m3["variant_b"], campaign_id=m3["campaign_id"])
    result = run_package_task(int(job["id"]))
    assert result["texts_mode"] == "llm"
    assert calls and calls[0]["mode"] == "json_schema"
    assert "Три карты" in calls[0]["messages"][-1]["content"]
    pkg = operator.get(f"/jobs/{job['id']}/package").json()
    assert pkg["texts"]["texts_mode"] == "llm"
    assert pkg["texts"]["title"] == "Что он думает о тебе: три карты"
    assert pkg["texts"]["description"].startswith("Короткий расклад на отношения.")
    assert "LLM" in pkg["texts"]["texts_label"]
    # вариант B без маркировки: строки «Реклама» нет, пункта маркировки нет
    assert "Реклама" not in pkg["texts"]["description"]
    row = package_row(db, int(job["id"]))
    assert "ad_marking" not in _checklist_codes(row)
    # CTA по умолчанию
    assert (
        f"Подробнее: u.umka.chat/{pkg['tracking']['code']} (ссылка в шапке канала)"
        in pkg["texts"]["description"]
    )


@pytest.mark.parametrize(
    "reply",
    [
        {"title": "Заходи на umka.chat за раскладом", "description": "Описание."},
        {"title": "Гарантирую точный ответ", "description": "Описание."},
    ],
)
def test_llm_text_with_link_or_forbidden_phrase_falls_back_to_template(
    client: Any,
    db: Any,
    m3: dict[str, Any],
    media_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    reply: dict[str, Any],
) -> None:
    _mock_llm(monkeypatch, reply)
    job = approved_job(db, media_root)
    assert run_package_task(int(job["id"]))["texts_mode"] == "template"
    assert package_row(db, int(job["id"]))["texts_mode"] == "template"


def test_clip_cc_by_package_has_attribution_changes_and_ai_disclosure(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    job = clip_job(db, media_root, rights_type="cc_by", voice_sources=("tts", "operator", "tts"))
    run_package_task(int(job["id"]))
    resp = operator.get(f"/jobs/{job['id']}/package")
    assert resp.status_code == 200, resp.text
    pkg = UploadPackage.model_validate(resp.json())
    desc = pkg.texts.description
    assert pkg.kind.value == "clip"
    assert pkg.texts.title == "Карты не врут?"
    attribution = "«Лекция о картах», Иван Иванов, youtube.com/watch?v=abcdefghijk, CC BY 4.0"
    assert pkg.texts.attribution_text == attribution
    assert f"Источник: {attribution}" in desc
    assert "Изменения: вертикальный кадр 9:16" in desc
    assert "авторское вступление голосом" in desc and "авторские комментарии (1)" in desc
    assert pkg.ai_disclosure_required is True  # есть синтезированные реплики
    assert pkg.originality is not None and pkg.originality.verdict.value == "pass"
    assert pkg.originality.author_share == pytest.approx(0.41)
    assert "CC BY" in (pkg.texts.audio_note or "")
    assert pkg.texts.related_video_hint  # YouTube


def test_clip_operator_voice_only_needs_no_ai_disclosure(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    job = clip_job(
        db,
        media_root,
        rights_type="own",
        voice_sources=("operator", "operator", "operator"),
        author_layer={"mode": "operator_text", "voice": "operator_recording", "captions": "both"},
    )
    run_package_task(int(job["id"]))
    pkg = operator.get(f"/jobs/{job['id']}/package").json()
    assert pkg["ai_disclosure_required"] is False
    items = {i["code"]: i for i in pkg["checklist"]}
    assert items["ai_disclosure"]["required"] is False
    # свой источник: атрибуции нет, Related video — на исходный ролик
    assert pkg["texts"]["attribution_text"] is None
    assert "Источник:" not in pkg["texts"]["description"]
    assert "abcdefghijk" in (pkg["texts"]["related_video_hint"] or "")


def test_licensed_clip_reports_content_id_allowlist(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, no_llm: None
) -> None:
    job = clip_job(db, media_root, rights_type="licensed_written", allowlist_status="requested")
    run_package_task(int(job["id"]))
    row = package_row(db, int(job["id"]))
    assert "content_id" in _checklist_codes(row)
    assert "Content ID" in row["audio_note"] and "не подтверждён" in row["audio_note"]


def test_foreign_clip_with_failed_originality_gets_no_package(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    from autoshorts.core.errors import PermanentError

    job = clip_job(db, media_root, rights_type="cc_by", verdict="fail")
    with pytest.raises(PermanentError) as ei:
        run_package_task(int(job["id"]))
    assert ei.value.error_class == "originality_fail"
    resp = operator.post(f"/jobs/{job['id']}/package", json={})
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["code"] == "package_not_ready"
    assert err["details"]["reason"] == "originality_fail" and err["details"]["fails"] == ["author_share"]
    assert package_row(db, int(job["id"])) is None


def test_rights_changed_to_foreign_after_qc_block_package_and_open_alert(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    """Регрессия: QC прошёл при своих правах (гейт не применялся), потом права поправили на CC BY —
    пакет с долей 0,07 и строкой атрибуции собирался. Теперь нужен повторный QC."""
    from autoshorts.core.errors import PermanentError

    job = clip_job(db, media_root, rights_type="own", verdict="warn", gate_applies=False)
    db.execute(
        """UPDATE source_rights SET rights_type = 'cc_by', license = 'CC BY 4.0',
                  attribution_text = '«Лекция», Иван Иванов, CC BY 4.0' WHERE source_id = %s""",
        (job["source_id"],),
    )
    with pytest.raises(PermanentError) as ei:
        run_package_task(int(job["id"]))
    assert ei.value.error_class == "originality_stale"
    assert package_row(db, int(job["id"])) is None
    alert = db.execute(
        "SELECT * FROM alerts WHERE dedup_key = %s AND status = 'open'", (f"package_blocked:{job['id']}",)
    ).fetchone()
    assert alert is not None and alert["code"] == "package_blocked"
    assert alert["details"]["reason"] == "originality_stale" and job["video_uid"] in alert["title"]
    resp = operator.post(f"/jobs/{job['id']}/package", json={})
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["reason"] == "originality_stale"
    # отчёт без записи о правах в отчёте, но снятый при другом типе прав — тоже устарел
    report = {
        "verdict": "pass",
        "summary": {"author_share": 0.41, "gate_applies": True, "rights_type": "licensed_written"},
        "fails": [],
        "rights": {"rights_type": "licensed_written"},
    }
    from psycopg.types.json import Jsonb

    db.execute(
        "UPDATE jobs SET originality = %s, originality_verdict = 'pass' WHERE id = %s",
        (Jsonb(report), job["id"]),
    )
    resp = operator.post(f"/jobs/{job['id']}/package", json={})
    assert resp.status_code == 409 and resp.json()["error"]["details"]["reason"] == "originality_stale"
    # повторный QC при текущих правах → пакет собирается, алерт закрывается
    report["summary"]["rights_type"] = "cc_by"
    report["rights"]["rights_type"] = "cc_by"
    db.execute("UPDATE jobs SET originality = %s WHERE id = %s", (Jsonb(report), job["id"]))
    run_package_task(int(job["id"]))
    assert package_row(db, int(job["id"])) is not None
    status = db.execute("SELECT status FROM alerts WHERE id = %s", (alert["id"],)).fetchone()["status"]
    assert status == "resolved"


def test_rights_change_supersedes_live_clip_packages_and_rebuilds(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    """Живой пакет собран при своих правах (без атрибуции), права поправили на CC BY — пакет устарел:
    superseded, одобренному ролику ставится новая сборка (она упрётся в originality_stale)."""
    job = clip_job(db, media_root, rights_type="own", verdict="warn", gate_applies=False)
    run_package_task(int(job["id"]))
    first = package_row(db, int(job["id"]))
    assert first is not None and first["attribution_text"] is None
    cc_by = {
        "rights_type": "cc_by",
        "owner_name": "Иван Иванов",
        "proof_note": "лицензия на странице ролика",
        "license": "CC BY 4.0",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "attribution_text": "«Лекция о картах», Иван Иванов, CC BY 4.0",
    }
    resp = operator.put(f"/sources/{job['source_id']}/rights", json=cc_by)
    assert resp.status_code == 200, resp.text
    old = db.execute("SELECT status FROM upload_packages WHERE id = %s", (first["id"],)).fetchone()
    assert old["status"] == "superseded" and package_row(db, int(job["id"])) is None
    task = db.execute(
        "SELECT * FROM tasks WHERE kind = 'package_build' AND job_id = %s AND status = 'queued'", (job["id"],)
    ).fetchone()
    assert task is not None
    # та же запись прав ещё раз — ничего не пересобирается
    db.execute("UPDATE tasks SET status = 'cancelled' WHERE id = %s", (task["id"],))
    assert operator.put(f"/sources/{job['source_id']}/rights", json=cc_by).status_code == 200
    n = db.execute(
        "SELECT count(*) AS n FROM tasks WHERE kind = 'package_build' AND job_id = %s AND status = 'queued'",
        (job["id"],),
    ).fetchone()["n"]
    assert n == 0


def test_ai_disclosure_follows_rendered_voice_not_later_recordings(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    """Финал озвучен TTS, записи оператора загружены уже на приёмке без переделки: AI-метка нужна."""
    from psycopg.types.json import Jsonb

    job = clip_job(
        db,
        media_root,
        rights_type="own",
        voice_sources=("operator", "operator", "operator"),
        author_layer={"mode": "operator_text", "voice": "operator_recording", "captions": "both"},
    )
    db.execute(
        "UPDATE jobs SET stage_payload = %s WHERE id = %s",
        (Jsonb({"clip_voice": {"kinds": ["tts"], "takes": {}}, "voice": {"kinds": ["tts"]}}), job["id"]),
    )
    run_package_task(int(job["id"]))
    pkg = operator.get(f"/jobs/{job['id']}/package").json()
    assert pkg["ai_disclosure_required"] is True
    assert {i["code"]: i for i in pkg["checklist"]}["ai_disclosure"]["required"] is True
    # и наоборот: в рендер ушли только записи оператора — метка не нужна
    db.execute(
        "UPDATE jobs SET stage_payload = %s WHERE id = %s",
        (Jsonb({"clip_voice": {"kinds": ["operator"], "takes": {}}}), job["id"]),
    )
    again = operator.post(f"/jobs/{job['id']}/package", json={}).json()
    assert again["ai_disclosure_required"] is False


def test_own_clip_failed_report_still_packages(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, no_llm: None
) -> None:
    job = clip_job(db, media_root, rights_type="own", verdict="fail", gate_applies=False)
    run_package_task(int(job["id"]))
    assert package_row(db, int(job["id"])) is not None


def test_expired_rights_are_rejected(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    job = clip_job(db, media_root, rights_type="cc_by", valid_until=days_ago(2))
    resp = operator.post(f"/jobs/{job['id']}/package", json={})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "rights_rejected"
    assert resp.json()["error"]["details"]["codes"] == ["rights_expired"]


def test_rebuild_requires_approved_job_with_final(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    from tests.api.test_jobs_support import make_job

    pending = approved_job(db, media_root, status="pending_review")
    resp = operator.post(f"/jobs/{pending['id']}/package", json={"regenerate_texts": False})
    assert resp.status_code == 409 and resp.json()["error"]["details"]["reason"] == "job_not_approved"
    no_final = make_job(db, status="approved")
    resp = operator.post(f"/jobs/{no_final['id']}/package", json={})
    assert resp.status_code == 409 and resp.json()["error"]["details"]["reason"] == "no_final"
    assert operator.get(f"/jobs/{no_final['id']}/package").status_code == 404


def test_rebuild_keeps_operator_texts_unless_regenerate(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    job = approved_job(db, media_root)
    first = operator.post(f"/jobs/{job['id']}/package", json={}).json()
    patched = operator.patch(
        f"/packages/{first['id']}", json={"title": "Мой заголовок", "hashtags": ["#мой"]}
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["texts"]["texts_mode"] == "operator"
    second = operator.post(f"/jobs/{job['id']}/package", json={"regenerate_texts": False}).json()
    assert second["id"] != first["id"]
    assert second["texts"]["title"] == "Мой заголовок" and second["texts"]["texts_mode"] == "operator"
    assert second["tracking"]["code"] == first["tracking"]["code"]
    old = operator.get(f"/packages/{first['id']}").json()
    assert old["status"] == "superseded"
    third = operator.post(f"/jobs/{job['id']}/package", json={"regenerate_texts": True}).json()
    assert third["texts"]["texts_mode"] == "template" and third["texts"]["title"].startswith("Три карты")


# ---------------------------------------------------------------- правки и чек-лист


def test_patch_texts_keeps_attribution_and_limits(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, login_as: Any, no_llm: None
) -> None:
    job = clip_job(db, media_root, rights_type="cc_by")
    pkg = operator.post(f"/jobs/{job['id']}/package", json={}).json()
    pid = pkg["id"]
    resp = operator.patch(f"/packages/{pid}", json={"description": "Без атрибуции"})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["errors"][0]["type"] == "attribution_required"
    ok = operator.patch(
        f"/packages/{pid}",
        json={"description": "Своё описание.\n\nИсточник: " + pkg["texts"]["attribution_text"]},
    )
    assert ok.status_code == 200
    assert operator.patch(f"/packages/{pid}", json={"title": "x" * 101}).status_code == 422
    assert operator.patch(f"/packages/{pid}", json={"hashtags": ["без решётки"]}).status_code == 422
    viewer = login_as("viewer")
    assert viewer.patch(f"/packages/{pid}", json={"title": "нельзя"}).status_code == 403


def test_checklist_mark_uploaded_and_list(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    job = approved_job(db, media_root)
    vk = approved_job(db, media_root, channel_id="taro_vk")
    pkg = operator.post(f"/jobs/{job['id']}/package", json={}).json()
    vk_pkg = operator.post(f"/jobs/{vk['id']}/package", json={}).json()
    assert "related_video" not in {i["code"] for i in vk_pkg["checklist"]}
    assert vk_pkg["texts"]["related_video_hint"] is None
    resp = operator.patch(f"/packages/{pkg['id']}/checklist", json={"code": "right_account", "done": True})
    assert resp.status_code == 200
    item = next(i for i in resp.json()["checklist"] if i["code"] == "right_account")
    assert item["done"] is True and item["done_at"] and item["done_by"] == operator.user["id"]
    bad = operator.patch(f"/packages/{vk_pkg['id']}/checklist", json={"code": "related_video", "done": True})
    assert bad.status_code == 422
    up = operator.post(f"/packages/{pkg['id']}/mark-uploaded")
    assert up.status_code == 200 and up.json()["status"] == "downloaded" and up.json()["marked_uploaded_at"]
    listing = operator.get("/packages", params={"status": "ready"}).json()
    assert [p["id"] for p in listing["items"]] == [vk_pkg["id"]]
    listing = operator.get("/packages", params={"platform": "youtube"}).json()
    assert listing["total"] == 1 and listing["items"][0]["id"] == pkg["id"]
    assert operator.get("/packages/999999").status_code == 404


# ---------------------------------------------------------------- файлы


def test_video_text_and_zip_downloads(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    content = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * 64
    job = approved_job(db, media_root, content=content)
    pkg = operator.post(f"/jobs/{job['id']}/package", json={}).json()
    code = pkg["tracking"]["code"]
    text = operator.get(f"/packages/{pkg['id']}/text")
    assert text.status_code == 200 and text.headers["content-type"].startswith("text/plain")
    body = text.content.decode("utf-8")
    assert pkg["texts"]["title"] in body and f"Код ролика: {code}" in body and "ЧЕК-ЛИСТ" in body
    assert "Реклама · erid" not in body  # вариант не назначен
    video = operator.get(f"/packages/{pkg['id']}/video")
    assert video.status_code == 200 and video.content == content
    disp = video.headers["content-disposition"]
    assert "attachment" in disp and re.search(
        rf'filename="taro_main_\d{{4}}-\d{{2}}-\d{{2}}_{code}\.mp4"', disp
    )
    after = operator.get(f"/packages/{pkg['id']}").json()
    assert after["status"] == "downloaded" and after["downloaded_at"]
    assert next(i for i in after["checklist"] if i["code"] == "file_downloaded")["done"] is True
    z = operator.get(f"/packages/{pkg['id']}/zip")
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(z.content))
    assert zf.testzip() is None
    names = zf.namelist()
    assert any(n.endswith(".mp4") for n in names) and any(n.endswith(".txt") for n in names)
    mp4 = next(n for n in names if n.endswith(".mp4"))
    assert zf.getinfo(mp4).compress_type == zipfile.ZIP_STORED
    assert zf.read(mp4) == content


def test_patch_texts_keeps_ad_marking_line(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    """Строку «Реклама… erid: …» (38-ФЗ) нельзя удалить из описания, как и атрибуцию."""
    job = approved_job(db, media_root, banner_variant_id=m3["variant_a"], campaign_id=m3["campaign_id"])
    pkg = operator.post(f"/jobs/{job['id']}/package", json={}).json()
    erid = f"erid: {AD_MARKING['erid']}"
    assert erid in pkg["texts"]["description"]
    resp = operator.patch(f"/packages/{pkg['id']}", json={"description": "Короткое описание без маркировки"})
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["errors"][0]["type"] == "erid_required"
    ok = operator.patch(
        f"/packages/{pkg['id']}", json={"description": f"Своё описание.\n\nРеклама. ООО «Умка». {erid}"}
    )
    assert ok.status_code == 200, ok.text
    # без маркировки в варианте — ограничения нет
    plain = approved_job(db, media_root)
    p2 = operator.post(f"/jobs/{plain['id']}/package", json={}).json()
    assert operator.patch(f"/packages/{p2['id']}", json={"description": "Просто текст"}).status_code == 200


def test_required_ad_marking_without_erid_blocks_package(
    client: Any, db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    from psycopg.types.json import Jsonb

    db.execute(
        "UPDATE banner_variants SET ad_marking = %s WHERE id = %s",
        (Jsonb({"required": True, "erid": None, "advertiser_text": "ООО «Умка»"}), m3["variant_a"]),
    )
    job = approved_job(db, media_root, banner_variant_id=m3["variant_a"], campaign_id=m3["campaign_id"])
    resp = operator.post(f"/jobs/{job['id']}/package", json={})
    assert resp.status_code == 409 and resp.json()["error"]["details"]["reason"] == "erid_missing"


# ---------------------------------------------------------------- бюджет LLM (без БД)


class _Gate:
    def __init__(self, paused: bool = False) -> None:
        self.paused = paused
        self.reserved: list[float] = []
        self.settled: list[tuple[float, dict[str, Any]]] = []

    def reserve(self, amount: float, *, provider: str) -> object:
        from autoshorts.core.errors import ResourceDeferred

        if self.paused:
            raise ResourceDeferred("budget_exhausted")
        self.reserved.append(amount)
        return object()

    def settle(self, reservation: object, actual: float, **event: Any) -> None:
        self.settled.append((actual, event))


def _inputs() -> Any:
    from autoshorts.services.packages import PackageInputs

    job = {
        "id": 1,
        "video_uid": "a1b2c3d4e5f6",
        "kind": "generate",
        "platform": "youtube",
        "niche_id": "taro",
        "topic": "отношения",
        "stage_payload": {"script": {"hook_text": "Хук", "body_text": "Тело."}},
    }
    return PackageInputs(job, "umka", None, None, None, None, None, None)


def _paid_client(reply: dict[str, Any]) -> Any:
    from autoshorts.clipping.llm_client import LLMClient, MockTransport

    return LLMClient(
        provider="openai_compat", model="m1", transport=MockTransport(lambda m, mode: json.dumps(reply))
    )


def test_llm_texts_go_through_budget() -> None:
    from autoshorts.services.packages import LLM_ESTIMATE_USD, LLM_STEP, draft_texts

    gate = _Gate()
    draft = draft_texts(
        _inputs(),
        regenerate=True,
        budget=gate,
        client_factory=lambda: _paid_client({"title": "T", "description": "D."}),
    )
    assert draft.mode == "llm" and draft.title == "T" and draft.model == "m1"
    assert gate.reserved == [LLM_ESTIMATE_USD]
    [(cost, event)] = gate.settled
    assert event["step_name"] == LLM_STEP and event["status"] == "ok" and cost >= 0


def test_paused_budget_falls_back_to_template() -> None:
    from autoshorts.services.packages import draft_texts

    draft = draft_texts(
        _inputs(),
        regenerate=True,
        budget=_Gate(paused=True),
        client_factory=lambda: _paid_client({"title": "T"}),
    )
    assert draft.mode == "template" and draft.note == "budget_paused" and draft.title == "Хук"


def test_broken_llm_reply_is_settled_as_error_and_templated() -> None:
    from autoshorts.services.packages import draft_texts

    gate = _Gate()
    draft = draft_texts(
        _inputs(), regenerate=True, budget=gate, client_factory=lambda: _paid_client({"x": 1})
    )
    assert draft.mode == "template" and draft.note == "llm_failed"
    assert gate.settled and gate.settled[0][1]["status"] == "error"


def test_cancelled_job_leaves_upload_list_even_if_package_was_downloaded(
    db: Any, m3: dict[str, Any], media_root: Path, operator: Any, no_llm: None
) -> None:
    """Скачанный пакет отменённого ролика («ждёт ссылку») уходит из «К заливке»: иначе старую версию
    принимают за актуальную."""
    job = approved_job(
        db, media_root, channel_id="taro_main", niche_id="taro", banner_variant_id=m3["variant_a"]
    )
    run_package_task(job["id"])
    pkg = package_row(db, job["id"])
    db.execute(
        "UPDATE upload_packages SET status = 'downloaded', downloaded_at = now() WHERE id = %s", (pkg["id"],)
    )
    r = operator.post(f"/jobs/{job['id']}/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    st = db.execute("SELECT status FROM upload_packages WHERE id = %s", (pkg["id"],)).fetchone()["status"]
    assert st == "superseded"

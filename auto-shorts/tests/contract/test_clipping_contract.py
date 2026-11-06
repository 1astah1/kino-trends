"""Контракт нарезки M5 (поток M5M3-0): API-схемы зеркалят движок `clipping/**`, а не выдумывают формы.

Сверяется: права источника (та же `validate_rights`, CC BY-SA/NC/ND и без атрибуции — отказ), опции
отбора (умолчания `SelectionConfig`), кандидат (`ClipCandidate.to_dict()`), авторский сценарий
(`AuthorScript`, лимиты), режимы (отбор, текст, голос), отчёт об оригинальности
(`OriginalityReport.to_dict()`), транскрипт, peaks и раскадровка подготовленного демо-источника,
tus `Upload-Metadata`, таблица эндпоинтов нарезки.
"""

from __future__ import annotations

import dataclasses
import json
import typing
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from pydantic import BaseModel, ValidationError

from autoshorts.api.schemas import clipping as C
from autoshorts.api.schemas import sources as S
from autoshorts.api.schemas import transcripts as T
from autoshorts.api.schemas import uploads as U
from autoshorts.api.schemas.jobs import JobCreate
from autoshorts.clipping import originality as O
from autoshorts.clipping import schemas as engine_schemas
from autoshorts.clipping import scoring, selection
from autoshorts.clipping.author import script as engine_script
from autoshorts.clipping.author import voice as engine_voice
from autoshorts.clipping.author.share import TextEvent, VoiceEvent, compute_author_share
from autoshorts.clipping.transcript import Transcript, Word

ROOT = Path(__file__).resolve().parents[2]
DEMO_SOURCE = ROOT / "var" / "media" / "sources" / "cc_shalyopa_cgevent"
DEMO_LICENSE = ROOT / "var" / "samples" / "clip_demo" / "source_license.json"
UPLOAD_ID = "0123456789abcdef0123456789abcdef"

OWN = {"rights_type": "own", "owner_name": "Umka", "proof_note": "свой эфир 12.09"}
CC_BY = {
    "rights_type": "cc_by",
    "owner": "Киностудия",
    "proof_note": "лицензия на странице ролика, 27.09.2026",
    "attribution_text": "«Выступление» — Киностудия, https://youtu.be/x, CC BY 3.0",
    "license": "CC BY 3.0",
    "license_url": "https://creativecommons.org/licenses/by/3.0/",
}


def _literal_args(tp: Any) -> set[str]:
    return set(typing.get_args(tp))


def _error_types(exc: ValidationError) -> set[str]:
    return {e["type"] for e in exc.errors()}


# ---------------------------------------------------------------- права


def test_rights_own_minimal_and_aliases() -> None:
    r = S.SourceRightsIn.model_validate(OWN)
    assert r.rights_type == "own" and r.owner_name == "Umka"
    lic = S.SourceRightsIn.model_validate(
        {
            "rights_type": "licensed",
            "owner": "Иван",
            "proof_note": "договор 12/09",
            "attribution_text": "Иван, youtube.com/@ivan",
        }
    )
    assert lic.rights_type == "licensed_written" and lic.owner_name == "Иван"
    rec, warnings = lic.validate_with_engine()
    assert rec.rights_type == "licensed_written"
    assert "content_id_allowlist_not_confirmed" in warnings and "author_share_gate_applies" in warnings


def test_rights_cc_by_ok_demo_license() -> None:
    r = S.SourceRightsIn.model_validate(CC_BY)
    _rec, warnings = r.validate_with_engine()
    assert "cc_by_attribution_in_description" in warnings
    if DEMO_LICENSE.exists():
        lic = json.loads(DEMO_LICENSE.read_text(encoding="utf-8"))
        demo = S.SourceRightsIn.model_validate(
            {
                "rights_type": "cc_by",
                "owner_name": lic["youtube_channel"],
                "proof_note": lic["youtube_license_how_verified"][:4000],
                "owner_channel_url": lic["youtube_channel_url"],
                "attribution_text": lic["attribution_text"],
                "license": lic["license"],
                "license_url": lic["license_url"],
            }
        )
        assert demo.license == "CC BY 3.0"


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"attribution_text": None}, "attribution_required"),
        ({"license": "CC BY-SA 4.0"}, "license_incompatible"),
        ({"license": "CC BY-NC 4.0"}, "license_incompatible"),
        ({"license": "CC BY-ND 4.0"}, "license_incompatible"),
        ({"license_url": "https://creativecommons.org/licenses/by-nc-sa/4.0/"}, "license_incompatible"),
        ({"valid_until": "2020-01-01"}, "rights_expired"),
    ],
)
def test_rights_rejected_by_engine_rules(patch: dict[str, Any], code: str) -> None:
    data = {**CC_BY, **patch}
    with pytest.raises(ValidationError) as exc:
        S.SourceRightsIn.model_validate(data)
    assert "rights_rejected" in _error_types(exc.value)
    assert code in str(exc.value)


def test_licensed_without_attribution_rejected() -> None:
    with pytest.raises(ValidationError) as exc:
        S.SourceRightsIn.model_validate({**OWN, "rights_type": "licensed_platform"})
    assert "rights_rejected" in _error_types(exc.value)


def test_source_create_requires_rights() -> None:
    with pytest.raises(ValidationError) as exc:
        S.SourceFromUpload.model_validate({"upload_id": UPLOAD_ID, "title": "Эфир"})
    assert any(e["loc"] == ("rights",) and e["type"] == "missing" for e in exc.value.errors())
    with pytest.raises(ValidationError):
        S.SourceFromUrl.model_validate({"url": "https://youtu.be/abc", "rights": {}})
    ok = S.SourceFromUrl.model_validate({"url": "https://youtu.be/abc", "rights": OWN})
    assert ok.language == "ru" and ok.options is None
    with pytest.raises(ValidationError):  # неизвестный ключ — не молча
        S.SourceFromUpload.model_validate({"upload_id": UPLOAD_ID, "title": "x", "rights": OWN, "extra": 1})


def test_rights_warning_labels_cover_engine() -> None:
    assert set(O._RIGHTS_WARNING_RU) <= set(S.RIGHTS_WARNING_LABELS)


def test_rights_fields_mirror_engine_record() -> None:
    from autoshorts.clipping.source_ingest import RightsRecord

    api = set(S.SourceRightsIn.model_fields) - {"proof_upload_id"}
    engine = set(RightsRecord.model_fields) - {"proof_file_path"}
    assert api == engine


# ---------------------------------------------------------------- опции отбора


def test_source_options_defaults_match_selection_config() -> None:
    opts = S.SourceOptions()
    cfg = selection.SelectionConfig()
    for key in (
        "target_clip_sec",
        "min_clip_sec",
        "max_clip_sec",
        "max_candidates",
        "target_count",
        "detail_pass",
        "tighten_pauses",
    ):
        assert getattr(opts, key) == getattr(cfg, key), key
    cfg2 = selection.SelectionConfig.from_options(opts.model_dump(mode="json"))
    assert cfg2.max_candidates == opts.max_candidates
    with pytest.raises(ValidationError):
        S.SourceOptions(target_clip_sec=40, max_clip_sec=30)


def test_selection_modes_match_engine() -> None:
    assert {m.value for m in S.SelectionMode} == _literal_args(selection.SelectMode)
    assert {m.value for m in C.CandidateOrigin} == _literal_args(scoring.Origin)
    assert set(S.SELECTION_MODE_LABELS) == {m.value for m in S.SelectionMode}
    assert set(C.CANDIDATE_ORIGIN_LABELS) == {m.value for m in C.CandidateOrigin}


# ---------------------------------------------------------------- кандидаты


def test_candidate_flags_match_engine() -> None:
    engine = {f.value for f in engine_schemas.ContentFlag} | {engine_schemas.FORBIDDEN_PHRASE_FLAG}
    assert {f.value for f in C.CandidateFlag} == engine
    assert C.BLOCKING_CANDIDATE_FLAGS == {str(f) for f in engine_schemas.BLOCKING_FLAGS} | {
        engine_schemas.FORBIDDEN_PHRASE_FLAG
    }


def test_engine_candidate_maps_onto_api_candidate() -> None:
    cand = scoring.ClipCandidate(
        start_sid=3,
        end_sid=9,
        hook_sid=3,
        start_sec=12.3,
        end_sec=41.9,
        title="Как начинался фильм",
        hook_text="А вы знали?",
        hook_type="question",
        reason="Законченная мысль",
        llm_scores={"hook": 8, "standalone": 7, "payoff": 7, "emotion": 6, "niche_fit": 8, "novelty": 5},
        content_flags=["guarantee_claim"],
        origin="heuristic",
        features={"speech_rate_wps": 2.8, "dead_air_ratio": 0.04, "first_word_latency": 0.1},
        final_score=0.71,
        score_parts={"llm": 0.7, "feat": 0.8, "len_fit": 0.9, "trend": 0.5},
        forbidden_hits=[{"phrase": "гарантирую", "start": 3, "end": 13}],
    )
    d = cand.to_dict()
    row = {
        **d,
        "id": 1,
        "source_id": 5,
        "start_word_idx": 40,
        "end_word_idx": 120,
        "snap": {
            "w0": 40,
            "w1": 120,
            "lead_sec": 0.2,
            "tail_sec": 0.3,
            "raw_start_sec": 12.5,
            "raw_end_sec": 41.6,
        },
        "score_parts": d["features"]["score_parts"],
        "forbidden_hits": d["features"]["forbidden_hits"],
        "unblocked_flags": [],
        "origin_label": C.CANDIDATE_ORIGIN_LABELS[d["origin"]],
        "notes": None,
        "job_id": None,
        "superseded": False,
        "created_at": "2026-09-27T10:00:00Z",
        "updated_at": "2026-09-27T10:00:00Z",
    }
    api = C.ClipCandidate.model_validate(row)
    assert api.blocked is True and api.block_reasons == [C.CandidateFlag.GUARANTEE_CLAIM]
    assert api.origin == C.CandidateOrigin.HEURISTIC and api.llm_scores is not None
    missing = set(d) - set(C.ClipCandidate.model_fields)
    assert not missing, f"поля движка без места в API: {missing}"


def test_candidate_patch_rules() -> None:
    p = C.ClipCandidatePatch(start_word_idx=10, end_word_idx=42)
    assert p.snap is True
    for bad in (
        {"start_word_idx": 10, "start_sec": 3.0},
        {"start_word_idx": 50, "end_word_idx": 10},
        {"start_sec": 5.0, "end_sec": 5.0},
        {"status": "used"},
        {"unblock_flags": ["медицина"]},
    ):
        with pytest.raises(ValidationError):
            C.ClipCandidatePatch.model_validate(bad)
    with pytest.raises(ValidationError):
        C.ClipCandidateCreate.model_validate({"start_sec": 1.0})
    assert C.ClipCandidateCreate.model_validate({"start_word_idx": 0, "end_sec": 30.0}).snap is True


# ---------------------------------------------------------------- авторский слой


def _nested_limits(model: type[BaseModel]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        out[name] = next((m.max_length for m in field.metadata if hasattr(m, "max_length")), None)
    return out


def test_author_script_body_mirrors_engine_model() -> None:
    pairs = [
        (C.AuthorColdOpen, engine_script.ColdOpen),
        (C.AuthorInsert, engine_script.Insert),
        (C.AuthorOutro, engine_script.Outro),
        (C.AuthorScriptBody, engine_script.AuthorScript),
    ]
    for api, engine in pairs:
        assert set(api.model_fields) == set(engine.model_fields), api.__name__
        api_limits = {k: v for k, v in _nested_limits(api).items() if v is not None}
        engine_limits = {k: v for k, v in _nested_limits(engine).items() if v is not None}
        engine_limits.pop("inserts", None)
        api_limits.pop("inserts", None)
        assert api_limits == engine_limits, api.__name__
    assert C.AuthorScriptBody.model_fields["inserts"].metadata[0].max_length == engine_script.MAX_INSERTS
    assert {m.value for m in C.AuthorInsertMode} == _literal_args(engine_script.InsertMode)
    assert {m.value for m in C.AuthorCtaStyle} == _literal_args(engine_script.CtaStyle)


def test_engine_script_roundtrips_through_api() -> None:
    draft = engine_script.draft_script_heuristic(
        [(10, "Первое предложение про фильм."), (11, "Второе, где главный поворот."), (12, "Финал.")],
        title="Как начинался фильм",
        owner="Umka",
    )
    body = C.AuthorScriptBody.model_validate(draft.model_dump())
    again = engine_script.AuthorScript.model_validate(body.model_dump(mode="json"))
    assert again.model_dump() == draft.model_dump()
    with pytest.raises(ValidationError):
        C.AuthorScriptBody.model_validate({"cold_open": {"vo_text": "х" * (engine_script.COLD_OPEN_MAX + 1)}})
    with pytest.raises(ValidationError):
        C.AuthorScriptBody.model_validate(
            {"inserts": [{"after_sid": 3, "vo_text": "a"}, {"after_sid": 3, "vo_text": "b"}]}
        )


def test_author_modes_match_engine() -> None:
    hints = typing.get_type_hints(engine_script.AuthorScriptResult)
    assert {m.value for m in C.AuthorScriptMode} == _literal_args(hints["mode"])
    issue_hints = typing.get_type_hints(engine_script.ScriptIssue)
    assert {m.value for m in C.AuthorScriptIssueSeverity} == _literal_args(issue_hints["severity"])
    assert {m.value for m in C.AuthorVoiceKind} == _literal_args(engine_voice.VoiceKind)
    take_hints = typing.get_type_hints(engine_voice.VoiceTake)
    assert {m.value for m in C.AuthorVoiceTiming} == _literal_args(take_hints["timing"])


def test_engine_result_fills_job_author_script() -> None:
    tr = _transcript()
    res = engine_script.generate_author_script(tr, 0, 3, llm=None, title="Хук")
    d = res.to_dict()
    api = C.JobAuthorScript.model_validate(
        {
            **d,
            "job_id": 7,
            "llm_configured": False,
            "version": 1,
            "drafting_task_id": None,
            "editable": True,
            "updated_at": None,
            "updated_by": None,
        }
    )
    assert api.mode == C.AuthorScriptMode.HEURISTIC and api.needs_operator_edit is True
    assert any(i.code == "template_text" for i in api.issues)
    assert set(d) - {"events", "cost_usd", "latency_ms"} <= set(C.JobAuthorScript.model_fields)


def test_job_create_clip_takes_typed_author_script() -> None:
    body = {
        "kind": "clip",
        "channel_id": "astro_main",
        "clip_candidate_id": 88,
        "author_layer": {
            "mode": "operator_text",
            "voice": "operator_recording",
            "script": {
                "cold_open": {"vo_text": "Мой хук", "on_screen": "Тезис"},
                "inserts": [{"after_sid": 5, "mode": "freeze_zoom", "vo_text": "Комментарий"}],
                "outro": {"vo_text": "А вы как думаете?", "cta_style": "question"},
                "on_screen_title": "Свой заголовок",
            },
        },
    }
    job = JobCreate.model_validate(body).root
    assert job.author_layer is not None and job.author_layer.script is not None
    assert job.author_layer.model_dump(mode="json")["script"]["inserts"][0]["after_sid"] == 5
    bad = json.loads(json.dumps(body))
    bad["author_layer"]["script"]["on_screen_title"] = "х" * 49
    with pytest.raises(ValidationError):
        JobCreate.model_validate(bad)


# ---------------------------------------------------------------- оригинальность


def _transcript() -> Transcript:
    words: list[Word] = []
    t = 1.0
    for i in range(10):
        for j in range(6):
            words.append(Word(f"сл{i}{j}" + ("." if j == 5 else ""), round(t, 3), round(t + 0.4, 3)))
            t += 0.5
        t += 0.6
    return Transcript.from_words(words, language="ru", duration=t + 2, provider="test")


def _report() -> O.OriginalityReport:
    share = compute_author_share(
        40.0,
        freeze_windows=[(10.0, 14.0)],
        voice=[VoiceEvent(0.1, 6.0, "cold_open", 16.0), VoiceEvent(10.1, 13.8, "insert", 60, True)],
        texts=[TextEvent(0, 40, "title")],
    )
    return build_report(share)


def build_report(share: Any) -> O.OriginalityReport:
    return O.build_originality_report(
        share=share,
        rights={**CC_BY, "owner_name": "Киностудия", "allowlist_status": "unknown"},
        rights_warnings=["cc_by_attribution_in_description", "author_share_gate_applies"],
        watermark={"suspect": False, "severity": "ok"},
        clip_sids=[3, 4, 5],
        previous_clips=[{"id": 7, "sids": [4, 5, 6]}],
        author_mode="llm",
        voice_kinds=["tts"],
        today=date(2026, 9, 27),
    )


def _api_report(rep: O.OriginalityReport, stage: str = "final") -> C.OriginalityReportOut:
    d = rep.to_dict()
    checks = [{**c, "title": O.CHECK_TITLES_RU[c["code"]]} for c in d["checks"]]
    return C.OriginalityReportOut.model_validate(
        {
            **d,
            "checks": checks,
            "stage": stage,
            "verdict_label": O.VERDICT_RU[d["verdict"]],
            "computed_at": "2026-09-27T10:00:00Z",
        }
    )


def test_originality_enums_match_engine() -> None:
    assert {c.value for c in C.OriginalityCheckCode} == set(O.CHECK_TITLES_RU)
    assert {s.value for s in C.OriginalityCheckStatus} == _literal_args(O.Status)
    assert {v.value for v in C.OriginalityVerdict} == set(O.VERDICT_RU)


def test_engine_report_validates_as_api_report() -> None:
    rep = _report()
    api = _api_report(rep)
    d = rep.to_dict()
    assert api.verdict.value == rep.verdict and api.summary.gate_applies is True
    assert set(d) <= set(C.OriginalityReportOut.model_fields)
    assert set(d["summary"]) == set(C.OriginalitySummary.model_fields)
    assert set(d["author_share"]) == set(C.AuthorShareBreakdown.model_fields)
    assert set(dataclasses.asdict(rep.checks[0])) | {"title"} == set(C.OriginalityCheckOut.model_fields)
    low = build_report(compute_author_share(40.0, voice=[VoiceEvent(0.1, 3.0, "cold_open", 16.0)]))
    low_api = _api_report(low, "estimate")
    assert low_api.verdict == C.OriginalityVerdict.FAIL
    assert C.OriginalityCheckCode.AUTHOR_SHARE in low_api.fails and low_api.suggestions


# ---------------------------------------------------------------- транскрипт и медиа демо-источника


def test_engine_transcript_validates_as_api_transcript() -> None:
    tr = _transcript()
    d = tr.to_dict()
    api = T.SourceTranscript.model_validate(
        {
            "source_id": 1,
            "language": d["language"],
            "duration_sec": d["duration"],
            "provider_key": d["provider"],
            "model": d["model"],
            "words": d["words"],
            "sentences": d["sentences"],
        }
    )
    assert api.words[0].sid == 0 and api.sentences[-1].w1 == len(api.words) - 1
    assert set(d["words"][0]) <= set(T.TranscriptWord.model_fields)
    assert set(d["sentences"][0]) == set(T.TranscriptSentence.model_fields)


@pytest.mark.skipif(not (DEMO_SOURCE / "source.json").exists(), reason="демо-источник не подготовлен")
def test_demo_source_media_match_schemas() -> None:
    manifest = json.loads((DEMO_SOURCE / "source.json").read_text(encoding="utf-8"))
    sb = manifest["storyboard"]
    S.SourceStoryboard.model_validate(
        {**sb, "sheets": [f"/api/v1/sources/1/storyboard/{i}" for i, _ in enumerate(sb["sheets"])]}
    )
    peaks = json.loads((DEMO_SOURCE / "peaks.json").read_text(encoding="utf-8"))
    assert S.SourcePeaks.model_validate(peaks).format == "wavesurfer"
    S.SourceWatermark.model_validate(
        {
            "suspect": manifest["watermark"]["suspect"],
            "severity": manifest["watermark"]["severity"],
            "detail": None,
            "frames_checked": manifest["watermark"]["frames_checked"],
            "logos_checked": manifest["watermark"]["logos_checked"],
        }
    )
    tmeta = manifest["transcript"]
    assert {"provider", "model", "language", "words", "sentences", "rtfx"} <= set(tmeta)
    rights = S.SourceRightsIn.model_validate(
        {k: v for k, v in manifest["rights"].items() if k != "proof_file_path"}
    )
    assert rights.rights_type == "cc_by"


# ---------------------------------------------------------------- tus


def test_tus_constants_and_metadata_roundtrip() -> None:
    assert U.TUS_VERSION == "1.0.0" and U.TUS_CHUNK_MAX_BYTES == 16 * 1024 * 1024
    meta = {"filename": "Эфир 12.09.mp4", "filetype": "video/mp4", "purpose": "source", "empty": ""}
    header = U.encode_upload_metadata(meta)
    assert header.isascii()
    assert U.decode_upload_metadata(header) == meta
    assert U.decode_upload_metadata(None) == {}
    for bad in ("filename !!!", "a b c", "filename ZmlsZQ==,filename ZmlsZQ==", "имя ZmlsZQ=="):
        with pytest.raises(ValueError):
            U.decode_upload_metadata(bad)


# ---------------------------------------------------------------- эндпоинты нарезки


CLIPPING_ENDPOINTS = {
    ("OPTIONS", "/api/v1/uploads/tus"): "uploads_tus_options",
    ("POST", "/api/v1/uploads/tus"): "uploads_tus_create",
    ("HEAD", "/api/v1/uploads/tus/{upload_id}"): "uploads_tus_head",
    ("PATCH", "/api/v1/uploads/tus/{upload_id}"): "uploads_tus_patch",
    ("DELETE", "/api/v1/uploads/tus/{upload_id}"): "uploads_tus_terminate",
    ("GET", "/api/v1/uploads/{upload_id}"): "uploads_get",
    ("POST", "/api/v1/sources/from-upload"): "sources_create_from_upload",
    ("POST", "/api/v1/sources/from-url"): "sources_create_from_url",
    ("GET", "/api/v1/sources"): "sources_list",
    ("GET", "/api/v1/sources/{source_id}"): "sources_get",
    ("PATCH", "/api/v1/sources/{source_id}"): "sources_update",
    ("DELETE", "/api/v1/sources/{source_id}"): "sources_delete",
    ("GET", "/api/v1/sources/{source_id}/rights"): "sources_rights_get",
    ("PUT", "/api/v1/sources/{source_id}/rights"): "sources_rights_put",
    ("POST", "/api/v1/sources/{source_id}/reprocess"): "sources_reprocess",
    ("GET", "/api/v1/sources/{source_id}/transcript"): "sources_transcript",
    ("GET", "/api/v1/sources/{source_id}/media/proxy"): "sources_media_proxy",
    ("GET", "/api/v1/sources/{source_id}/media/audio"): "sources_media_audio",
    ("GET", "/api/v1/sources/{source_id}/peaks"): "sources_peaks",
    ("GET", "/api/v1/sources/{source_id}/storyboard"): "sources_storyboard",
    ("GET", "/api/v1/sources/{source_id}/storyboard/{sheet_idx}"): "sources_storyboard_sheet",
    ("GET", "/api/v1/sources/{source_id}/scenes"): "sources_scenes",
    ("GET", "/api/v1/sources/{source_id}/candidates"): "clipping_candidates_list",
    ("POST", "/api/v1/sources/{source_id}/candidates"): "clipping_candidates_create",
    ("POST", "/api/v1/sources/{source_id}/select"): "clipping_select",
    ("POST", "/api/v1/sources/{source_id}/clips"): "clipping_clips_batch",
    ("GET", "/api/v1/clip-candidates/{candidate_id}"): "clipping_candidate_get",
    ("PATCH", "/api/v1/clip-candidates/{candidate_id}"): "clipping_candidate_update",
    ("POST", "/api/v1/clip-candidates/{candidate_id}/jobs"): "clipping_candidate_create_job",
    (
        "POST",
        "/api/v1/clip-candidates/{candidate_id}/originality-estimate",
    ): "clipping_candidate_originality_estimate",
    ("GET", "/api/v1/jobs/{job_id}/author-script"): "clipping_author_script_get",
    ("PUT", "/api/v1/jobs/{job_id}/author-script"): "clipping_author_script_put",
    ("POST", "/api/v1/jobs/{job_id}/author-script/draft"): "clipping_author_script_draft",
    ("GET", "/api/v1/jobs/{job_id}/author-voice"): "clipping_author_voice_get",
    ("POST", "/api/v1/jobs/{job_id}/author-voice"): "clipping_author_voice_upload",
    ("GET", "/api/v1/jobs/{job_id}/author-voice/{segment}/audio"): "clipping_author_voice_audio",
    ("DELETE", "/api/v1/jobs/{job_id}/author-voice/{segment}"): "clipping_author_voice_delete",
    ("POST", "/api/v1/jobs/{job_id}/originality/estimate"): "clipping_originality_estimate",
    ("GET", "/api/v1/jobs/{job_id}/originality"): "clipping_originality_get",
}


def test_clipping_endpoints_present(openapi_spec: dict[str, Any]) -> None:
    ops = {
        (method.upper(), path): op["operationId"]
        for path, item in openapi_spec["paths"].items()
        for method, op in item.items()
    }
    missing = {k: v for k, v in CLIPPING_ENDPOINTS.items() if ops.get(k) != v}
    assert not missing, missing


def test_clip_fields_in_job_and_review(app: FastAPI, openapi_spec: dict[str, Any]) -> None:
    schemas = openapi_spec["components"]["schemas"]
    job = schemas["Job"]["properties"]
    assert {"originality", "author_script"} <= set(job) and "originality" not in schemas["Job"]["required"]
    assert "originality_verdict" in schemas["JobRow"]["properties"]
    ctx = schemas["ReviewContext"]
    assert {"source", "author_script", "originality"} <= set(ctx["properties"])
    assert not {"source", "author_script", "originality"} & set(ctx["required"])
    assert "originality_verdict" in schemas["ReviewQueueItem"]["properties"]
    # тело голоса оператора — multipart с файлом и сегментом
    body = openapi_spec["paths"]["/api/v1/jobs/{job_id}/author-voice"]["post"]["requestBody"]
    assert "multipart/form-data" in body["content"]
    # tus PATCH — поток байт, не JSON
    patch = openapi_spec["paths"]["/api/v1/uploads/tus/{upload_id}"]["patch"]["requestBody"]
    assert list(patch["content"]) == [U.TUS_CONTENT_TYPE]

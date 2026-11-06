"""Стадии clip-job и сервис нарезки без БД и ffmpeg (M5-B): гейт QC по отчёту об оригинальности,
план рефрейма между стадиями, снаппинг правок кандидата, реплики, бюджет платных вызовов, подписи.

Критерий §10.2 M5: лицензированный источник с author_share 0,2 → qc_failed + author_share; свой
источник с 0,2 → проходит с пометкой.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from autoshorts.clipping.author.script import AuthorScript, AuthorScriptResult
from autoshorts.clipping.author.share import AuthorShare
from autoshorts.clipping.originality import build_originality_report
from autoshorts.clipping.qc_clip import QCCheck, QCReport
from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.core.errors import ValidationFailed
from autoshorts.services import clipping as svc
from autoshorts.stages.clip_qc import ORIGINALITY_QC_CODE, qc_outcome

LICENSED = {
    "rights_type": "licensed_written",
    "owner_name": "Test Owner",
    "attribution_text": "Test Owner, youtube.com/@test",
    "allowlist_status": "allowlisted",
}
OWN = {"rights_type": "own", "owner_name": "Umka"}


def _share(value: float, duration: float = 30.0) -> AuthorShare:
    author_only = value * duration
    return AuthorShare(
        value=value,
        duration=duration,
        author_only_sec=author_only,
        voice_over_sec=0.0,
        text_sec=0.0,
        own_voice_sec=author_only,
        own_text_sec=0.0,
        freeze_sec=0.0,
    )


def _author(mode: str = "operator") -> AuthorScriptResult:
    script = AuthorScript.model_validate(
        {
            "cold_open": {"vo_text": "Одно правило решает, досмотрят ли ролик."},
            "on_screen_title": "Три секунды",
        }
    )
    return AuthorScriptResult(script, mode, "текст оператора")  # type: ignore[arg-type]


def _report(value: float, rights: dict[str, Any]) -> Any:
    return build_originality_report(
        share=_share(value),
        rights=rights,
        watermark={"suspect": False, "severity": "ok"},
        author_mode="operator",
        operator_edited=True,
        voice_kinds=["tts"],
    )


def _qc(extra: list[QCCheck]) -> QCReport:
    media = [QCCheck("duration", True, "30.0 с", "20–58 с"), QCCheck("loudness", True, "-14.1", "-14±1.5")]
    checks = media + extra
    return QCReport(all(c.ok or c.severity != "fail" for c in checks), checks)


def _gate_checks(report: Any, value: float) -> list[QCCheck]:
    from autoshorts.clipping.produce import originality_qc_checks

    return originality_qc_checks(report, _share(value), _author(), min_share=0.30)


# ---------------------------------------------------------------- гейт QC


def test_licensed_share_02_fails_qc_with_author_share() -> None:
    report = _report(0.2, LICENSED)
    assert report.summary["gate_applies"] is True
    assert report.check("author_share").status == "fail"
    qc = _qc(_gate_checks(report, 0.2))
    failed, warnings = qc_outcome(report, qc, gate_applies=True, author_blocked=False, min_share=0.30)
    assert "author_share" in failed
    assert not any(w["code"] == "author_share" for w in warnings)


def test_own_share_02_passes_with_note() -> None:
    report = _report(0.2, OWN)
    assert report.summary["gate_applies"] is False
    assert report.check("author_share").status == "info"
    qc = _qc(_gate_checks(report, 0.2))
    failed, warnings = qc_outcome(report, qc, gate_applies=False, author_blocked=False, min_share=0.30)
    assert failed == []
    note = next(w for w in warnings if w["code"] == "author_share")
    assert "0.20" in note["detail"] and "гейт не применяется" in note["detail"]


def test_licensed_share_above_threshold_passes() -> None:
    report = _report(0.45, LICENSED)
    qc = _qc(_gate_checks(report, 0.45))
    failed, _ = qc_outcome(report, qc, gate_applies=True, author_blocked=False, min_share=0.30)
    assert failed == []


def test_non_own_originality_fail_without_share_problem_maps_to_codes() -> None:
    report = build_originality_report(
        share=_share(0.5),
        rights=LICENSED,
        watermark={"suspect": False, "severity": "ok"},
        edl=None,
        clip_sids=[3, 4, 5],
        previous_clips=[{"id": "job 1", "sids": [3, 4, 5]}],
        author_mode="mock",
        voice_kinds=["mock"],
    )
    assert report.verdict == "fail"
    assert {"repeat_moment", "author_text"} <= set(report.fails)
    qc = _qc(_gate_checks(report, 0.5))
    failed, _ = qc_outcome(report, qc, gate_applies=True, author_blocked=False, min_share=0.30)
    assert "library_similarity" in failed  # тот же момент уже нарезан
    assert ORIGINALITY_QC_CODE in failed  # заглушка вместо авторского текста
    # свой источник: те же провалы — только пометки
    own = build_originality_report(
        share=_share(0.5), rights=OWN, clip_sids=[3], previous_clips=[{"id": "x", "sids": [3]}],
        author_mode="mock", voice_kinds=["mock"],
    )  # fmt: skip
    failed_own, warns_own = qc_outcome(own, _qc([]), gate_applies=False, author_blocked=False, min_share=0.3)
    assert failed_own == [] and any("стоп" in w["detail"] for w in warns_own)


def test_media_failure_always_fails() -> None:
    report = _report(0.5, OWN)
    qc = _qc([QCCheck("silence", False, "1 пауз", "нет тишины ≥ 4 с")])
    failed, _ = qc_outcome(report, qc, gate_applies=False, author_blocked=False, min_share=0.30)
    assert failed == ["silence"]


# ---------------------------------------------------------------- план рефрейма между стадиями


def test_reframe_plan_json_roundtrip_keeps_tracking_points() -> None:
    from autoshorts.clipping.reframe import build_plan
    from autoshorts.clipping.reframe.strategy import CropGeometry

    @dataclass
    class Face:
        cx: float
        x: float
        w: float
        area: float

    @dataclass
    class Sample:
        t: float
        faces: list[Face]

    geo = CropGeometry.for_source(1920, 1080)
    samples = [Sample(10 + i * 0.1, [Face(0.3 + i * 0.01, 0.25 + i * 0.01, 0.1, 0.02)]) for i in range(40)]
    plan = build_plan(geo, 10.0, 14.0, face_samples=samples, scene_cuts=[12.0], mode="auto")
    data = svc.reframe_plan_to_json(plan)
    back = svc.reframe_plan_from_json(data)
    assert back.geometry == plan.geometry
    assert [s.mode for s in back.shots] == [s.mode for s in plan.shots]
    assert back.scene_cuts == plan.scene_cuts
    for t in (10.0, 11.3, 12.5, 13.9):
        assert back.x_at(t) == plan.x_at(t)
    assert back.sendcmd_for(10.0, 12.0, target="crop@c0") == plan.sendcmd_for(10.0, 12.0, target="crop@c0")


# ---------------------------------------------------------------- снаппинг правок кандидата


def _transcript() -> Transcript:
    text = "Первое слово тут. Второе предложение длиннее обычного. Третье короткое! Четвёртое финальное."
    words, t = [], 1.0
    for tok in text.split():
        d = 0.08 * len(tok) + 0.1
        words.append(Word(tok, round(t, 3), round(t + d, 3)))
        t += d + (0.6 if tok.endswith((".", "!")) else 0.08)
    return Transcript.from_words(words, language="ru", duration=t + 1.0, provider="mock")


def test_resolve_bounds_by_word_index_keeps_clicked_words() -> None:
    tr = _transcript()
    b = svc.resolve_bounds(tr, start_word_idx=3, end_word_idx=9, start_sec=None, end_sec=None, snap=True)
    assert (b.w0, b.w1) == (3, 9)
    assert b.start <= tr.words[3].s and b.start >= tr.words[2].e  # разрез в тишине перед словом
    assert b.end >= tr.words[9].e and b.end <= tr.words[10].s
    assert 0 < b.lead <= 0.35 and 0 < b.tail <= 0.45
    assert svc.sid_for_word(tr, b.w0) == 1 and svc.sid_for_word(tr, b.w1) == 3


def test_resolve_bounds_by_seconds_snaps_to_natural_boundary() -> None:
    tr = _transcript()
    s1 = tr.sentences[1]
    b = svc.resolve_bounds(
        tr,
        start_word_idx=None,
        end_word_idx=None,
        start_sec=s1.s + 0.3,
        end_sec=tr.sentences[2].e - 0.1,
        snap=True,
    )
    assert b.w0 == s1.w0  # ближайший естественный стык — начало предложения
    assert b.w1 == tr.sentences[2].w1


def test_resolve_bounds_keeps_current_edge_and_validates() -> None:
    tr = _transcript()
    cur = (tr.sentences[0].s - 0.2, tr.sentences[2].e + 0.2)
    b = svc.resolve_bounds(
        tr, start_word_idx=None, end_word_idx=10, start_sec=None, end_sec=None, snap=True, current=cur
    )
    assert b.w0 == 0 and b.w1 == 10
    with pytest.raises(ValidationFailed) as ei:
        svc.resolve_bounds(
            tr, start_word_idx=99, end_word_idx=None, start_sec=None, end_sec=None, snap=True, current=cur
        )
    assert ei.value.details["errors"][0]["type"] == "word_index"
    with pytest.raises(ValidationFailed) as ei:  # короче 3 с
        svc.resolve_bounds(tr, start_word_idx=0, end_word_idx=1, start_sec=None, end_sec=None, snap=True)
    assert ei.value.details["errors"][0]["type"] == "clip_length"


def test_clip_sids_and_word_span() -> None:
    tr = _transcript()
    assert svc.clip_sids(tr, tr.sentences[1].s - 0.1, tr.sentences[2].e + 0.1) == (1, 2)
    assert svc.word_span(tr.words, tr.words[2].s, tr.words[5].e) == (2, 5)


# ---------------------------------------------------------------- авторский текст и реплики


def test_lint_blocks_forbidden_phrase_and_keeps_mode() -> None:
    tr = _transcript()
    niche = svc.ClipNiche(forbidden_phrases=["гарантированный результат"])
    res = svc.lint_author_script(
        {"cold_open": {"vo_text": "Гарантированный результат за неделю."}, "on_screen_title": "Итог"},
        tr, 0, 3, mode="llm", label="LLM x/y", niche=niche,
    )  # fmt: skip
    assert res.blocked and res.mode == "llm" and res.label == "LLM x/y"
    assert any(i.code == "forbidden_phrase" for i in res.issues)
    tpl = svc.lint_author_script({"cold_open": {"vo_text": "Смысл в темпе."}}, tr, 0, 3, mode="heuristic")
    assert tpl.issues[0].code == "template_text" and tpl.needs_operator_edit


def test_template_saved_as_operator_text_stays_template() -> None:
    """Регрессия: PUT шаблона (или «Отменить черновик» → «В рендер») давал mode=operator без правки."""
    from autoshorts.clipping.author.script import draft_script_heuristic

    tr = _transcript()
    tpl = draft_script_heuristic([(s.sid, s.text) for s in tr.sentences], title="Итог")
    res = svc.lint_author_script(tpl.model_dump(), tr, 0, 3, mode="operator")
    assert res.mode == "heuristic" and res.needs_operator_edit and res.issues[0].code == "template_text"
    assert "operator_text_is_template" in res.warnings
    body = tpl.model_dump()
    body["cold_open"]["vo_text"] = "Автор путает частоту и вероятность — разберём, где именно."
    if body["inserts"]:
        body["inserts"][0]["vo_text"] = "Вот здесь и ошибка: одна удачная неделя ничего не доказывает."
    own = svc.lint_author_script(body, tr, 0, 3, mode="operator")
    assert own.mode == "operator" and not own.needs_operator_edit


def test_author_result_dict_roundtrip() -> None:
    tr = _transcript()
    res = svc.lint_author_script(
        {
            "cold_open": {"vo_text": "Вот почему темп важнее громкости."},
            "inserts": [{"after_sid": 1, "vo_text": "Заметьте разницу."}],
        },
        tr, 0, 3, mode="operator",
    )  # fmt: skip
    data = svc.author_result_to_dict(res, source="operator", updated_by=7, start_sid=0, end_sid=3)
    assert data["source"] == "operator" and data["clip"] == {"start_sid": 0, "end_sid": 3}
    back = svc.author_result_from_dict(data)
    assert back.script == res.script and back.mode == "operator"
    assert [i.code for i in back.issues] == [i.code for i in res.issues]
    summary = svc.author_summary({"author_script": data})
    assert summary == {
        "mode": "operator",
        "label": "текст оператора",
        "needs_operator_edit": False,
        "blocked": False,
        "issues_count": len(res.issues),
    }


def test_take_dict_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autoshorts.clipping.author.voice import VoiceTake
    from autoshorts.core.config import reload_env

    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    reload_env()
    wav = tmp_path / "jobs" / "abc" / "cold_open.wav"
    wav.parent.mkdir(parents=True)
    wav.write_bytes(b"RIFF")
    take = VoiceTake(
        "cold_open", wav, 2.5, [Word("Привет", 0.1, 0.5)], "tts", "Привет", "v", "edge_tts", "tts"
    )
    d = svc.take_to_dict(take)
    assert d["path"] == "jobs/abc/cold_open.wav"
    back = svc.take_from_dict("cold_open", d)
    assert back.path == wav.resolve() and back.duration == 2.5 and back.words[0].w == "Привет"
    assert back.kind == "tts" and back.provider == "edge_tts"


def test_estimated_takes_use_ready_recordings() -> None:
    parts = [("cold_open", "Короткий хук."), ("outro", "А вы как считаете?")]
    segs = {"cold_open": {"status": "ready", "duration_sec": 3.2, "kind": "operator"}}
    takes = svc._estimate_takes(parts, "tts", segs)
    assert takes["cold_open"]["duration"] == 3.2 and takes["cold_open"]["kind"] == "operator"
    assert takes["outro"]["kind"] == "tts" and takes["outro"]["duration"] >= 1.0
    # «Мой голос» без записи: рендер озвучит реплику TTS — оценка тоже считает её синтезом
    mine = svc._estimate_takes(parts, "operator_recording", segs)
    assert mine["cold_open"]["kind"] == "operator" and mine["outro"]["kind"] == "tts"


def test_section_start_for_stream_copied_sections(tmp_path: Path) -> None:
    """mp4 с edit list начинается ровно с запрошенного `start`; webm — с ключевого кадра раньше."""
    assert svc.section_start(tmp_path / "section.mp4", 97.0, 160.0, 63.1) == 97.0
    assert svc.section_start(tmp_path / "section.webm", 97.0, 160.0, 65.5) == pytest.approx(94.5)
    assert svc.section_start(tmp_path / "section.webm", 2.0, 60.0, 70.0) == 0.0
    assert svc.section_start(tmp_path / "section.mkv", 97.0, 160.0, 0.0) == 97.0


def test_code_url_template_goes_through_redirector(monkeypatch: pytest.MonkeyPatch) -> None:
    """Код в кадре и ссылки пакета — через один хост редиректора (§6.6), не через домен бренда."""
    from types import SimpleNamespace

    from autoshorts.services import products, tracking

    monkeypatch.setattr(
        "autoshorts.core.config.get_env", lambda: SimpleNamespace(public_base_url="https://u.stand.example/")
    )
    branded = {
        "redirect_host": None,
        "brand": {"display_domain": "example.org"},
        "landing_url": "https://x.ru/",
    }
    assert products.code_url_template(branded) == "u.stand.example/{code}"
    assert tracking.redirect_url(branded, "abcde") == "https://u.stand.example/abcde"
    with_host = {**branded, "redirect_host": "u.umka.chat"}
    assert products.code_url_template(with_host) == "u.umka.chat/{code}"
    assert tracking.redirect_url(with_host, "abcde") == "https://u.umka.chat/abcde"


def test_sniff_audio_signatures() -> None:
    assert svc._sniff_audio(b"\x1a\x45\xdf\xa3....") == "audio/webm"
    assert svc._sniff_audio(b"OggS\x00\x02") == "audio/ogg"
    assert svc._sniff_audio(b"RIFF\x24\x00\x00\x00WAVEfmt ") == "audio/wav"
    assert svc._sniff_audio(b"ID3\x03") is None


# ---------------------------------------------------------------- бюджет платных вызовов


class FakeBudget:
    def __init__(self) -> None:
        self.reserved: list[float] = []
        self.settled: list[tuple[Any, float, dict[str, Any]]] = []
        self.released: list[Any] = []

    def reserve(self, amount: float, *, provider: str) -> Any:
        from autoshorts.services.budget import BudgetReservation

        self.reserved.append(amount)
        return BudgetReservation(Decimal(str(amount)), provider, datetime.now(UTC).date())

    def settle(self, res: Any, actual: float, **event: Any) -> None:
        self.settled.append((res, actual, event))

    def release(self, res: Any) -> None:
        self.released.append(res)


def test_paid_calls_writes_event_per_call_and_releases_unused() -> None:
    budget = FakeBudget()
    with svc.PaidCalls(budget, provider="openai_compat", estimate_usd=0.03) as paid:
        paid.on_event({"step_name": "highlight_select", "model": "m", "cost_usd": 0.004, "latency_ms": 10})
        paid.on_event({"step_name": "highlight_select", "status": "error", "error": "boom", "cost_usd": 0.0})
    assert budget.reserved == [0.03]
    assert [s[1] for s in budget.settled] == [0.004, 0.0]
    assert budget.settled[0][0].amount_usd == Decimal("0.03")  # первый вызов закрывает резерв
    assert budget.settled[1][0].amount_usd == Decimal(0) and budget.settled[1][2]["status"] == "error"
    assert budget.released == [] and paid.cost_usd == pytest.approx(0.004)
    unused = FakeBudget()
    with svc.PaidCalls(unused, provider="openai_compat", estimate_usd=0.01):
        pass
    assert len(unused.released) == 1 and unused.settled == []
    off = FakeBudget()
    with svc.PaidCalls(off, provider="mock", estimate_usd=0.01, enabled=False) as p:
        p.on_event({"cost_usd": 0.0})
    assert off.reserved == [] and off.settled == []


# ---------------------------------------------------------------- режимы LLM и подписи


def test_llm_modes_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(svc, "llm_key", lambda: None)
    # адрес по умолчанию: локальный .env стенда задаёт свой LLM_BASE_URL (OpenRouter) — тест от него не зависит
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.setenv("CLIP_LLM", "auto")
    assert svc.llm_client("select") == (None, "heuristic")
    monkeypatch.setenv("CLIP_LLM", "mock")
    client, mode = svc.llm_client("author")
    assert mode == "mock" and client.is_mock
    monkeypatch.setenv("CLIP_LLM", "none")
    monkeypatch.setattr(svc, "llm_key", lambda: "sk-test")
    assert svc.llm_client("select") == (None, "heuristic")
    monkeypatch.setenv("CLIP_LLM", "auto")
    client, mode = svc.llm_client("select")
    assert mode == "llm" and client.base_url.startswith("https://umka.chat/v1")
    assert svc.llm_client("select", "heuristic") == (None, "heuristic")


def test_report_out_labels_and_filters() -> None:
    report = _report(0.2, {**LICENSED, "rights_type": "licensed"})
    out = svc.report_out(report.to_dict(), stage="final", computed_at="2026-09-27T10:00:00+00:00")
    assert out["verdict"] == "fail" and out["verdict_label"] == "не заливать, пока не исправлено"
    assert out["summary"]["rights_type"] == "licensed_written"
    titles = {c["code"]: c["title"] for c in out["checks"]}
    assert titles["author_share"] == "Доля авторского вклада (author_share)"
    assert out["computed_at"].year == 2026
    from autoshorts.api.schemas.clipping import OriginalityReportOut

    OriginalityReportOut.model_validate(out)


def test_candidate_view_blocking_and_labels() -> None:
    now = datetime.now(UTC)
    row = {
        "id": 5, "source_id": 2, "start_sec": 10.0, "end_sec": 40.5, "start_sid": 1, "end_sid": 4,
        "hook_sid": 1,
        "start_word_idx": 3, "end_word_idx": 60,
        "snap": {"w0": 3, "w1": 60, "lead_sec": 0.2, "tail_sec": 0.3, "raw_start_sec": 10.1,
                 "raw_end_sec": 40.2},
        "final_score": Decimal("0.712"), "trend_prior": Decimal("0.5"),
        "llm_scores": {"hook": 7, "standalone": 6, "payoff": 6, "emotion": 5, "niche_fit": 6, "novelty": 4},
        "features": {"score_parts": {"llm": 0.6},
                     "forbidden_hits": [{"phrase": "гарантия", "start": 1, "end": 8}],
                     "origin": "heuristic", "speech_rate_wps": 2.8},
        "hook_type": "weird", "reason": "r", "transcript_excerpt": "t", "title": "", "hook_text": None,
        "content_flags": ["medical_claim", "forbidden_phrase", "unknown_flag"],
        "unblocked_flags": ["medical_claim"],
        "needs_context": False, "status": "proposed", "created_by_kind": "auto", "origin": "heuristic",
        "notes": None, "job_id": None, "superseded_at": None, "created_at": now, "updated_at": now,
    }  # fmt: skip
    v = svc.candidate_view(row)
    assert v["blocked"] and v["block_reasons"] == ["forbidden_phrase"]
    assert v["content_flags"] == ["medical_claim", "forbidden_phrase"]
    assert v["hook_type"] is None and v["title"] is None
    assert v["origin_label"] == "Эвристика без LLM" and v["features"] == {"speech_rate_wps": 2.8}
    assert v["score_parts"] == {"llm": 0.6} and v["snap"]["w1"] == 60
    from autoshorts.api.schemas.clipping import ClipCandidate

    ClipCandidate.model_validate(v)

"""Отчёт об оригинальности: вердикт pass/warn/fail, конкретные подсказки, пропуск непроверенного."""

from __future__ import annotations

from datetime import date

import pytest

from autoshorts.clipping.author.script import AuthorScript
from autoshorts.clipping.author.share import TextEvent, VoiceEvent, compute_author_share
from autoshorts.clipping.edl import LayoutConfig, layout_clip
from autoshorts.clipping.originality import (
    build_originality_report,
    sid_jaccard,
    suggest_insert_sid,
)
from autoshorts.clipping.transcript import Transcript, Word

OWN = {"rights_type": "own", "owner_name": "Umka", "proof_note": "own"}
LIC = {
    "rights_type": "licensed_written",
    "owner_name": "Иван Петров",
    "proof_note": "договор 12/09",
    "attribution_text": "Иван Петров, youtube.com/@ivan",
    "allowlist_status": "allowlisted",
}
TODAY = date(2026, 9, 27)


def good_share():
    # хук 5,9 с (исходник −16 дБ), вставка на стоп-кадре 4 с, outro на стоп-кадре 6 с, заголовок
    return compute_author_share(
        40.0,
        freeze_windows=[(10.0, 14.0), (34.0, 40.0)],
        voice=[
            VoiceEvent(0.1, 6.0, "cold_open", 16.0),
            VoiceEvent(10.1, 13.8, "insert", 60, True),
            VoiceEvent(34.1, 39.5, "outro", 60, True),
        ],
        texts=[TextEvent(0, 40, "title")],
    )


def low_share():
    return compute_author_share(40.0, voice=[VoiceEvent(0.1, 3.0, "cold_open", 16.0)])


def _transcript() -> Transcript:
    words: list[Word] = []
    t = 1.0
    for i in range(10):
        for j in range(6):
            words.append(Word(f"сл{i}{j}" + ("." if j == 5 else ""), round(t, 3), round(t + 0.4, 3)))
            t += 0.5
        t += 0.6
    return Transcript.from_words(words, language="ru", duration=t + 2, provider="test")


def _edl_low(tr: Transcript):
    script = AuthorScript.model_validate({"cold_open": {"vo_text": "Хук автора.", "duck_source": True}})
    takes = {
        "cold_open": {"path": "c.wav", "duration": 2.0, "words": [], "kind": "tts", "text": "Хук автора."}
    }
    return layout_clip(
        tr,
        start=tr.sentences[0].s,
        end=tr.sentences[-1].e,
        script=script,
        takes=takes,
        source={"path": "s.mp4", "width": 1920, "height": 1080, "duration": tr.duration},
        config=LayoutConfig(min_total_sec=0),
    )


def test_pass_for_licensed_with_real_author_layer() -> None:
    rep = build_originality_report(
        share=good_share(),
        rights=LIC,
        watermark={"suspect": False, "severity": "ok"},
        clip_sids=[3, 4, 5],
        previous_clips=[{"id": 7, "sids": [20, 21, 22]}],
        library_similarity=[(11, 0.52)],
        author_mode="llm",
        voice_kinds=["operator"],
        today=TODAY,
    )
    assert rep.verdict == "pass", rep.to_text()
    assert rep.check("author_share").status == "pass"
    assert rep.check("repeat_moment").status == "pass"
    assert rep.check("library_similarity").status == "pass"
    assert rep.summary["gate_applies"] is True
    assert rep.summary["own_voice_sec"] == pytest.approx(5.9 + 3.7 + 5.4, abs=0.01)


def test_licensed_low_share_fails_with_concrete_insert_suggestion() -> None:
    tr = _transcript()
    edl = _edl_low(tr)
    share = edl.author_share()
    rep = build_originality_report(
        share=share, rights=LIC, edl=edl, transcript=tr, author_mode="llm", today=TODAY
    )
    assert rep.verdict == "fail"
    assert rep.check("author_share").status == "fail"
    joined = " ".join(rep.suggestions)
    assert "после sid" in joined and "author_share вырастет" in joined
    assert "outro" in joined  # нет вывода в конце


def test_own_low_share_is_only_shown() -> None:
    rep = build_originality_report(share=low_share(), rights=OWN, author_mode="llm", today=TODAY)
    assert rep.check("author_share").status == "info"
    assert rep.verdict in ("pass", "warn")
    assert rep.summary["gate_applies"] is False


@pytest.mark.parametrize(
    ("wm", "status"),
    [
        ({"suspect": True, "severity": "warning", "best": {"tiktok": 0.93}}, "fail"),
        ({"suspect": False, "severity": "hint", "static_overlay": {"corner": "top_right"}}, "warn"),
        ({"suspect": False, "severity": "ok"}, "pass"),
        (None, "skip"),
    ],
)
def test_foreign_watermark(wm: dict | None, status: str) -> None:
    rep = build_originality_report(
        share=good_share(), rights=OWN, watermark=wm, author_mode="llm", today=TODAY
    )
    assert rep.check("foreign_watermark").status == status
    if status == "fail":
        assert rep.verdict == "fail"
        assert any("мастер" in s for s in rep.suggestions)


def test_repeat_moment_thresholds() -> None:
    assert sid_jaccard([1, 2, 3, 4], [3, 4, 5, 6]) == pytest.approx(2 / 6)
    rep = build_originality_report(
        share=good_share(),
        rights=OWN,
        clip_sids=[1, 2, 3, 4],
        previous_clips=[{"id": "c9", "sids": [1, 2, 3, 5]}],
        author_mode="llm",
        today=TODAY,
    )
    assert rep.check("repeat_moment").status == "fail"
    assert any("c9" in s for s in rep.suggestions)
    rep2 = build_originality_report(
        share=good_share(),
        rights=OWN,
        clip_sids=[1, 2, 3, 4],
        previous_clips=[{"id": "c9", "sids": [3, 4, 5, 6]}],
        author_mode="llm",
        today=TODAY,
    )
    assert rep2.check("repeat_moment").status == "warn"


@pytest.mark.parametrize(
    ("rights", "status"),
    [
        ({**LIC, "valid_until": "2026-01-01"}, "fail"),
        ({**LIC, "attribution_text": ""}, "fail"),
        ({**LIC, "allowlist_status": "requested"}, "warn"),
        ({**LIC, "valid_until": "2026-10-10"}, "warn"),
        ({}, "fail"),
        (OWN, "pass"),
    ],
)
def test_rights_status(rights: dict, status: str) -> None:
    rep = build_originality_report(share=good_share(), rights=rights, author_mode="llm", today=TODAY)
    assert rep.check("rights").status == status


def test_allowlist_warning_gives_suggestion() -> None:
    rep = build_originality_report(
        share=good_share(),
        rights=LIC,
        rights_warnings=["content_id_allowlist_not_confirmed"],
        author_mode="llm",
        today=TODAY,
    )
    assert any("allowlist" in s for s in rep.suggestions)
    assert any("allowlist" in n for n in rep.notes)


def test_library_similarity_warn_and_skip() -> None:
    rep = build_originality_report(
        share=good_share(),
        rights=OWN,
        library_similarity=[(1, 0.5), (2, 0.95)],
        author_mode="llm",
        today=TODAY,
    )
    chk = rep.check("library_similarity")
    assert chk.status == "warn" and chk.value == pytest.approx(0.95)
    rep2 = build_originality_report(share=good_share(), rights=OWN, author_mode="llm", today=TODAY)
    assert rep2.check("library_similarity").status == "skip"


def test_template_and_mock_author_text() -> None:
    mock = build_originality_report(share=good_share(), rights=OWN, author_mode="mock", today=TODAY)
    assert mock.check("author_text").status == "fail" and mock.verdict == "fail"
    heur = build_originality_report(share=good_share(), rights=OWN, author_mode="heuristic", today=TODAY)
    assert heur.check("author_text").status == "warn"
    edited = build_originality_report(
        share=good_share(), rights=OWN, author_mode="heuristic", operator_edited=True, today=TODAY
    )
    assert edited.check("author_text").status == "pass"


def test_unedited_template_fails_for_foreign_rights() -> None:
    """OWNER-DECISIONS п. 5: шаблон без LLM на чужом источнике — не авторский слой, стоп."""
    heur = build_originality_report(share=good_share(), rights=LIC, author_mode="heuristic", today=TODAY)
    assert heur.check("author_text").status == "fail" and heur.verdict == "fail"
    assert "author_text" in heur.fails and "чужих прав" in (heur.check("author_text").detail or "")
    edited = build_originality_report(
        share=good_share(), rights=LIC, author_mode="heuristic", operator_edited=True, today=TODAY
    )
    assert edited.check("author_text").status == "pass"


def test_script_issues_become_warnings_and_errors() -> None:
    issues = [
        {"code": "filler", "severity": "warn", "where": "cold_open", "detail": "пустые фразы: «шок»"},
        {"code": "retells_source", "severity": "warn", "where": "insert_0", "detail": "пересказ"},
    ]
    rep = build_originality_report(
        share=good_share(), rights=OWN, author_mode="llm", script_issues=issues, today=TODAY
    )
    assert rep.check("author_text").status == "warn"
    assert any("пустые фразы" in s for s in rep.suggestions)
    err = [
        {"code": "forbidden_phrase", "severity": "error", "where": "script", "detail": "запрещённая фраза"}
    ]
    rep2 = build_originality_report(
        share=good_share(), rights=OWN, author_mode="llm", script_issues=err, today=TODAY
    )
    assert rep2.check("author_text").status == "fail"


def test_text_dominated_share_warns_voice_balance() -> None:
    # тезисы на экране вне голоса: текст упирается в потолок 0,10 и всё равно больше голоса
    share = compute_author_share(
        40.0, voice=[VoiceEvent(0, 2, "cold_open", 16)], texts=[TextEvent(4, 30, "on_screen")]
    )
    assert share.text_capped and share.text_part == pytest.approx(0.10)
    rep = build_originality_report(share=share, rights=OWN, author_mode="llm", today=TODAY)
    assert rep.check("voice_balance").status == "warn"
    assert any("надписях" in s for s in rep.suggestions)
    # при чужих правах это часть гейта: стоп
    lic = build_originality_report(share=share, rights=LIC, author_mode="llm", today=TODAY)
    assert lic.check("voice_balance").status == "fail" and lic.verdict == "fail"


def test_persistent_title_plus_3s_voice_does_not_pass_gate() -> None:
    """Регрессия: заголовок на весь ролик + 3 с хука давали 0,306 и проходили гейт 0,30."""
    share = compute_author_share(
        40.0,
        voice=[VoiceEvent(0.12, 3.12, "cold_open", 16.0)],
        texts=[TextEvent(0.0, 40.0, "title")],
    )
    assert share.value < 0.15
    rep = build_originality_report(
        share=share, rights={**LIC, "rights_type": "cc_by"}, author_mode="llm", today=TODAY
    )
    assert rep.check("author_share").status == "fail" and rep.verdict == "fail"


def test_voice_balance_fails_under_gate_even_if_share_passes() -> None:
    """Защита в глубину: если доля (с другими весами) прошла на надписях, гейт всё равно стоп."""
    share = compute_author_share(
        40.0,
        voice=[VoiceEvent(0, 4, "cold_open", 16)],
        texts=[TextEvent(4, 40, "on_screen")],
        text_part_max=1.0,
    )
    assert share.value >= 0.30 and share.voice_share < 0.15
    lic = build_originality_report(share=share, rights=LIC, author_mode="llm", today=TODAY)
    assert lic.check("author_share").status == "pass"
    assert lic.check("voice_balance").status == "fail" and lic.verdict == "fail"


def test_script_errors_fail_in_heuristic_and_mock_modes() -> None:
    """Регрессия: в режиме heuristic ошибки линтера (гарантия, запрет ниши) давали только warn."""
    issues = [
        {"code": "template_text", "severity": "warn", "where": "script", "detail": "шаблон"},
        {"code": "content_flag", "severity": "error", "where": "script", "detail": "guarantee_claim: …"},
    ]
    for mode in ("llm", "heuristic", "mock", "operator"):
        rep = build_originality_report(
            share=good_share(), rights=OWN, author_mode=mode, script_issues=issues, today=TODAY
        )
        chk = rep.check("author_text")
        assert chk.status == "fail" and "guarantee_claim" in chk.detail, mode
        assert rep.verdict == "fail", mode
        assert any(s.startswith("Уберите из авторского текста") for s in rep.suggestions), mode
    heur = build_originality_report(
        share=good_share(), rights=OWN, author_mode="heuristic", script_issues=issues, today=TODAY
    )
    assert any("шаблонные реплики" in s for s in heur.suggestions)


def test_author_text_repetition() -> None:
    mine = ["Смотрите, как автор объясняет разницу между привычкой и дисциплиной."]
    prev = [(5, "Смотрите, как автор объясняет разницу между привычкой и дисциплиной, это важно.")]
    rep = build_originality_report(
        share=good_share(),
        rights=OWN,
        author_mode="llm",
        author_texts=mine,
        previous_author_texts=prev,
        today=TODAY,
    )
    assert rep.check("author_text_repetition").status == "warn"
    # дословно та же озвучка (шаблон на всех роликах) — стоп
    same = build_originality_report(
        share=good_share(),
        rights=OWN,
        author_mode="llm",
        author_texts=mine,
        previous_author_texts=[(6, mine[0])],
        today=TODAY,
    )
    assert same.check("author_text_repetition").status == "fail"
    assert same.verdict == "fail"
    assert any("дословно" in s for s in same.suggestions)


def test_identical_insert_line_warns_but_identical_outro_is_fine() -> None:
    parts = [
        ("cold_open", "Почему эта сцена держит зрителя дольше остальных?"),
        ("insert_0", "Остановлюсь здесь. Это ключевой момент: дальше из него следует вывод."),
        ("outro", "А вы с этим согласны? Напишите в комментариях, что думаете."),
    ]
    prev = [
        ("cold_open", "Совсем другой хук про бюджет и сроки съёмок большого фильма."),
        ("insert_0", "Остановлюсь здесь. Это ключевой момент: дальше из него следует вывод."),
        ("outro", "А вы с этим согласны? Напишите в комментариях, что думаете."),
    ]
    rep = build_originality_report(
        share=good_share(),
        rights=OWN,
        author_mode="operator",
        author_parts=parts,
        previous_author_texts=[("auto/clip_01", prev)],
        today=TODAY,
    )
    chk = rep.check("author_text_repetition")
    assert chk.status == "warn" and chk.value < 0.9
    assert "вставка 1 = ролик auto/clip_01" in chk.detail
    assert any("дословно повторяет ролик auto/clip_01" in s for s in rep.suggestions)
    # одинаковое аутро само по себе — не повод (YouTube допускает одинаковые интро и аутро)
    only_outro = build_originality_report(
        share=good_share(),
        rights=OWN,
        author_mode="operator",
        author_parts=[parts[0], parts[2]],
        previous_author_texts=[("auto/clip_01", [prev[0], prev[2]])],
        today=TODAY,
    )
    assert only_outro.check("author_text_repetition").status == "pass"


def test_synthetic_voice_suggests_live_voice() -> None:
    rep = build_originality_report(
        share=good_share(), rights=OWN, author_mode="llm", voice_kinds=["tts"], today=TODAY
    )
    assert any("своим голосом" in s for s in rep.suggestions)
    assert any("ai_disclosure" in n for n in rep.notes)


def test_suggest_insert_sid_picks_body_sentence() -> None:
    tr = _transcript()
    edl = _edl_low(tr)
    got = suggest_insert_sid(tr, edl)
    assert got is not None
    sid, tail = got
    assert tr.sentences[0].sid < sid < tr.sentences[-1].sid
    assert tail


def test_report_serializes() -> None:
    rep = build_originality_report(share=good_share(), rights=LIC, author_mode="llm", today=TODAY)
    d = rep.to_dict()
    assert d["verdict"] in ("pass", "warn", "fail")
    assert {"author_share", "own_voice_sec", "own_text_sec"} <= set(d["summary"])
    assert "Оригинальность" in rep.to_text()


def test_markdown_report_is_russian_and_actionable() -> None:
    tr = _transcript()
    edl = _edl_low(tr)
    rep = build_originality_report(
        share=edl.author_share(),
        rights={**LIC, "rights_type": "cc_by", "license": "CC BY 3.0"},
        rights_warnings=["cc_by_attribution_in_description"],
        watermark={"suspect": False, "severity": "ok"},
        edl=edl,
        transcript=tr,
        author_mode="heuristic",
        today=TODAY,
    )
    md = rep.to_markdown(
        heading="Отчёт — clip_01",
        clip={
            "file": "clip_01.mp4",
            "range": "1.0–40.0 с",
            "vo_parts": [("cold_open", "Хук автора."), ("outro", "")],
            "on_screen_title": "Мой заголовок",
        },
    )
    assert md.startswith("# Отчёт — clip_01\n")
    assert "**Итог: СТОП (fail)**" in md
    assert "| Доля авторского вклада (author_share) | стоп |" in md
    assert "## Что сделать до заливки" in md
    assert "Добавьте вставку-комментарий" in md
    assert "**Хук (cold open):** Хук автора." in md
    assert "Вывод (outro)" not in md  # пустые реплики не показываются
    assert "Атрибуция (в описание ролика):** Иван Петров" in md
    # в таблице проверок проваленные идут первыми
    lines = md.splitlines()
    head = lines.index("| Проверка | Статус | Значение | Порог | Комментарий |")
    assert "| стоп |" in lines[head + 2]
    # voice_balance не ругается на «надписи», когда надписей нет вовсе
    assert rep.check("voice_balance").status == "pass"


def test_markdown_cells_are_escaped() -> None:
    from autoshorts.clipping.originality import _md_cell

    assert _md_cell("a|b\nc") == "a\\|b c"
    assert _md_cell(None) == "—"
    assert _md_cell(0.456) == "0.46"


def test_dropped_insert_and_wordless_title_are_explained() -> None:
    tr = _transcript()
    edl = _edl_low(tr)
    edl.warnings = ["insert_0_dropped_to_fit:sid7", "insert_1_skipped_out_of_range:sid8"]
    from autoshorts.clipping.edl import TextItem

    edl.texts = [TextItem("title", "🔥🔥🔥", 0.0, edl.duration, "body", False, "no_words")]
    rep = build_originality_report(
        share=edl.author_share(), rights=OWN, edl=edl, transcript=tr, author_mode="llm", today=TODAY
    )
    assert any(s.startswith("Вставка 1 (после sid 7) не влезла в 58 с") for s in rep.suggestions)
    assert any(s.startswith("Вставка 2 (после sid 8) не попала в ролик") for s in rep.suggestions)
    chk = rep.check("own_text")
    assert chk.status == "warn" and "нет своих слов" in chk.detail
    assert any("Заголовок без слов не засчитывается" in s for s in rep.suggestions)
    md = rep.to_markdown()
    assert "засчитано 0.0 с × 0,25" in md


def test_channel_without_author_voice_skips_voice_balance() -> None:
    """Канал без нейроозвучки (решение владельца): голос не требуется, порог доли задаёт канал."""
    share = compute_author_share(40.0, voice=[], texts=[TextEvent(0.0, 40.0, "title")])
    rep = build_originality_report(share=share, rights=LIC, author_mode="llm", today=TODAY, min_share=0.0,
                                   require_voice=False)  # fmt: skip
    assert rep.check("voice_balance").status == "skip"
    assert rep.check("author_share").status != "fail"

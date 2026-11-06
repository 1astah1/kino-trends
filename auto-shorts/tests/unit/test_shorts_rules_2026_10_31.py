"""Правки по сбору правил Shorts 31.10.2026 (docs/SHORTS_RULES.md): справка о фильме для викторины, факт после
ответа по рубрике, лимит нарезок в сутки на канал, субтитры в безопасной зоне плеера."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from datetime import time as dtime

from autoshorts.clipping.author.film_ref import qid_from, trim_article
from autoshorts.clipping.author.script import QUIZ_FACT_RUBRICS, AuthorScript, build_author_messages, quiz_finalize
from autoshorts.clipping.frame_style import SAFE_SUB_BOTTOM, SAFE_SUB_RIGHT, FrameStyle, caption_layout
from autoshorts.services.autopublish import Plan, next_slot


def _script(outro: str) -> AuthorScript:
    return AuthorScript.model_validate({
        "cold_open": {"vo_text": "Вопрос", "on_screen": "", "duck_source": False},
        "inserts": [{"after_sid": 3, "mode": "over_ducked", "vo_text": "Подсказка: снят по видеоигре.", "on_screen": ""}],
        "outro": {"vo_text": outro, "cta_style": "question"},
        "on_screen_title": "Угадаешь ужастик нулевых?",
    })


def test_qid_from_rights_note() -> None:
    assert qid_from("источник из подборки Кино-трендов; wikidata=Q23871") == "Q23871"
    assert qid_from("права заявлены владельцем канала") is None
    assert qid_from(None) is None


def test_trim_article_keeps_intro_and_production_drops_plot() -> None:
    text = (
        "«Такси» — французская комедия 1998 года.\n\n"
        "== Сюжет ==\nДаниэль водит такси и гоняет по Марселю, а в конце всё раскрывается.\n\n"
        "== Создание ==\nСценарий Люк Бессон написал за тридцать дней, съёмки шли в Марселе летом 1997 года.\n\n"
        "== В ролях ==\nСами Насери — Даниэль\n\n"
        "== Кассовые сборы ==\nФильм собрал 44,4 млн долларов и породил серию из четырёх сиквелов.\n"
    )
    out = trim_article(text)
    assert out.startswith("«Такси» — французская комедия")
    assert "[Создание]" in out and "[Кассовые сборы]" in out
    assert "Сюжет" not in out and "раскрывается" not in out and "В ролях" not in out


REF = ("«Такси» — французский комедийный боевик 1998 года, снятый по сценарию Люка Бессона. В мировом прокате "
       "фильм собрал 44,4 млн долларов и породил серию из четырёх сиквелов.")


def test_quiz_finalize_appends_fact_after_answer() -> None:
    fact = "Фильм собрал 44,4 млн долларов и породил четыре сиквела."
    s = quiz_finalize(_script(fact), "Такси", "1998", seed=1, ref=REF)
    assert s.outro.vo_text == f"Это «Такси», 1998 год. {fact}"
    assert s.outro.cta_style == "loop_back"
    # выдумка (нет в справке) и факт без справки — прежний вопрос; мнение с пометкой проходит
    invented = "Бессон не смог получить водительские права и решил написать сценарий о таксисте."
    assert quiz_finalize(_script(invented), "Такси", "1998", seed=1, ref=REF).outro.vo_text.endswith("Угадали?")
    assert quiz_finalize(_script(fact), "Такси", "1998", seed=1).outro.vo_text.endswith("Угадали?")
    opinion = "По-моему, вся сцена держится на паузе перед погоней."
    assert quiz_finalize(_script(opinion), "Такси", "1998", seed=1).outro.vo_text.endswith(opinion)
    # пустой или служебный текст модели — прежний вопрос
    assert quiz_finalize(_script(""), "Такси", "1998", seed=1).outro.vo_text == "Это «Такси», 1998 год. Угадали?"
    assert quiz_finalize(_script("Угадали? Пишите"), "Такси", None, seed=1).outro.vo_text == "Это «Такси». Угадали?"


def test_quiz_prompt_rotates_rubric_and_carries_reference() -> None:
    from autoshorts.clipping.transcript import Sentence, Transcript

    sents = [Sentence(sid=i, s=float(i), e=float(i) + 0.9, text=f"Реплика номер {i}.", w0=i, w1=i) for i in range(6)]
    tr = Transcript(language="ru", duration=6.0, provider="test", model=None, words=[], sentences=sents)
    msgs = build_author_messages(tr, 1, 4, quiz=("Такси", "1998"), film_ref="Фильм собрал 44,4 млн долларов.")
    system, user = msgs[0]["content"], msgs[-1]["content"]
    user_text = user if isinstance(user, str) else " ".join(p.get("text", "") for p in user if isinstance(p, dict))
    assert QUIZ_FACT_RUBRICS[(1 + 4) % len(QUIZ_FACT_RUBRICS)] in system
    assert "<reference>" in user_text and "44,4 млн" in user_text
    plain = build_author_messages(tr, 1, 5, quiz=("Такси", "1998"))
    assert QUIZ_FACT_RUBRICS[(1 + 5) % len(QUIZ_FACT_RUBRICS)] in plain[0]["content"]
    assert "<reference>" not in str(plain[-1]["content"])


def test_clips_per_day_caps_only_clips() -> None:
    plan = Plan(mode="auto", slots=(dtime(12), dtime(16), dtime(20)), timezone="UTC", privacy="public",
                videos_per_day=None, clips_per_day=1)
    now = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    day1_clip = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
    # нарезка: первый день уже занят одной нарезкой — следующая уходит на завтра
    when = next_slot(plan, [day1_clip], now=now, taken_clips=[day1_clip], is_clip=True)
    assert when == datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
    # своё (не нарезка) встаёт в свободный слот того же дня
    assert next_slot(plan, [day1_clip], now=now, taken_clips=[day1_clip], is_clip=False) == datetime(
        2026, 11, 1, 16, 0, tzinfo=UTC
    )
    # без лимита — как раньше
    free = Plan(mode="auto", slots=plan.slots, timezone="UTC", privacy="public", videos_per_day=None)
    assert next_slot(free, [day1_clip], now=now, taken_clips=[day1_clip], is_clip=True) - now < timedelta(hours=9)


def test_subtitles_stay_in_safe_zone() -> None:
    for fs in (
        FrameStyle.coerce({"shape": "rounded", "cx": 540, "cy": 1150, "width": 1060, "height": 795}),
        FrameStyle.coerce({"shape": "circle", "cx": 540, "cy": 1150, "size": 1020}),
    ):
        sub = caption_layout(fs).source_zone
        assert sub.y2 <= SAFE_SUB_BOTTOM
        assert sub.x2 <= SAFE_SUB_RIGHT and sub.x1 >= 1080 - SAFE_SUB_RIGHT
        assert sub.y1 > fs.y1 and sub.y2 > sub.y1


def test_wiki_base_title_match() -> None:
    from autoshorts.clipping.author.film_ref import _base_title

    assert _base_title("Такси (фильм, 1998)") == _base_title("Такси") == "такси"
    assert _base_title("Острые козырьки: Бессмертный") != _base_title("Острые козырьки")
    assert _base_title("Тот, кто читает мысли (Менталист)") != _base_title("Менталист")


def test_quiz_hint_must_be_grounded_when_reference_exists() -> None:
    bad = _script("")
    bad.inserts[0].vo_text = "Подсказка: режиссёр снял потом культовую трилогию про киллера."
    assert quiz_finalize(bad, "Такси", "1998", seed=1, ref=REF).inserts == []
    good = _script("")
    good.inserts[0].vo_text = "Подсказка: французский комедийный боевик по сценарию известного режиссёра."
    assert len(quiz_finalize(good, "Такси", "1998", seed=1, ref=REF).inserts) == 1
    # без справки подсказку по кадру не трогаем
    assert len(quiz_finalize(bad.model_copy(deep=True), "Такси", "1998", seed=1).inserts) == 1


def test_quiz_fact_strips_repeated_answer() -> None:
    s = quiz_finalize(_script("Это «Такси», 1998 год. Фильм породил серию из четырёх сиквелов."), "Такси", "1998", seed=1, ref=REF)
    assert s.outro.vo_text == "Это «Такси», 1998 год. Фильм породил серию из четырёх сиквелов."


def test_crop_geometry_skips_letterbox_bars() -> None:
    from autoshorts.clipping.reframe.strategy import CropGeometry

    plain = CropGeometry.for_source(1920, 1080)
    assert (plain.crop_h, plain.y) == (1080, 0)
    boxed = CropGeometry.for_source(1920, 1080, box={"x": 0, "y": 138, "w": 1920, "h": 804})
    assert boxed.crop_h == 804 and boxed.y == 138 and boxed.crop_w == 452
    # полос нет — как раньше
    assert CropGeometry.for_source(1920, 1080, box={"x": 0, "y": 0, "w": 1920, "h": 1080}) == plain


def test_edit_caption_styles_are_big() -> None:
    from autoshorts.clipping.produce import edit_caption_styles

    st = edit_caption_styles()
    assert st["source_style"].font_size >= 90 and st["title_style"].border_style == 1


def test_format_mix_by_job_id_and_flags(monkeypatch) -> None:
    from autoshorts.services import clipping as C

    prof = {"format": "edit", "format_mix": {"edit": 2, "facts": 1}, "author_voice": False, "cold_open": False}
    monkeypatch.setattr(C, "channel_profile", lambda conn, job: prof)
    fmts = [C.channel_format(None, {"id": i}) for i in range(6)]
    assert fmts == ["edit", "edit", "facts", "edit", "edit", "facts"]
    assert C.channel_author_voice(None, {"id": 2}) is True and C.channel_cold_open(None, {"id": 2}) is True
    assert C.channel_author_voice(None, {"id": 0}) is False and C.channel_cold_open(None, {"id": 0}) is False
    assert C.channel_lag_ad(None, {"id": 2}) is False and C.channel_author_text(None, {"id": 2}) is False


def test_facts_finalize_keeps_only_grounded_lines() -> None:
    from autoshorts.clipping.author.script import facts_finalize

    script = AuthorScript.model_validate({
        "cold_open": {"vo_text": "Знаете, сколько собрал фильм «Такси»?", "on_screen": "", "duck_source": False},
        "inserts": [
            {"after_sid": 2, "mode": "freeze_zoom", "vo_text": "В мировом прокате фильм собрал 44,4 млн долларов.", "on_screen": "x"},
            {"after_sid": 4, "mode": "over_ducked", "vo_text": "Бессон не смог получить права и написал сценарий о таксисте.", "on_screen": ""},
        ],
        "outro": {"vo_text": "И породил серию из четырёх сиквелов.", "cta_style": "question"},
        "on_screen_title": "Сколько собрало «Такси»",
    })
    s = facts_finalize(script, REF)
    assert [i.vo_text for i in s.inserts] == ["В мировом прокате фильм собрал 44,4 млн долларов."]
    assert s.inserts[0].mode == "over_ducked" and s.outro.vo_text and s.cold_open.vo_text
    empty = facts_finalize(script, "")
    assert not empty.inserts and not empty.cold_open.vo_text and not empty.outro.vo_text


def test_facts_finalize_uses_meaning_check() -> None:
    from autoshorts.clipping.author.script import facts_finalize

    script = AuthorScript.model_validate({
        "cold_open": {"vo_text": "Знаете, почему «Такси» чуть не остался без студии?", "on_screen": "", "duck_source": False},
        "inserts": [{"after_sid": 2, "mode": "over_ducked", "vo_text": "Gaumont отказала Бессону.", "on_screen": ""}],
        "outro": {"vo_text": "Они договорились о разделе прибыли 50/50.", "cta_style": "question"},
        "on_screen_title": "x",
    })
    s = facts_finalize(script, REF, check=lambda t: "50/50" in t or "Gaumont" in t or "Знаете" in t)
    assert s.outro.vo_text == "Они договорились о разделе прибыли 50/50." and len(s.inserts) == 1

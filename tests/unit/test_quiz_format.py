"""Формат «Угадай фильм»: ответ из атрибуции, подсказки без спойлера, вопрос и ответ подставляет система,
название ролика на площадке не выдаёт ответ."""

from autoshorts.clipping.author.script import QUIZ_TITLES, AuthorScript, film_of, quiz_finalize, reveals_film


def test_film_and_year_from_attribution() -> None:
    assert film_of("фрагмент фильма «Обитель зла» (2002)") == ("Обитель зла", "2002")
    assert film_of("фрагмент сериала «Сваты»") == ("Сваты", None)
    assert film_of(None, "Джентльмены") == ("Джентльмены", None)


def test_spoiler_check_by_significant_words() -> None:
    assert reveals_film("Подсказка: в Обители живут зомби", "Обитель зла")
    assert reveals_film("ДЖЕНТЛЬМЕНЫ удачи", "Джентльмены")
    assert not reveals_film("Подсказка: снят по видеоигре, 2000-е", "Обитель зла")
    assert not reveals_film("Подсказка: всё зло под землёй", "Обитель зла")  # «зла» короче 4 букв


def test_quiz_finalize_sets_question_answer_and_drops_spoiler_hints() -> None:
    script = AuthorScript.model_validate({
        "cold_open": {"vo_text": "Смотрим сцену", "on_screen": "Напряжение", "duck_source": False},
        "inserts": [
            {"after_sid": 3, "mode": "over_ducked", "vo_text": "Подсказка: снят по видеоигре.", "on_screen": "Видеоигра"},
            {"after_sid": 5, "mode": "over_ducked", "vo_text": "Подсказка: это Обитель зла.", "on_screen": "Ответ"},
        ],
        "outro": {"vo_text": "Пишите", "cta_style": "loop_back"},
        "on_screen_title": "Что-то своё",
    })
    s = quiz_finalize(script, "Обитель зла", "2002", seed=7)
    assert s.on_screen_title == QUIZ_TITLES[7 % len(QUIZ_TITLES)] == s.cold_open.vo_text  # «Что-то своё» без «?»
    assert s.cold_open.duck_source and not s.cold_open.on_screen
    assert [i.vo_text for i in s.inserts] == ["Подсказка: снят по видеоигре."]
    assert [i.mode for i in s.inserts] == ["over_ducked"]  # голос поверх сцены, без стоп-кадра
    hooked = script.model_copy(update={"on_screen_title": "Угадаешь ужастик нулевых?"})
    assert quiz_finalize(hooked, "Обитель зла", "2002", seed=7).on_screen_title == "Угадаешь ужастик нулевых?"
    spoiler = script.model_copy(update={"on_screen_title": "Узнаёшь Обитель зла?"})
    assert quiz_finalize(spoiler, "Обитель зла", "2002", seed=7).on_screen_title in QUIZ_TITLES
    assert s.outro.vo_text == "Это «Обитель зла», 2002 год. Угадали?" and s.outro.cta_style == "question"
    assert "Угадали?" in quiz_finalize(script, "Сваты", None, 0).outro.vo_text


def test_package_title_hides_answer_and_description_holds_it() -> None:
    from autoshorts.services.packages import _quiz, _quiz_fix

    class Inp:
        job = {"author_script": {"format": "quiz", "answer": {"film": "Обитель зла", "year": "2002"},
                                 "script": {"on_screen_title": "Узнаёшь фильм?"}}}

    quiz = _quiz(Inp())  # type: ignore[arg-type]
    assert quiz == {"film": "Обитель зла", "year": "2002"}
    title, body, tags = _quiz_fix(Inp(), quiz, "Обитель зла: побег из улья", "Сможете угадать?", ["#ОбительЗла", "#зомби"])  # type: ignore[arg-type]
    assert title == "Узнаёшь фильм?"
    assert body.endswith("Ответ: «Обитель зла» (2002)")
    assert tags == ["#зомби"]
    assert _quiz(type("I", (), {"job": {"author_script": {"script": {}}}})()) is None  # type: ignore[arg-type]

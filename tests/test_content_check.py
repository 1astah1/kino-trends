"""Проверка исходника по содержанию, выбор моментов по трендовым сценам, строгий вердикт исходника."""
from kinotrends import content_check, sources
from kinotrends.llm import LLMError

RU = ["Что вы там балаган устроили? Давай ко мне.", "Тихо всё, замерли.", "Немцы! Тревога!", "Держитесь, братья, мы не сдадимся."] * 30
EN = ["A secret organization was great with superheroes.", "Mr. Incredible Bob works in an insurance company."] * 40


class FakeLLM:
    configured = True
    model = "fake-model"

    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, []

    def ask_json(self, system, user, max_tokens=700, image=None):
        self.calls.append(user)
        if self.fail:
            raise LLMError("429 rate-limited upstream")
        return self.answer, None


def test_english_speech_is_rejected_without_asking_the_model():
    llm = FakeLLM({"is_work": True, "russian": True, "what": "film", "confidence": 0.9})
    res = content_check.check_transcript(llm, "Суперсемейка", 2004, "animated", EN)
    assert res["ok"] is False and "не на русском" in res["reason"] and not llm.calls


def test_stream_with_chat_is_rejected_by_the_model():
    llm = FakeLLM({"is_work": False, "russian": True, "what": "stream", "confidence": 0.9, "reason": "стрим с чатом"})
    res = content_check.check_transcript(llm, "Первый мститель", 2011, "film", RU)
    assert res["ok"] is False and res["reason"] == "стрим с чатом"
    assert "Первый мститель" in llm.calls[0]


def test_film_dialogue_passes_and_model_failure_means_wait():
    ok = content_check.check_transcript(FakeLLM({"is_work": True, "russian": True, "what": "film", "confidence": 0.85}), "Брестская крепость", 2010, "film", RU)
    assert ok["ok"] is True
    wait = content_check.check_transcript(FakeLLM(fail=True), "Брестская крепость", 2010, "film", RU)
    assert wait["ok"] is None


def test_excerpts_come_from_the_whole_video():
    sents = [f"реплика {i}" for i in range(400)]
    ex = content_check.excerpts(sents)
    assert len(ex) == 8 and "реплика 0" in ex[0] and any("реплика 3" in e for e in ex[-2:])


def test_pick_by_scenes_keeps_model_order_and_known_ids():
    cands = [{"id": 1, "title": "Скучно"}, {"id": 2, "title": "Тревога"}, {"id": 3, "title": "Последний бой"}]
    llm = FakeLLM({"picked": [{"id": 3}, {"id": 99}, {"id": 2}]})
    got = content_check.pick_by_scenes(llm, "Брестская крепость", [{"name": "Начало обороны", "what_to_cut": "первые минуты"}], cands, 2)
    assert [c["id"] for c in got] == [3, 2]
    assert content_check.pick_by_scenes(llm, "x", [], cands, 2) == []


def test_verdict_requires_russian_speech_and_real_title_match():
    names = ["суперсемеика", "the incredibles"]
    film = {"title": "Суперсемейка (2004) полный фильм", "label": "Суперсемейка (2004) полный фильм"}
    v = {"is_full": True, "trust": 0.9, "language": "английский", "note": "Полный фильм на английском"}
    assert not sources.verdict_ok(v, names, film, russian_made=False)
    assert sources.verdict_ok(dict(v, language="русский дубляж", note="Полный фильм"), names, film, russian_made=False)
    other = {"title": "Полуночный экспресс 1978 полный фильм"}
    assert not sources.verdict_ok(dict(v, language="русский дубляж", trust=0.95, note="Полный фильм"), ["водитель"], other, russian_made=False)
    brest = {"title": "Брестская крепость (2010) фильм"}
    assert sources.verdict_ok({"is_full": True, "trust": 0.8, "language": "оригинал", "note": "Полный фильм"}, ["брестская крепость"], brest, russian_made=True)
    assert not sources.verdict_ok({"is_full": True, "trust": 0.8, "language": "оригинал", "note": "Полный фильм"}, ["брестская крепость"], brest, russian_made=False)
    assert not sources.verdict_ok({"is_full": True, "trust": 0.9, "language": "русский", "note": "стрим с фильмом"}, ["брестская крепость"], brest, russian_made=True)


def test_series_answer_counts_as_the_work_itself():
    res = content_check.check_transcript(FakeLLM({"is_work": True, "russian": True, "what": "series", "confidence": 0.9}), "Истинное образование", None, "series", RU)
    assert res["ok"] is True


def test_audiobook_and_thin_russian_voiceover_are_rejected():
    book = ["Уинстон Грум. Форрест Гамп. Роман. Перевод с английского."] + ["Тренер сказал, что я молодец, и спросил, хочу ли я играть."] * 60
    res = content_check.check_transcript(FakeLLM({"is_work": True, "russian": True, "what": "film", "confidence": 1.0}), "Форрест Гамп", 1994, "film", book)
    assert res["ok"] is False and "аудиокнига" in res["reason"]
    thin = content_check.check_transcript(FakeLLM({"is_work": True, "russian": True, "what": "series", "confidence": 0.9}), "Истинное образование", None,
                                          "series", RU, duration_sec=54 * 60 * 10)
    assert thin["ok"] is False and "слов в минуту" in thin["reason"]


def test_reversed_voice_is_rejected_by_asr_confidence():
    from kinotrends import content_check as cc
    good = [{"w": "слово", "p": 0.96}] * 400 + [{"w": "эээ", "p": 0.4}] * 4
    bad = [{"w": "базян", "p": 0.43}] * 150 + [{"w": "встань", "p": 0.7}] * 250
    assert cc.speech_quality(good)["ok"] is True
    q = cc.speech_quality(bad)
    assert q["ok"] is False and "перевёрнут" in q["reason"]
    assert cc.speech_quality([{"w": "x", "p": 0.2}] * 10)["ok"] is None  # мало слов — не решаем
    sents = ["Встань. Ну наем что умее. месе свои лицоми."] * 40
    res = cc.check_transcript(None, "Мегамозг", 2010, "animated", sents, duration_sec=5735, words=bad)
    assert res["ok"] is False and res["what"] == "garbled"


def test_resolution_gate():
    from kinotrends import content_check as cc
    assert cc.check_resolution(368, 208)["ok"] is False
    assert cc.check_resolution(1280, 720)["ok"] is True
    assert cc.check_resolution(None, None)["ok"] is None


def test_frames_check_rejects_game_and_mirror():
    from kinotrends import content_check as cc

    class FakeLLM:
        configured, model = True, "fake"

        def __init__(self, data):
            self.data = data

        def ask_json(self, system, user, image=None, max_tokens=0):
            assert image == b"jpg" and "раскадровк" in user
            return self.data, None

    game = cc.check_frames(FakeLLM({"is_work": False, "kind": "game", "confidence": 0.9}), "Симпсоны", 1989, "animated_series", b"jpg")
    assert game["ok"] is False and "игр" in game["reason"]
    mirror = cc.check_frames(FakeLLM({"is_work": True, "kind": "film", "mirrored": True, "confidence": 0.8}), "Мегамозг", 2010, "animated", b"jpg")
    assert mirror["ok"] is False and "зеркал" in mirror["reason"]
    fine = cc.check_frames(FakeLLM({"is_work": True, "kind": "film", "confidence": 0.9}), "Мегамозг", 2010, "animated", b"jpg")
    assert fine["ok"] is True
    unsure = cc.check_frames(FakeLLM({"is_work": False, "kind": "other", "confidence": 0.3}), "X", None, None, b"jpg")
    assert unsure["ok"] is True  # модель не уверена — не браковать


def test_frames_check_passes_when_model_safety_filter_refuses():
    from kinotrends import content_check as cc
    from kinotrends.llm import LLMError

    class Blocked:
        configured, model = True, "fake"

        def ask_json(self, *a, **k):
            raise LLMError("нет choices: {'error': {'message': 'Gemini blocked the request: PROHIBITED_CONTENT'}}")

    res = cc.check_frames(Blocked(), "Теория большого взрыва", 2007, "series", b"jpg")
    assert res["ok"] is True and res.get("blocked") is True

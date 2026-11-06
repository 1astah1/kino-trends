"""Подгонка границ ролика под проверку длины (QC 20–58 с): fit_clip_bounds."""

from __future__ import annotations

from types import SimpleNamespace

from autoshorts.services.clipping import CLIP_FIT_MAX_SEC, CLIP_FIT_MIN_SEC, fit_clip_bounds


def tr(spans: list[tuple[float, float]], duration: float = 600.0) -> SimpleNamespace:
    sents = [SimpleNamespace(sid=i, s=s, e=e, text="", w0=i, w1=i) for i, (s, e) in enumerate(spans)]
    return SimpleNamespace(sentences=sents, duration=duration)


# фразы по 4 с с паузой 0,5 с, начиная с 100 с
SENTS = [(100 + i * 4.5, 104 + i * 4.5) for i in range(40)]


def test_ok_length_untouched() -> None:
    assert fit_clip_bounds(tr(SENTS), 100.0, 130.0) is None


def test_short_clip_at_source_start_extends_forward_by_whole_sentences() -> None:
    s, e = fit_clip_bounds(tr(SENTS), 100.0, 117.0)  # раньше первой фразы брать нечего — только вперёд
    assert s == 100.0 and e - s >= CLIP_FIT_MIN_SEC
    assert any(abs(e - (se + 0.25)) < 1e-6 for _, se in SENTS)  # конец — на конце фразы


def test_short_clip_at_source_end_extends_backward() -> None:
    t = tr(SENTS, duration=SENTS[-1][1] + 1)
    last_s, last_e = SENTS[-1]
    s, e = fit_clip_bounds(t, last_s - 10, last_e + 0.25, source_duration=t.duration)
    assert e - s >= CLIP_FIT_MIN_SEC and s < last_s - 10


def test_long_clip_trimmed_from_start_keeping_payoff() -> None:
    """Развязка в конце сцены: длинную укорачиваем с начала, конец не трогаем."""
    s, e = fit_clip_bounds(tr(SENTS), 100.0, 175.0)
    assert e == 175.0 and CLIP_FIT_MIN_SEC <= e - s <= CLIP_FIT_MAX_SEC
    assert any(abs(s - (ss - 0.25)) < 1e-6 for ss, _ in SENTS)  # начало — на начале фразы


def test_short_clip_takes_setup_before_extending_end() -> None:
    s, e = fit_clip_bounds(tr(SENTS), 120.0, 135.0)
    assert e == 135.0 and s < 120.0 and e - s >= CLIP_FIT_MIN_SEC


def test_without_transcript_uses_seconds() -> None:
    s, e = fit_clip_bounds(None, 50.0, 60.0, source_duration=1000.0)
    assert (s, e) == (50.0, 50.0 + CLIP_FIT_MIN_SEC)

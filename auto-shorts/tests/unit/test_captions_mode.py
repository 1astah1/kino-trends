"""Режим субтитров слоя автора доходит до рендера: дорожки Source/Author включаются по `captions`,
а при `none` полоса субтитров не блокирует заголовок и тезисы."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from autoshorts.clipping.captions import BANNER_AREA, CaptionLayout, Zone, captions_from_edl
from autoshorts.clipping.transcript import Word


def _edl() -> SimpleNamespace:
    src = [Word("исходник", 0.0, 0.4), Word("говорит", 0.5, 0.9)]
    vo = [Word("автор", 1.0, 1.4), Word("комментирует", 1.5, 2.0)]
    return SimpleNamespace(
        texts=[SimpleNamespace(text="Заголовок", start=0.0, end=2.0, kind="title")],
        source_words=lambda words, hide_under_vo=True: src,
        author_words=lambda: vo,
    )


def _styles(path: Path) -> set[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return {line.split(",")[3] for line in lines if line.startswith("Dialogue:")}


def test_captions_mode_selects_tracks(tmp_path: Path) -> None:
    both = _styles(captions_from_edl(_edl(), [], tmp_path / "both.ass", mode="both"))
    assert {"Source", "Author", "Title"} <= both
    author = _styles(captions_from_edl(_edl(), [], tmp_path / "author.ass", mode="author"))
    assert "Author" in author and "Source" not in author and "Title" in author
    none = _styles(captions_from_edl(_edl(), [], tmp_path / "none.ass", mode="none"))
    assert "Source" not in none and "Author" not in none and "Title" in none


def test_avoiding_frees_captions_band_when_captions_off() -> None:
    layout = CaptionLayout(title_zone=Zone(65, 560, 895, 720), onscreen_zone=Zone(65, 740, 895, 900))
    blocked, _ = layout.avoiding([BANNER_AREA])
    assert blocked.onscreen_zone.y1 != 740  # полоса субтитров занята → тезисы уехали
    free, moved = layout.avoiding([BANNER_AREA], block_captions=False)
    assert free.onscreen_zone == Zone(65, 740, 895, 900) and not moved

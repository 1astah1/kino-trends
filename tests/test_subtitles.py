"""Проверка сборки ASS-субтитров."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from autoshorts.models import WordTiming
from autoshorts.pipeline.subtitles import _ass_time, _hex_to_ass_color, build_karaoke_ass


def test_ass_time_format():
    assert _ass_time(0) == "0:00:00.00"
    assert _ass_time(65.5) == "0:01:05.50"


def test_hex_to_ass_color_is_bgr():
    # '#F2C14E' -> R=F2 G=C1 B=4E -> ASS хранит BGR: &H004EC1F2
    assert _hex_to_ass_color("#F2C14E") == "&H004EC1F2"


def test_karaoke_ass_covers_all_words(tmp_path):
    words = [
        WordTiming(word="Три", start_sec=0.0, end_sec=0.3),
        WordTiming(word="карты", start_sec=0.35, end_sec=0.7),
    ]
    out = build_karaoke_ass(words, tmp_path / "test.ass")
    content = out.read_text(encoding="utf-8")
    assert "Три" in content
    assert "карты" in content
    assert "[Events]" in content
    assert content.count("Dialogue:") >= len(words)  # база + подсветка на каждое слово

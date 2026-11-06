"""Служебный текст баннера в Pillow: кириллица, перенос, автокегль, обрезка, кеш, прозрачность."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from autoshorts.domain.banner_schema import TextAlign, TextStyle
from autoshorts.pipeline import banner_text as bt

CODE_STYLE = TextStyle(font_file="Montserrat-Bold.ttf", max_font_px=36, min_font_px=24, bg_opacity=0.6)
PLATE_STYLE = TextStyle(
    font_file="Montserrat-Bold.ttf",
    max_font_px=66,
    min_font_px=44,
    bg_opacity=0.7,
    max_lines=2,
    hug_text=False,
    padding_x=28,
    padding_y=18,
)


def test_code_layer_renders_and_hugs_text(tmp_path: Path) -> None:
    res = bt.render_text_layer("u.umka.chat/k7m2q", CODE_STYLE, (825, 50), cache_dir=tmp_path)
    assert res.path.is_file() and not res.truncated
    assert res.width < 825 and res.height <= 50
    assert res.font_px <= 36 and res.lines == ["u.umka.chat/k7m2q"]
    with Image.open(res.path) as im:
        assert im.mode == "RGBA" and im.size == (res.width, res.height)
        # подложка полупрозрачная, углы скруглены — угол прозрачнее центра подложки
        a_corner = im.getpixel((0, 0))[3]
        a_mid = im.getpixel((3, res.height // 2))[3]
        assert a_corner < a_mid
        assert abs(a_mid - round(0.6 * 255)) <= 2


def test_cache_hit_and_manifest_changes(tmp_path: Path) -> None:
    a = bt.render_text_layer("u.umka.chat/k7m2q", CODE_STYLE, (825, 50), cache_dir=tmp_path)
    b = bt.render_text_layer("u.umka.chat/k7m2q", CODE_STYLE, (825, 50), cache_dir=tmp_path)
    assert b.cached and b.path == a.path and b.manifest_sha == a.manifest_sha
    c = bt.render_text_layer("u.umka.chat/zz999", CODE_STYLE, (825, 50), cache_dir=tmp_path)
    d = bt.render_text_layer("u.umka.chat/k7m2q", CODE_STYLE, (825, 50), cache_dir=tmp_path, scale=0.5)
    e = bt.render_text_layer(
        "u.umka.chat/k7m2q", CODE_STYLE.model_copy(update={"color": "#FFEE00"}), (825, 50), cache_dir=tmp_path
    )
    assert len({a.manifest_sha, c.manifest_sha, d.manifest_sha, e.manifest_sha}) == 4
    assert d.font_px < a.font_px


def test_cyrillic_plate_wraps_balanced_without_hanging_prepositions(tmp_path: Path) -> None:
    text = "Umka — все нейросети в одном месте и без лишних подписок"
    res = bt.render_text_layer(text, PLATE_STYLE, (825, 190), cache_dir=tmp_path)
    assert len(res.lines) == 2 and not res.truncated
    for line in res.lines:
        last = line.replace(bt.NBSP, " ").split()[-1].lower()
        assert last not in {"в", "и", "без", "на"}, res.lines
    with Image.open(res.path) as im:
        assert im.size == (825, 190)  # плашка во весь слот (hug_text=False)


def test_auto_font_size_goes_down_for_longer_text(tmp_path: Path) -> None:
    style = TextStyle(max_font_px=66, min_font_px=30, max_lines=1)
    short = bt.render_text_layer("Umka", style, (825, 110), cache_dir=tmp_path)
    long = bt.render_text_layer("Umka — все нейросети в одном месте", style, (825, 110), cache_dir=tmp_path)
    assert short.font_px == 66 and 30 <= long.font_px < 66 and not long.truncated


def test_truncates_with_ellipsis_at_min_size(tmp_path: Path) -> None:
    style = TextStyle(max_font_px=40, min_font_px=36, max_lines=1)
    res = bt.render_text_layer(
        "Очень длинный текст, который никак не поместится в узкий слот", style, (300, 60), cache_dir=tmp_path
    )
    assert res.truncated and res.font_px == 36 and res.lines[0].endswith(bt.ELLIPSIS)


def test_scale_applies_to_font_and_padding(tmp_path: Path) -> None:
    full = bt.render_text_layer("umka.chat", CODE_STYLE, (825, 50), cache_dir=tmp_path)
    small = bt.render_text_layer("umka.chat", CODE_STYLE, (244, 15), cache_dir=tmp_path, scale=568 / 1920)
    assert small.font_px == pytest.approx(full.font_px * 568 / 1920, abs=2)
    assert small.height <= 15


def test_opacity_multiplies_alpha(tmp_path: Path) -> None:
    style = TextStyle(bg_opacity=1.0, corner_radius_px=0)
    solid = bt.render_text_layer("umka.chat", style, (400, 60), cache_dir=tmp_path)
    half = bt.render_text_layer("umka.chat", style, (400, 60), cache_dir=tmp_path, opacity=0.5)
    with Image.open(solid.path) as a, Image.open(half.path) as b:
        assert a.getpixel((1, 1))[3] == 255 and abs(b.getpixel((1, 1))[3] - 128) <= 1


def test_text_without_fringe_on_transparent(tmp_path: Path) -> None:
    style = TextStyle(bg_opacity=0.0, color="#FFFFFF", align=TextAlign.LEFT)
    res = bt.render_text_layer("Реклама · erid 2VtzqvAbCd", style, (600, 50), cache_dir=tmp_path)
    with Image.open(res.path) as im:
        px = [p for p in im.get_flattened_data() if 0 < p[3] < 255]
        assert px, "ожидались полупрозрачные пиксели сглаживания"
        assert all(min(p[:3]) >= 250 for p in px)  # края белые, а не серые


def test_fill_placeholders() -> None:
    assert bt.fill_placeholders("{code_url}", code_url="u.umka.chat/k7m2q") == "u.umka.chat/k7m2q"
    assert bt.fill_placeholders("Реклама · erid {erid}", erid="2Vtzqv") == "Реклама · erid 2Vtzqv"
    assert bt.fill_placeholders("Реклама · erid {erid}") is None
    assert bt.fill_placeholders("{unknown}") is None
    assert bt.fill_placeholders("umka.chat") == "umka.chat"
    assert bt.code_url_from_template("u.umka.chat/{code}", "k7m2q") == "u.umka.chat/k7m2q"
    assert bt.code_url_from_template("u.umka.chat/{code}", None) is None


def test_wrap_text_tokens_bind_short_words() -> None:
    toks = bt._tokens("нейросети в одном месте и без подписок")
    assert f"в{bt.NBSP}одном" in toks and f"и{bt.NBSP}без{bt.NBSP}подписок" in toks


def test_missing_font_and_empty_text(tmp_path: Path) -> None:
    with pytest.raises(bt.TextLayerError):
        bt.render_text_layer("x", TextStyle(font_file="Nope-Bold.ttf"), (100, 40), cache_dir=tmp_path)
    with pytest.raises(bt.TextLayerError):
        bt.render_text_layer("   ", CODE_STYLE, (100, 40), cache_dir=tmp_path)


def test_fonts_are_local() -> None:
    assert bt.default_fonts_dir() == bt.PROJECT_ROOT / "config" / "fonts"
    assert (bt.default_fonts_dir() / "Montserrat-Bold.ttf").is_file()

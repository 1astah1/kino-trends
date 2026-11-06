"""Серверная проверка баннера: коды предупреждений по safe zone, размеру, анимации, erid, субтитрам."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from autoshorts.domain.banner_schema import (
    AdMarking,
    Anchor,
    BannerCreative,
    BannerLayer,
    BannerLayerKind,
    BannerSlot,
    BannerValidationCode,
    CreativeKind,
    CreativeSourceFormat,
    FitMode,
    LayerShow,
    Rect,
    SafeZoneCatalog,
    WarningSeverity,
    default_banner_variant,
)
from autoshorts.pipeline import banner_assets as ba
from autoshorts.pipeline import banner_place as bp
from autoshorts.pipeline import banner_validate as bv

SHA = "b" * 64
C = BannerValidationCode


def creative(w: int = 900, h: int = 220, *, kind=CreativeKind.STATIC, duration: float = 0.0, sha: str = SHA):
    animated = kind == CreativeKind.ANIMATED
    return BannerCreative(
        sha256=sha,
        kind=kind,
        source_format=CreativeSourceFormat.GIF if animated else CreativeSourceFormat.PNG,
        width=w,
        height=h,
        source_width=w,
        source_height=h,
        has_alpha=True,
        duration_sec=duration,
        fps=30.0 if animated else 0.0,
        frame_count=max(1, round(duration * 30)),
        loop=True,
        source_bytes=1000,
        normalized_path=f"banners/creatives/{sha[:2]}/{sha}/creative.{'webm' if animated else 'png'}",
        normalized_mime="video/webm" if animated else "image/png",
        thumb_path=f"banners/creatives/{sha[:2]}/{sha}/thumb.png",
        first_frame_path=f"banners/creatives/{sha[:2]}/{sha}/first_frame.png",
    )


@pytest.fixture(scope="module")
def catalog() -> SafeZoneCatalog:
    return bp.load_safe_zone_catalog()


@pytest.fixture(scope="module")
def profiles(catalog: SafeZoneCatalog):
    return [catalog.get("common_organic"), catalog.get("youtube_shorts"), catalog.get("vk_clips")]


def with_creative_slot(slot: BannerSlot, **kw):
    v = default_banner_variant(creative_sha256=SHA, **kw)
    layers = [la.model_copy(update={"slot": slot}) if la.id == "creative" else la for la in v.layers]
    return v.model_copy(update={"layers": layers})


def codes(res) -> set[str]:
    return {w.code.value for w in res.warnings}


def test_default_variant_is_clean(profiles) -> None:
    v = default_banner_variant(creative_sha256=SHA, erid="2VtzqvAbCd")
    res = bv.validate_variant(v, {SHA: creative()}, profiles=profiles, duration_sec=21.4)
    assert res.ok
    assert not [w for w in res.warnings if w.severity != WarningSeverity.INFO], res.warnings
    assert res.captions_zone.fits and len(res.windows) == 2


def test_bottom_dead_zone(profiles) -> None:
    v = with_creative_slot(BannerSlot(x=65, y=1450, w=825, h=250, anchor=Anchor.TOP_CENTER))
    res = bv.validate_variant(v, {SHA: creative()}, profiles=profiles)
    hits = [w for w in res.warnings if w.code == C.CREATIVE_IN_BOTTOM_DEAD_ZONE and w.layer_id == "creative"]
    assert {w.profile_id for w in hits} == {"common_organic", "youtube_shorts", "vk_clips"}  # y2=1652 > 1536
    assert hits[0].details["overflow_px"] > 0


def test_outside_safe_zone_right(profiles) -> None:
    v = with_creative_slot(BannerSlot(x=300, y=1120, w=760, h=190))
    res = bv.validate_variant(v, {SHA: creative()}, profiles=profiles[:1])
    w = next(w for w in res.warnings if w.code == C.CREATIVE_OUTSIDE_SAFE_ZONE and w.layer_id == "creative")
    assert "right" in w.details["overflow"] and w.profile_id == "common_organic"


def test_micro_brand_in_top_bar(profiles) -> None:
    v = default_banner_variant(creative_sha256=SHA)
    layers = [
        la.model_copy(update={"slot": la.slot.model_copy(update={"y": 56})}) if la.id == "micro_brand" else la
        for la in v.layers
    ]
    res = bv.validate_variant(v.model_copy(update={"layers": layers}), {SHA: creative()}, profiles=profiles)
    assert C.MICRO_BRAND_IN_TOP_BAR.value in codes(res)


def test_too_small_and_upscaled(profiles) -> None:
    small = with_creative_slot(BannerSlot(x=65, y=1120, w=200, h=60))
    res = bv.validate_variant(small, {SHA: creative()}, profiles=profiles[:1])
    assert C.CREATIVE_TOO_SMALL_TO_READ.value in codes(res)
    blurry = default_banner_variant(creative_sha256=SHA)
    res2 = bv.validate_variant(blurry, {SHA: creative(300, 80)}, profiles=profiles[:1])
    w = next(w for w in res2.warnings if w.code == C.CREATIVE_UPSCALED_BLURRY)
    assert w.details["upscale_factor"] > 1.5


def test_contain_never_reports_distortion(profiles) -> None:
    for w, h in [(900, 220), (1080, 1080), (123, 457), (4096, 100), (64, 64)]:
        v = default_banner_variant(creative_sha256=SHA)
        res = bv.validate_variant(v, {SHA: creative(w, h)}, profiles=profiles[:1])
        assert C.CREATIVE_ASPECT_DISTORTED.value not in codes(res)


def test_cover_reports_clipping_info(profiles) -> None:
    v = with_creative_slot(BannerSlot(x=65, y=1120, w=825, h=250, fit=FitMode.COVER))
    res = bv.validate_variant(v, {SHA: creative(400, 400)}, profiles=profiles[:1])
    w = next(w for w in res.warnings if w.code == C.CREATIVE_CLIPPED_BY_SLOT)
    assert w.severity == WarningSeverity.INFO


def test_animated_too_long_and_cut_by_window(profiles) -> None:
    v = default_banner_variant(creative_sha256=SHA)
    long = creative(kind=CreativeKind.ANIMATED, duration=20.0)
    res = bv.validate_variant(v, {SHA: long}, profiles=profiles[:1], duration_sec=30)
    assert C.ANIMATED_TOO_LONG.value in codes(res)
    mid = creative(kind=CreativeKind.ANIMATED, duration=4.5)
    res2 = bv.validate_variant(v, {SHA: mid}, profiles=profiles[:1], duration_sec=30)
    assert C.ANIMATED_CUT_BY_WINDOW.value in codes(res2)  # финальный повтор 2,5 с короче анимации
    assert C.ANIMATED_TOO_LONG.value not in codes(res2)


def test_erid_required_missing_is_error(profiles) -> None:
    v = default_banner_variant(creative_sha256=SHA, ad_marking_required=True)
    res = bv.validate_variant(v, {SHA: creative()}, profiles=profiles[:1])
    w = next(w for w in res.warnings if w.code == C.ERID_MISSING_WHEN_REQUIRED)
    assert w.severity == WarningSeverity.ERROR and not res.ok
    no_layer = v.model_copy(
        update={
            "layers": [la for la in v.layers if la.kind != BannerLayerKind.TEXT_ERID],
            "ad_marking": AdMarking(required=True, erid="2VtzqvAbCd"),
        }
    )
    res2 = bv.validate_variant(no_layer, {SHA: creative()}, profiles=profiles[:1])
    assert C.ERID_MISSING_WHEN_REQUIRED.value in codes(res2) and not res2.ok
    ok = default_banner_variant(creative_sha256=SHA, ad_marking_required=True, erid="2VtzqvAbCd")
    assert bv.validate_variant(ok, {SHA: creative()}, profiles=profiles[:1]).ok


def test_banner_overlaps_given_captions_zone(profiles) -> None:
    v = default_banner_variant(creative_sha256=SHA)
    legacy_captions = Rect(x=60, y=1000, w=960, h=200)  # как если бы субтитры стояли по старому margin_v
    res = bv.validate_variant(v, {SHA: creative()}, profiles=profiles[:1], captions_zone=legacy_captions)
    hit = [w for w in res.warnings if w.code == C.BANNER_OVERLAPS_CAPTIONS_ZONE]
    assert hit and hit[0].layer_id == "creative"


def test_captions_zone_too_small(profiles) -> None:
    v = with_creative_slot(BannerSlot(x=65, y=300, w=825, h=1000))
    res = bv.validate_variant(v, {SHA: creative(825, 1000)}, profiles=profiles[:1])
    assert C.CAPTIONS_ZONE_TOO_SMALL.value in codes(res)


def test_missing_creative_falls_back(profiles) -> None:
    v = default_banner_variant(creative_sha256=SHA)
    res = bv.validate_variant(v, {}, profiles=profiles[:1])
    assert {C.CREATIVE_MISSING.value, C.FALLBACK_PLATE_USED.value} <= codes(res)


def test_layer_outside_canvas(profiles) -> None:
    v = with_creative_slot(BannerSlot(x=700, y=1120, w=600, h=190, fit=FitMode.NONE, clip_to_slot=False))
    res = bv.validate_variant(v, {SHA: creative()}, profiles=profiles[:1])
    assert C.LAYER_OUTSIDE_CANVAS.value in codes(res)


def test_persistent_creative_logo_in_micro_brand_slot(profiles) -> None:
    v = default_banner_variant(creative_sha256=SHA)
    logo_sha = "c" * 64
    layers = [
        BannerLayer(
            id="logo",
            kind=BannerLayerKind.MICRO_BRAND,
            show=LayerShow.PERSISTENT,
            creative_sha256=logo_sha,
            slot=BannerSlot(x=65, y=200, w=200, h=60, anchor=Anchor.TOP_LEFT),
        ),
        *[la for la in v.layers if la.id != "micro_brand"],
    ]
    res = bv.validate_variant(
        v.model_copy(update={"layers": layers}),
        {SHA: creative(), logo_sha: creative(400, 120, sha=logo_sha)},
        profiles=profiles[:1],
    )
    assert res.placement.by_id("logo") is not None and res.ok


def test_warnings_sorted_by_severity(profiles) -> None:
    v = with_creative_slot(BannerSlot(x=65, y=1450, w=825, h=250), ad_marking_required=True)
    res = bv.validate_variant(v, {SHA: creative()}, profiles=profiles)
    order = [w.severity for w in res.warnings]
    rank = {WarningSeverity.ERROR: 0, WarningSeverity.WARN: 1, WarningSeverity.INFO: 2}
    assert order == sorted(order, key=rank.get)


# ---------------------------------------------------------------- до приёма файла


def test_validate_upload_codes(tmp_path: Path) -> None:
    big = tmp_path / "big.png"
    Image.new("RGBA", (64, 64), (1, 2, 3, 4)).save(big)
    w = bv.validate_upload(big, ba.AssetLimits(max_static_bytes=10))
    assert [x.code for x in w] == [C.FILE_TOO_LARGE] and w[0].severity == WarningSeverity.ERROR
    txt = tmp_path / "x.gif"
    txt.write_text("hello", encoding="utf-8")
    w2 = bv.validate_upload(txt)
    assert [x.code for x in w2] == [C.UNSUPPORTED_FORMAT]
    ok = tmp_path / "ok.png"
    Image.new("RGBA", (64, 64)).save(ok)
    assert bv.validate_upload(ok) == []

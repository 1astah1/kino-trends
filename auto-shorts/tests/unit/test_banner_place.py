"""Геометрия баннера: вписывание, якоря, safe zones, масштаб выхода, зона субтитров."""

from __future__ import annotations

import json

import pytest

from autoshorts.domain.banner_schema import (
    Anchor,
    BannerLayer,
    BannerLayerKind,
    BannerSlot,
    FitMode,
    Rect,
    SafeZoneCatalog,
    SafeZoneStatus,
    TextAttach,
    default_banner_variant,
)
from autoshorts.pipeline import banner_place as bp

SHA = "a" * 64


@pytest.fixture(scope="module")
def catalog() -> SafeZoneCatalog:
    return bp.load_safe_zone_catalog()


@pytest.fixture(scope="module")
def common(catalog: SafeZoneCatalog):
    return catalog.get("common_organic")


# ---------------------------------------------------------------- safe_zones.json


def test_safe_zones_json_profiles_match_research(catalog: SafeZoneCatalog) -> None:
    ids = {p.id for p in catalog.profiles}
    assert ids == {
        "common_organic",
        "youtube_shorts",
        "instagram_reels",
        "tiktok",
        "vk_clips",
        "meta_reels_ads",
        "google_vertical_ads",
    }
    assert catalog.default_profile == "common_organic"
    c = catalog.get("common_organic").safe
    assert (c.x1, c.y1, c.x2, c.y2) == (65, 192, 895, 1380)
    offer = catalog.get("common_organic").offer_area
    assert offer is not None and (offer.x1, offer.y1, offer.x2, offer.y2) == (65, 1120, 890, 1370)
    assert catalog.get("meta_reels_ads").status == SafeZoneStatus.OFFICIAL
    assert catalog.get("google_vertical_ads").safe.x2 == 888
    assert catalog.get("vk_clips").status == SafeZoneStatus.UNCONFIRMED
    for p in catalog.profiles:
        assert p.sources, p.id
        if p.status == SafeZoneStatus.OFFICIAL:
            assert all(s.url and s.url.startswith("https://") for s in p.sources)


def test_safe_zone_margins_consistent(catalog: SafeZoneCatalog) -> None:
    for p in catalog.profiles:
        m, s = p.margins, p.safe
        if m.top is not None:
            assert m.top == s.y1, p.id
        if m.bottom is not None:
            assert m.bottom == p.canvas_h - s.y2, p.id
        if m.left is not None:
            assert m.left == s.x1, p.id
        if m.right is not None:
            assert m.right == p.canvas_w - s.x2, p.id
        if p.offer_area is not None:
            assert (
                p.safe.rect.contains(p.offer_area.rect) or p.id == "common_organic" or p.offer_area.y2 <= s.y2
            )


def test_safe_zone_json_keys_are_latin() -> None:
    data = json.loads(bp.DEFAULT_SAFE_ZONES_PATH.read_text(encoding="utf-8"))

    def keys(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                yield k
                yield from keys(v)
        elif isinstance(obj, list):
            for v in obj:
                yield from keys(v)

    assert all(k.isascii() for k in keys(data))


def test_dead_zones_cover_outside(common) -> None:
    dz = common.dead_zones()
    assert dz["top_bar"] == Rect(x=0, y=0, w=1080, h=192)
    assert dz["bottom_ui"].y == 1380 and dz["right_rail"].x == 895


def test_platform_profile_mapping(catalog: SafeZoneCatalog) -> None:
    assert bp.profile_for_platform("youtube", catalog).id == "youtube_shorts"
    assert bp.profile_for_platform("vk", catalog).id == "vk_clips"


# ---------------------------------------------------------------- вписывание


def test_contain_width_limited_bottom_anchor() -> None:
    slot = BannerSlot(x=65, y=1120, w=825, h=250, anchor=Anchor.BOTTOM_CENTER)
    fit = bp.fit_into_slot(900, 220, slot)
    assert fit.rect.w == 825 and fit.rect.h == 202  # 220 × 825/900 = 201.67
    assert fit.rect.y2 == 1370 and fit.rect.x == 65
    assert not fit.clipped and fit.src_crop == Rect(x=0, y=0, w=900, h=220)
    assert fit.aspect_error < 0.005


def test_contain_height_limited_centered() -> None:
    slot = BannerSlot(x=0, y=0, w=800, h=100)
    fit = bp.fit_into_slot(400, 400, slot)
    assert (fit.rect.w, fit.rect.h) == (100, 100)
    assert fit.rect.x == 350 and fit.rect.y == 0
    assert fit.scale == pytest.approx(0.25)


@pytest.mark.parametrize(
    ("anchor", "xy"),
    [
        (Anchor.TOP_LEFT, (0, 0)),
        (Anchor.TOP_RIGHT, (700, 0)),
        (Anchor.BOTTOM_LEFT, (0, 0)),
        (Anchor.CENTER_RIGHT, (700, 0)),
        (Anchor.BOTTOM_CENTER, (350, 0)),
    ],
)
def test_anchors(anchor: Anchor, xy: tuple[int, int]) -> None:
    fit = bp.fit_into_slot(100, 100, BannerSlot(x=0, y=0, w=800, h=100, anchor=anchor))
    assert (fit.rect.x, fit.rect.y) == xy


def test_contain_never_distorts_or_overflows() -> None:
    for sw, sh in [(900, 220), (1, 1), (4096, 16), (16, 4096), (333, 777), (1080, 1920), (901, 223)]:
        for w, h in [(825, 250), (825, 190), (100, 37), (1080, 1920), (17, 999)]:
            slot = BannerSlot(x=0, y=0, w=w, h=h)
            fit = bp.fit_into_slot(sw, sh, slot)
            assert fit.rect.w <= w and fit.rect.h <= h, (sw, sh, w, h)
            assert slot.rect.contains(fit.rect)
            assert not fit.clipped
            if min(fit.rect.w, fit.rect.h) < 2:
                continue  # вырожденный случай: сторона меньше пикселя зажата до 1 px
            rel = abs(fit.rect.w / fit.rect.h - sw / sh) / (sw / sh)
            assert rel <= 1.0 / min(fit.rect.w, fit.rect.h) + 1e-9, (sw, sh, w, h, fit.rect)


def test_scale_and_offset_clip_to_slot() -> None:
    slot = BannerSlot(x=100, y=100, w=400, h=100, scale=1.5, offset_x=30)
    fit = bp.fit_into_slot(400, 100, slot)
    assert fit.full_rect.w == 600 and fit.full_rect.h == 150
    assert fit.clipped and slot.rect.contains(fit.rect)
    # видимая часть соответствует обрезке исходника в той же пропорции
    assert fit.src_crop.w == pytest.approx(fit.rect.w / fit.scale, abs=1)
    assert fit.src_crop.h == pytest.approx(fit.rect.h / fit.scale, abs=1)


def test_cover_fills_slot_and_crops_source() -> None:
    slot = BannerSlot(x=0, y=0, w=800, h=200, fit=FitMode.COVER)
    fit = bp.fit_into_slot(400, 400, slot)
    assert fit.rect == Rect(x=0, y=0, w=800, h=200)
    assert fit.clipped
    assert fit.src_crop.w == 400 and fit.src_crop.h == 100 and fit.src_crop.y == 150
    assert fit.aspect_error < 0.01


def test_none_keeps_native_size() -> None:
    fit = bp.fit_into_slot(
        300, 60, BannerSlot(x=0, y=0, w=800, h=200, fit=FitMode.NONE, anchor=Anchor.TOP_LEFT)
    )
    assert (fit.rect.w, fit.rect.h) == (300, 60) and fit.scale == 1.0


def test_canvas_clipping_is_reported() -> None:
    slot = BannerSlot(x=900, y=100, w=400, h=100, anchor=Anchor.TOP_LEFT, fit=FitMode.NONE)
    fit = bp.fit_into_slot(400, 100, slot)
    assert fit.canvas_clipped and fit.rect.x2 == 1080
    off = bp.fit_into_slot(100, 100, BannerSlot(x=1200, y=100, w=100, h=100))
    assert not off.visible


# ---------------------------------------------------------------- safe zone


def test_check_safe_zone_overflow(common) -> None:
    inside = bp.check_safe_zone(Rect(x=65, y=1120, w=825, h=250), common)
    assert inside.inside and inside.outside_fraction == 0
    low = bp.check_safe_zone(Rect(x=65, y=1300, w=825, h=250), common)
    assert not low.inside and low.overflow["bottom"] == 170 and low.sides == ["bottom"]
    right = bp.check_safe_zone(Rect(x=500, y=500, w=500, h=100), common)
    assert right.overflow["right"] == 105 and 0 < right.outside_fraction < 1


def test_clamp_into_safe_zone(common) -> None:
    moved, changed = bp.clamp_into_safe_zone(Rect(x=65, y=1400, w=825, h=250), common)
    assert changed and moved.y2 == 1380 and moved.h == 250
    shrunk, _ = bp.clamp_into_safe_zone(Rect(x=0, y=0, w=1080, h=1920), common)
    assert common.safe_rect.contains(shrunk)
    assert shrunk.w / shrunk.h == pytest.approx(1080 / 1920, rel=0.01)
    slot = bp.clamp_slot_into_safe_zone(BannerSlot(x=65, y=1500, w=825, h=200), common)
    assert slot.y + slot.h <= 1380


# ---------------------------------------------------------------- масштаб выхода


def test_to_output_identity_and_scaled() -> None:
    r = Rect(x=65, y=1120, w=825, h=202)
    assert bp.to_output(r, 1080, 1920) == r
    half = bp.to_output(r, 540, 960)
    assert (half.x, half.y, half.w, half.h) == (32, 560, 412, 101) or abs(half.w - 412) <= 1
    tiny = bp.to_output(r, 320, 568)
    assert tiny.w == pytest.approx(825 * 568 / 1920, abs=1)
    assert tiny.w / tiny.h == pytest.approx(825 / 202, rel=0.02)
    assert 0 <= tiny.x and tiny.x2 <= 320 and tiny.y2 <= 568


# ---------------------------------------------------------------- зона субтитров


def test_captions_zone_avoids_banner(common) -> None:
    banner = Rect(x=89, y=1076, w=777, h=290)
    micro = Rect(x=65, y=200, w=300, h=58)
    zone = bp.compute_captions_zone([banner, micro], common)
    assert zone.fits
    assert not zone.rect.intersects(banner.inflate(bp.CAPTIONS_GAP_PX - 1))
    assert not zone.rect.intersects(micro)
    assert common.safe_rect.contains(zone.rect)
    assert zone.rect.y2 <= banner.y - bp.CAPTIONS_GAP_PX
    assert zone.ass_margin_v_bottom == 1920 - zone.rect.y2
    assert zone.ass_margin_l == 65 and zone.ass_margin_r == 1080 - 895


def test_captions_zone_moves_below_top_banner(common) -> None:
    top_banner = Rect(x=65, y=230, w=825, h=900)
    zone = bp.compute_captions_zone([top_banner], common)
    assert zone.fits and zone.rect.y >= top_banner.y2 + bp.CAPTIONS_GAP_PX


def test_captions_zone_too_small(common) -> None:
    huge = Rect(x=0, y=300, w=1080, h=1000)
    zone = bp.compute_captions_zone([huge], common, min_h=400)
    assert not zone.fits


# ---------------------------------------------------------------- вариант


def test_place_default_variant(common) -> None:
    v = default_banner_variant(creative_sha256=SHA, erid="2VtzqvAbCd")
    pl = bp.place_variant(v, {SHA: (900, 220)}, common)
    creative = pl.by_id("creative")
    code = pl.by_id("code")
    erid = pl.by_id("erid")
    assert creative and code and erid
    assert code.rect.y == creative.rect.y2 + 6  # код под креативом
    assert erid.rect.y2 == creative.rect.y - 6  # erid над креативом
    for p in pl.layers:
        assert common.safe_rect.contains(p.rect), p.layer_id
        assert not p.rect.intersects(pl.captions_zone.rect)
    assert not pl.fallback_used and pl.by_id("fallback_plate") is None


def test_place_variant_missing_creative_uses_fallback(common) -> None:
    v = default_banner_variant(creative_sha256=SHA)
    pl = bp.place_variant(v, {}, common)
    assert pl.fallback_used and pl.missing_creatives == [SHA]
    assert pl.by_id("fallback_plate") is not None and pl.by_id("creative") is None


def _set_enabled(variant, **flags: bool):
    layers = [la.model_copy(update={"enabled": flags.get(la.id, la.enabled)}) for la in variant.layers]
    return variant.model_copy(update={"layers": layers})


def test_disabled_creative_and_disabled_plate_mean_no_offer(common) -> None:
    """Контроль в A/B или снятый баннер: выключенная плашка не вшивается вместо выключенного креатива."""
    v = _set_enabled(default_banner_variant(creative_sha256=SHA), creative=False, fallback_plate=False)
    pl = bp.place_variant(v, {SHA: (900, 220)}, common)
    assert not pl.fallback_used
    assert pl.by_id("fallback_plate") is None and pl.by_id("creative") is None
    assert pl.by_id("micro_brand") is not None


def test_disabled_creative_uses_plate_only_if_plate_enabled(common) -> None:
    v = _set_enabled(default_banner_variant(creative_sha256=SHA), creative=False, fallback_plate=True)
    pl = bp.place_variant(v, {SHA: (900, 220)}, common)
    assert pl.fallback_used and pl.by_id("fallback_plate") is not None and pl.by_id("creative") is None


def test_no_creative_set_uses_enabled_plate(common) -> None:
    pl = bp.place_variant(default_banner_variant(creative_sha256=None), {}, common)
    assert pl.fallback_used and pl.by_id("fallback_plate") is not None and pl.missing_creatives == []


def test_attached_slots() -> None:
    anchor = Rect(x=100, y=1000, w=600, h=200)
    base = BannerSlot(x=0, y=0, w=400, h=40, anchor=Anchor.CENTER)
    below = BannerLayer(
        id="c",
        kind=BannerLayerKind.TEXT_CODE,
        text="x",
        slot=base,
        attach=TextAttach.BELOW_CREATIVE,
        attach_gap_px=5,
    )
    s = bp.attached_slot(below, anchor)
    assert (s.x, s.y, s.w) == (200, 1205, 400)
    inside = below.model_copy(update={"attach": TextAttach.INSIDE_BOTTOM})
    s2 = bp.attached_slot(inside, anchor)
    assert s2.y + s2.h == 1195 and s2.w == 400
    above = below.model_copy(update={"attach": TextAttach.ABOVE_CREATIVE})
    assert bp.attached_slot(above, anchor).y == 1000 - 5 - 40


def test_text_sizes_make_rect_tight(common) -> None:
    v = default_banner_variant(creative_sha256=SHA, erid="2VtzqvAbCd")
    pl = bp.place_variant(v, {SHA: (900, 220)}, common, text_sizes={"code": (300, 44)})
    code = pl.by_id("code")
    assert code is not None and code.rect.w == 300 and code.rect.h == 44
    assert code.slot_rect.w == 825

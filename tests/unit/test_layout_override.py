"""Ручная раскладка ролика (`clipping/layout_override.py`): схема, разбор JSON, сдвиг баннера с
вписыванием в safe zone, зоны текста, пересечения и применение в `produce_clip`."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from autoshorts.api.schemas.clipping import AuthorLayerInput, LayoutOverride
from autoshorts.clipping import layout_override as lo
from autoshorts.clipping.captions import CaptionLayout, Zone
from autoshorts.clipping.produce import ClipJobConfig, ClipSource, produce_clip
from autoshorts.clipping.transcript import Transcript, Word
from autoshorts.domain.banner_schema import BannerLayerKind, Rect, default_banner_variant
from autoshorts.pipeline.banner_place import load_safe_zone_catalog

SHA = "a" * 64
SIZES = {SHA: (1650, 380)}
CC = {
    "rights_type": "cc_by",
    "owner_name": "Канал автора",
    "proof_note": "лицензия на странице",
    "attribution_text": "«Лекция», Канал автора, CC BY 3.0",
    "license": "CC BY 3.0",
}


@pytest.fixture(scope="module")
def profile():
    return load_safe_zone_catalog().get("youtube_shorts")


@pytest.fixture(scope="module")
def variant():
    return default_banner_variant(creative_sha256=SHA)


# ---------------------------------------------------------------- схема и разбор


def test_schema_accepts_manual_and_rejects_out_of_canvas() -> None:
    ok = LayoutOverride(mode="manual", banner={"dx": 10, "dy": -400, "scale": 1.2}, captions_bottom=900)
    assert ok.banner is not None and ok.banner.dy == -400 and ok.title_top is None
    with pytest.raises(ValidationError):
        LayoutOverride(mode="manual", captions_bottom=2000)
    with pytest.raises(ValidationError):
        LayoutOverride(mode="manual", banner={"dx": 5000})
    with pytest.raises(ValidationError):
        LayoutOverride(mode="manual", banner={"scale": 3.0})
    with pytest.raises(ValidationError):
        LayoutOverride(mode="manual", bogus=1)
    layer = AuthorLayerInput(layout=ok).model_dump(mode="json")
    assert layer["layout"]["mode"] == "manual" and layer["layout"]["banner"]["scale"] == 1.2
    assert AuthorLayerInput().model_dump(mode="json")["layout"] is None


def test_spec_from_json_is_tolerant() -> None:
    assert lo.LayoutSpec.from_json(None) is None
    assert lo.LayoutSpec.from_json("manual") is None
    assert lo.LayoutSpec.from_json({"mode": "weird"}) is None
    spec = lo.LayoutSpec.from_json(
        {
            "mode": "manual",
            "banner": {"dx": 99999, "dy": -5, "scale": 9},
            "captions_bottom": -10,
            "title_top": 5000,
        }
    )
    assert spec is not None and spec.is_manual
    assert spec.banner == lo.BannerShift(dx=1080, dy=-5, scale=2.0)
    assert spec.captions_bottom == 0 and spec.title_top == 1920 and spec.onscreen_top is None
    auto = lo.LayoutSpec.from_json({"mode": "auto", "banner": {"dx": 100}, "captions_bottom": 700})
    assert (
        auto is not None
        and not auto.is_manual
        and not auto.has_banner_shift
        and not auto.has_caption_override
    )
    identity = lo.LayoutSpec.from_json({"mode": "manual", "banner": {"dx": 0, "dy": 0, "scale": 1}})
    assert identity is not None and not identity.has_banner_shift
    assert lo.LayoutSpec.coerce(spec) is spec
    assert lo.LayoutSpec.from_json(spec.to_json()) == spec


# ---------------------------------------------------------------- сдвиг баннера


def test_shift_moves_block_and_keeps_micro_brand(variant, profile) -> None:
    before = lo.block_rect(variant, SIZES, profile)
    assert before is not None and (before.y, before.y2) == (1120, 1366)
    res = lo.shift_variant(variant, lo.BannerShift(dx=0, dy=-400), profile, SIZES)
    assert res.block_after is not None and res.block_after.y == before.y - 400 and not res.clamped
    assert res.block_after.h == before.h and res.block_after.x == before.x
    by_id = {la.id: la for la in res.variant.layers}
    assert by_id["micro_brand"].slot == variant.layers[0].slot  # микробренд не трогаем
    assert by_id["creative"].slot.y == 1120 - 400
    # привязанный код ролика едет вместе с креативом: его слот не меняется, место — от креатива
    assert by_id["code"].slot.x == {la.id: la for la in variant.layers}["code"].slot.x


def test_shift_clamps_block_into_safe_zone(variant, profile) -> None:
    res = lo.shift_variant(variant, lo.BannerShift(dx=300, dy=900), profile, SIZES)
    assert res.clamped and res.notes == ["banner_clamped_to_safe_zone"]
    safe = profile.safe_rect
    assert res.block_after is not None
    assert res.block_after.y2 <= safe.y2 and res.block_after.x2 <= safe.x2 and res.block_after.x >= safe.x
    assert (
        res.block_after.y2 >= safe.y2 - 2 and res.block_after.h >= 240
    )  # прижат к низу целиком, а не улетел
    by_id = {la.id: la for la in res.variant.layers}
    assert by_id["creative"].slot.rect.y2 <= safe.y2  # креатив не уехал за кадр


def test_shift_scales_down_about_center(variant, profile) -> None:
    before = lo.block_rect(variant, SIZES, profile)
    assert before is not None
    res = lo.shift_variant(variant, lo.BannerShift(scale=0.5), profile, SIZES)
    after = res.block_after
    assert after is not None and abs(after.w - before.w / 2) <= 3 and abs(after.h - before.h / 2) <= 3
    assert abs((after.x + after.w / 2) - (before.x + before.w / 2)) <= 2
    assert abs((after.y + after.h / 2) - (before.y + before.h / 2)) <= 2


def test_scale_up_is_limited_by_safe_zone(variant, profile) -> None:
    res = lo.shift_variant(variant, lo.BannerShift(scale=1.5), profile, SIZES)
    safe = profile.safe_rect
    assert res.block_after is not None and res.block_after.w <= safe.w and res.block_after.h <= safe.h
    assert res.scale_applied < 1.5 and any(n.startswith("banner_scaled_to_safe_zone") for n in res.notes)


def test_identity_shift_returns_same_variant(variant, profile) -> None:
    res = lo.shift_variant(variant, lo.BannerShift(), profile, SIZES)
    assert res.variant is variant and not res.clamped and res.notes == []


# ---------------------------------------------------------------- зоны текста


def test_apply_caption_override_moves_zones_within_safe() -> None:
    layout = CaptionLayout()
    spec = lo.LayoutSpec(mode="manual", captions_bottom=900, title_top=100, onscreen_top=1900)
    out, applied = lo.apply_caption_override(layout, spec, safe=Rect.from_edges(65, 192, 895, 1380))
    assert out.source_zone == Zone(65, 640, 895, 900) and out.author_zone == Zone(65, 640, 895, 900)
    assert out.title_zone == Zone(65, 192, 895, 352)  # прижат к верху safe zone
    assert out.onscreen_zone == Zone(65, 1220, 895, 1380)  # не ниже safe zone
    assert applied == ["captions:1080->900", "title:280->192", "onscreen:480->1220"]
    same, none = lo.apply_caption_override(layout, lo.LayoutSpec(mode="auto", captions_bottom=900))
    assert same is layout and none == []


def test_layout_conflicts_depth_groups_and_skip() -> None:
    rects = {
        "creative": Rect.from_edges(65, 1120, 890, 1310),
        "code": Rect.from_edges(65, 1200, 890, 1366),  # лежит на креативе по замыслу
        "captions": Rect.from_edges(65, 1000, 895, 1160),
        "title": Rect.from_edges(65, 1300, 895, 1460),
        "onscreen": Rect.from_edges(65, 400, 895, 560),
    }
    groups = {"creative": "banner", "code": "banner"}
    out = lo.layout_conflicts(rects, groups=groups)
    pairs = {(c["a"], c["b"]): c["overlap_px"] for c in out}
    assert ("creative", "code") not in pairs
    assert pairs[("code", "title")] == 66 and pairs[("creative", "title")] == 10
    assert pairs[("captions", "creative")] == 40
    assert out[0]["overlap_px"] == 66 and out[0]["rect"] == {"x1": 65, "y1": 1300, "x2": 890, "y2": 1366}
    assert all("onscreen" not in (c["a"], c["b"]) for c in out)
    assert not any(
        "title" in (c["a"], c["b"]) for c in lo.layout_conflicts(rects, groups=groups, skip=["title"])
    )


# ---------------------------------------------------------------- расчёт целиком


def test_compute_layout_auto_matches_effective(variant, profile) -> None:
    comp = lo.compute_layout(variant, SIZES, profile, None)
    assert comp.auto is comp.effective and comp.auto.conflicts == []
    ids = {r.id: r for r in comp.auto.rects}
    assert ids["micro_brand"].movable is False and ids["creative"].movable is True
    assert ids["captions"].kind == "captions" and ids["title"].has_text and ids["captions"].rect.y2 <= 1120
    assert comp.auto.block == Rect.from_edges(65, 1120, 890, 1366)


def test_compute_layout_manual_reports_conflicts_and_skips_empty_zones(variant, profile) -> None:
    spec = lo.LayoutSpec(mode="manual", banner=lo.BannerShift(dy=-600), captions_bottom=1300)
    comp = lo.compute_layout(variant, SIZES, profile, spec)
    assert comp.auto.block != comp.effective.block
    assert comp.effective.captions.y2 == 1300
    assert any(a.startswith("captions:") and a.endswith("->1300") for a in comp.effective.adjustments)
    pairs = {(c["a"], c["b"]) for c in comp.effective.conflicts}
    assert ("creative", "onscreen") in pairs  # тезисам места нет — честно показываем
    without = lo.compute_layout(variant, SIZES, profile, spec, empty_zones=["onscreen", "title"])
    assert without.effective.conflicts == []
    assert not {r.id: r for r in without.effective.rects}["onscreen"].has_text


def test_compute_layout_without_variant_uses_default_zones(profile) -> None:
    comp = lo.compute_layout(None, {}, profile, lo.LayoutSpec(mode="manual", title_top=600))
    assert comp.effective.block is None and [r.kind for r in comp.effective.rects] == [
        "captions",
        "title",
        "onscreen",
    ]
    assert comp.effective.title.y == 600


# ---------------------------------------------------------------- produce_clip


def _transcript(n_sent: int = 12, lead: float = 12.0) -> Transcript:
    words: list[Word] = []
    t = lead
    for i in range(n_sent):
        for j in range(6):
            words.append(Word(f"слово{i}{j}" + ("." if j == 5 else ""), round(t, 3), round(t + 0.4, 3)))
            t += 0.5
        t += 0.6
    return Transcript.from_words(words, language="ru", duration=t + 20, provider="test")


class _BottomPlan:
    items: list = []
    captions_zone = SimpleNamespace(rect=SimpleNamespace(x=65, y=820, x2=895, y2=1080))
    placement = SimpleNamespace(profile_id="youtube_shorts")

    def to_json(self) -> dict:
        return {
            "v": 1,
            "out_size": [1080, 1920],
            "rects": {
                "creative": {"x1": 65, "y1": 1120, "x2": 890, "y2": 1310},
                "code": {"x1": 65, "y1": 1316, "x2": 890, "y2": 1366},
            },
            "items": [],
        }


def _produce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, layout_override: object):
    from autoshorts.clipping import reframe
    from autoshorts.clipping.author.script import AuthorScript

    monkeypatch.setattr(
        reframe,
        "plan_reframe",
        lambda video, a, b, *, src_w, src_h, **kw: reframe.static_plan(src_w, src_h, a, b, "center"),
    )
    tr = _transcript()
    op = AuthorScript.model_validate(
        {
            "cold_open": {"vo_text": "Мой хук про главное.", "on_screen": "Тезис автора на экране"},
            "outro": {"vo_text": "А вы как думаете?"},
            "on_screen_title": "Мой заголовок про лекцию",
        }
    )
    src = ClipSource(tmp_path / "s.mp4", tmp_path / "s.mp4", 1920, 1080, tr.duration, rights=CC)
    return produce_clip(
        src,
        tr,
        start=tr.sentences[2].s,
        end=tr.sentences[9].e,
        workdir=tmp_path / "w",
        author_script=op,
        config=ClipJobConfig(tts="mock", detect_faces=False, detect_scenes=False, run_qc=False),
        banner_plan=_BottomPlan(),
        layout_override=layout_override,
        render=False,
    )


def _ass_styles(tmp_path: Path) -> dict[str, list[str]]:
    return {
        ln.split(",")[0].split(":")[1].strip(): ln.split(",")
        for ln in (tmp_path / "w" / "captions.ass").read_text(encoding="utf-8").splitlines()
        if ln.startswith("Style:")
    }


def test_produce_applies_manual_zones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res = _produce(
        tmp_path,
        monkeypatch,
        {"mode": "manual", "captions_bottom": 900, "title_top": 200, "onscreen_top": 380},
    )
    meta = res.edl.meta
    assert meta["layout_manual"] is True and meta["layout_override"]["captions_bottom"] == 900
    zones = meta["caption_zones"]
    assert zones["source"] == {"x1": 65, "y1": 640, "x2": 895, "y2": 900}
    assert zones["title"]["y1"] == 200 and zones["onscreen"]["y1"] == 380
    assert "layout_conflicts" not in meta
    assert not any(w.startswith("captions_overlap_banner") for w in res.warnings)
    styles = _ass_styles(tmp_path)
    assert (
        styles["Source"][-2] == str(1920 - 900)
        and styles["Title"][-2] == "200"
        and styles["OnScreen"][-2] == "380"
    )


def test_produce_manual_captions_on_banner_warns_and_records_conflicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    res = _produce(tmp_path, monkeypatch, {"mode": "manual", "captions_bottom": 1250})
    assert any(w.startswith("captions_overlap_banner:") and "source_zone" in w for w in res.warnings)
    conflicts = res.edl.meta["layout_conflicts"]
    assert {(c["a"], c["b"]) for c in conflicts} >= {("captions", "creative")}
    assert res.edl.meta["caption_zones"]["source"]["y2"] == 1250


def test_produce_auto_mode_keeps_old_behaviour(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    res = _produce(tmp_path, monkeypatch, {"mode": "auto", "captions_bottom": 1250})
    meta = res.edl.meta
    assert meta["layout_manual"] is False and meta["caption_zones"]["source"]["y2"] == 1080
    assert "layout_conflicts" not in meta
    assert not any(w.startswith("captions_overlap_banner") for w in res.warnings)
    plain = _produce(tmp_path / "b", monkeypatch, None)
    assert plain.edl.meta["layout_manual"] is False and "layout_override" not in plain.edl.meta


def test_layer_labels_cover_all_kinds() -> None:
    assert set(lo.LAYER_LABELS) == {k.value for k in BannerLayerKind}

"""Контракт баннерного слоя M2 (решение владельца 2026-09-26: баннер — загруженный креатив).

API-схемы (`api/schemas/banners.py`, `campaigns.py`) зеркалят домен `domain/banner_schema.py` один
в один, коды предупреждений и отказов — те же, что у `pipeline/banner_validate` и `banner_assets`,
ответ валидации строится из настоящего результата `validate_variant`, эндпоинты редактора есть в OpenAPI.
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autoshorts.api.schemas.banners import (
    MAX_CREATIVE_UPLOAD_BYTES,
    BannerLayerPlacement,
    BannerPreviewRequest,
    BannerSafeZoneCatalog,
    BannerSafeZoneProfile,
    BannerValidateRequest,
    BannerValidateResponse,
    BannerVariantDraft,
    BannerWarning,
    CreativeRejectCode,
    PlatformSafeZone,
)
from autoshorts.api.schemas.campaigns import BannerVariant, BannerVariantCreate, BannerVariantPatch
from autoshorts.api.schemas.review import ReviewBanner
from autoshorts.domain import banner_schema as B
from autoshorts.domain.enums import Platform, WarningSeverity
from autoshorts.domain.settings_schema import CanvasSize

ROOT = Path(__file__).resolve().parents[2]
API = "/api/v1"
SHA_A = "a" * 64
SHA_B = "b" * 64


def _creative(
    sha: str, w: int = 800, h: int = 200, kind: B.CreativeKind = B.CreativeKind.STATIC
) -> B.BannerCreative:
    animated = kind == B.CreativeKind.ANIMATED
    return B.BannerCreative(
        sha256=sha,
        kind=kind,
        source_format=B.CreativeSourceFormat.WEBM if animated else B.CreativeSourceFormat.PNG,
        width=w,
        height=h,
        source_width=w,
        source_height=h,
        has_alpha=True,
        duration_sec=4.0 if animated else 0.0,
        fps=30.0 if animated else 0.0,
        frame_count=120 if animated else 1,
        source_bytes=1000,
        normalized_path=f"banners/creatives/{sha[:2]}/{sha}/creative.{'webm' if animated else 'png'}",
        normalized_mime="video/webm" if animated else "image/png",
        thumb_path=f"banners/creatives/{sha[:2]}/{sha}/thumb.png",
        first_frame_path=f"banners/creatives/{sha[:2]}/{sha}/first_frame.png",
    )


# ---------------------------------------------------------------- черновик = домен


def test_draft_has_exactly_domain_variant_fields() -> None:
    assert set(BannerVariantDraft.model_fields) == set(B.BannerVariant.model_fields)


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"creative_sha256": SHA_A},
        {"creative_sha256": SHA_A, "erid": "2SDnjeXyZ1", "ad_marking_required": True},
        {"show_code": False},
    ],
)
def test_default_variant_roundtrips_through_api(kwargs: dict[str, Any]) -> None:
    domain = B.default_banner_variant(**kwargs)
    draft = BannerVariantDraft.from_domain(domain)
    assert draft.to_domain().model_dump() == domain.model_dump()
    # и через JSON, как это делает фронт
    again = BannerVariantDraft.model_validate_json(draft.model_dump_json())
    assert again.to_domain().model_dump() == domain.model_dump()


def test_draft_null_code_url_template_takes_product_default() -> None:
    draft = BannerVariantDraft(layers=B.default_banner_variant().layers, code_url_template=None)
    assert draft.to_domain(default_code_url_template="go.example.ru/{code}").code_url_template == (
        "go.example.ru/{code}"
    )
    with pytest.raises(ValidationError):
        BannerVariantDraft(layers=[], code_url_template="u.umka.chat/no-placeholder")


def test_draft_enforces_domain_rules() -> None:
    layers = [la.model_dump() for la in B.default_banner_variant(creative_sha256=SHA_A).layers]
    with pytest.raises(ValidationError, match="layer ids must be unique"):
        BannerVariantDraft(layers=[*layers, layers[0]])
    bad_text = {**layers[-1], "text": "  "}  # text_erid без текста
    with pytest.raises(ValidationError, match="needs text"):
        BannerVariantDraft(layers=[*layers[:-1], bad_text])
    with pytest.raises(ValidationError):
        BannerVariantDraft(layers=[{**layers[1], "creative_sha256": "NOT-A-SHA"}])
    with pytest.raises(ValidationError):  # extra="forbid": опечатки не теряются
        BannerVariantDraft(layers=layers, timing={"mode": "impulse", "typo": 1})
    with pytest.raises(ValidationError):
        BannerVariantDraft(layers=layers, timing={"mode": "windows", "windows": []})


def test_create_and_patch_layer_rules() -> None:
    ok = BannerVariantCreate(code="A", name="Креатив A", creative_sha256=SHA_A)
    assert ok.layers is None and ok.status.value == "draft"
    layers = B.default_banner_variant(creative_sha256=SHA_A).layers
    with pytest.raises(ValidationError, match="only used when layers is omitted"):
        BannerVariantCreate(code="B", name="B", layers=layers, creative_sha256=SHA_B)
    with pytest.raises(ValidationError, match="unique"):
        BannerVariantCreate(code="B", name="B", layers=[*layers, layers[0]])
    with pytest.raises(ValidationError, match="unique"):
        BannerVariantPatch(layers=[*layers, layers[0]])
    assert BannerVariantPatch(weight=10).model_fields_set == {"weight"}


def test_saved_variant_converts_to_domain() -> None:
    domain = B.default_banner_variant(creative_sha256=SHA_A)
    now = datetime(2026, 9, 27, tzinfo=UTC)
    api = BannerVariant(
        id=1,
        campaign_id=2,
        product_id="umka",
        code="A",
        name="default",
        status="active",
        weight=50,
        layers=domain.layers,
        timing=domain.timing,
        ad_marking=domain.ad_marking,
        code_url_template=None,
        effective_code_url_template="u.umka.chat/{code}",
        voice_cta_text=None,
        pinned_comment_template=None,
        description_cta_template=None,
        notes=None,
        creatives=[],
        version=1,
        created_at=now,
        updated_at=now,
    )
    assert api.to_domain().model_dump() == domain.model_dump()
    assert api.to_domain().creative_shas() == [SHA_A]


# ---------------------------------------------------------------- коды


def _asset_error_codes() -> set[str]:
    tree = ast.parse((ROOT / "src/autoshorts/pipeline/banner_assets.py").read_text(encoding="utf-8"))
    codes: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "BannerAssetError"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            codes.add(node.args[0].value)
    return codes


def test_reject_codes_cover_every_banner_asset_error() -> None:
    codes = _asset_error_codes()
    assert len(codes) >= 5, "парсер не нашёл вызовов BannerAssetError"
    assert codes <= {c.value for c in CreativeRejectCode}, codes - {c.value for c in CreativeRejectCode}


def test_warning_is_domain_warning_one_to_one() -> None:
    assert BannerWarning.model_fields["code"].annotation is B.BannerValidationCode
    assert set(BannerWarning.model_fields) == set(B.BannerValidationWarning.model_fields)
    assert {s.value for s in WarningSeverity} == {s.value for s in B.WarningSeverity}
    for code in B.BannerValidationCode:
        w = B.BannerValidationWarning(code=code, severity=B.WarningSeverity.ERROR, layer_id="creative")
        api = BannerWarning.from_domain(w)
        assert api.code is code and api.severity is WarningSeverity.ERROR


def test_upload_limit_matches_asset_limits() -> None:
    from autoshorts.pipeline.banner_assets import DEFAULT_LIMITS

    assert MAX_CREATIVE_UPLOAD_BYTES == DEFAULT_LIMITS.max_animated_bytes == 50 * 1024 * 1024
    assert DEFAULT_LIMITS.max_static_bytes <= MAX_CREATIVE_UPLOAD_BYTES


# ---------------------------------------------------------------- safe zones


def test_safe_zone_catalog_serves_config_profiles() -> None:
    from autoshorts.pipeline.banner_place import PLATFORM_PROFILE, load_safe_zone_catalog

    catalog = load_safe_zone_catalog()
    items = [BannerSafeZoneProfile.from_domain(p) for p in catalog.profiles]
    api = BannerSafeZoneCatalog(
        version=catalog.version,
        canvas=CanvasSize(w=B.CANVAS_W, h=B.CANVAS_H),
        default_profile=catalog.default_profile,
        platform_profiles=[
            PlatformSafeZone(platform=p, profile_id=PLATFORM_PROFILE[p.value]) for p in Platform
        ],
        items=items,
    )
    ids = {p.id for p in api.items}
    assert api.default_profile in ids
    assert {pp.profile_id for pp in api.platform_profiles} <= ids  # YouTube Shorts и VK Клипы есть
    for p in api.items:
        assert p.safe.x1 < p.safe.x2 and p.safe.y1 < p.safe.y2
        for z in p.dead_zones:
            assert 0 <= z.x1 < z.x2 <= p.canvas.w and 0 <= z.y1 < z.y2 <= p.canvas.h, (p.id, z)
    yt = next(p for p in api.items if p.id == PLATFORM_PROFILE["youtube"])
    assert {z.id for z in yt.dead_zones} >= {"top_bar", "bottom_ui", "right_rail"}


# ---------------------------------------------------------------- ответ валидации из настоящего результата


def test_validate_response_built_from_domain_result() -> None:
    from autoshorts.pipeline.banner_place import load_safe_zone_catalog, profile_for_platform
    from autoshorts.pipeline.banner_validate import validate_variant

    catalog = load_safe_zone_catalog()
    variant = B.default_banner_variant(creative_sha256=SHA_A)
    # креатив уехал вниз, под интерфейс площадки — должны быть предупреждения
    layers = [
        la.model_copy(update={"slot": la.slot.model_copy(update={"y": 1600})}) if la.id == "creative" else la
        for la in variant.layers
    ]
    variant = variant.model_copy(update={"layers": layers})
    profiles = [profile_for_platform("youtube", catalog), catalog.get()]
    result = validate_variant(variant, {SHA_A: _creative(SHA_A)}, profiles=profiles, duration_sec=21.4)
    api = BannerValidateResponse.from_result(result, profile_ids=[p.id for p in profiles], duration_sec=21.4)

    assert api.profile_ids[0] == "youtube_shorts"
    assert {w.code.value for w in api.warnings} & {
        "creative_in_bottom_dead_zone",
        "creative_outside_safe_zone",
    }
    creative = next(pl for pl in api.placements if pl.layer_id == "creative")
    assert creative.creative_sha256 == SHA_A and creative.scale is not None and creative.full_rect is not None
    assert {w.group.value for w in api.windows} == {"persistent", "with_creative"}
    assert all(0 <= w.start_sec < w.end_sec <= 21.4 for w in api.windows)
    assert api.ok == (not any(w.severity is WarningSeverity.ERROR for w in api.warnings))
    # ответ сериализуется и читается обратно (то, что увидит фронт)
    assert BannerValidateResponse.model_validate_json(api.model_dump_json()) == api
    # те же коробки годятся для оверлея приёмки
    ReviewBanner(
        variant_id=1,
        variant_code="A",
        creative_sha256s=[SHA_A],
        fallback_used=api.fallback_used,
        windows=api.windows,
        boxes=[{"layer_id": pl.layer_id, "kind": pl.kind, **pl.rect.model_dump()} for pl in api.placements],
        captions_zone=api.captions_zone.rect,
        warnings=api.warnings,
    )


def test_placement_without_creative_uses_fallback_plate() -> None:
    from autoshorts.pipeline.banner_place import load_safe_zone_catalog
    from autoshorts.pipeline.banner_validate import validate_variant

    catalog = load_safe_zone_catalog()
    variant = B.default_banner_variant(creative_sha256=SHA_B)  # файла нет в библиотеке
    result = validate_variant(variant, {}, profiles=[catalog.get()], duration_sec=20.0)
    api = BannerValidateResponse.from_result(result, profile_ids=[catalog.default_profile], duration_sec=20.0)
    assert api.missing_creatives == [SHA_B] and api.fallback_used
    assert "creative_missing" in {w.code.value for w in api.warnings}
    plate = [pl for pl in api.placements if pl.kind.value == "fallback_plate"]
    assert plate and isinstance(plate[0], BannerLayerPlacement) and plate[0].scale is None


def test_check_requests_accept_draft_or_saved_variant() -> None:
    draft = BannerVariantDraft.from_domain(B.default_banner_variant(creative_sha256=SHA_A)).model_dump()
    v = BannerValidateRequest.model_validate({"draft": draft, "platform": "vk", "profile_ids": ["vk_clips"]})
    assert v.draft is not None and v.duration_sec == 30.0
    with pytest.raises(ValidationError):
        BannerValidateRequest.model_validate({"variant_id": 1, "profile_ids": ["VK Клипы"]})
    p = BannerPreviewRequest.model_validate(
        {"variant_id": 3, "moment": "at_time", "t_sec": 9.5, "background": "job:12"}
    )
    assert p.size.value == "half"
    with pytest.raises(ValidationError):
        BannerPreviewRequest.model_validate({"variant_id": 3, "background": "https://evil.example/x.png"})


# ---------------------------------------------------------------- OpenAPI редактора


EDITOR_OPS = {
    ("get", f"{API}/banner-creatives"): "banners_creatives_list",
    ("post", f"{API}/banner-creatives"): "banners_creatives_upload",
    ("get", f"{API}/banner-creatives/{{creative_sha256}}"): "banners_creatives_get",
    ("patch", f"{API}/banner-creatives/{{creative_sha256}}"): "banners_creatives_update",
    ("delete", f"{API}/banner-creatives/{{creative_sha256}}"): "banners_creatives_delete",
    ("get", f"{API}/banner-creatives/{{creative_sha256}}/media/{{media_kind}}"): "banners_creatives_media",
    ("get", f"{API}/banner-safe-zones"): "banners_safe_zones",
    ("get", f"{API}/banner-fonts"): "banners_fonts_list",
    ("get", f"{API}/banner-fonts/{{font_name}}"): "banners_fonts_file",
    ("post", f"{API}/banner-variants/validate"): "banners_variant_validate",
    ("post", f"{API}/banner-variants/preview"): "banners_variant_preview",
    ("get", f"{API}/banner-variants/{{variant_id}}"): "banners_variant_get",
    ("post", f"{API}/banner-variants/{{variant_id}}/duplicate"): "banners_variant_duplicate",
}


def test_editor_endpoints_present(openapi_spec: dict[str, Any]) -> None:
    for (method, path), op_id in EDITOR_OPS.items():
        op = openapi_spec["paths"].get(path, {}).get(method)
        assert op is not None, f"{method.upper()} {path}"
        assert op["operationId"] == op_id and op["tags"] == ["banners"]


def test_upload_is_multipart_with_file(openapi_spec: dict[str, Any]) -> None:
    op = openapi_spec["paths"][f"{API}/banner-creatives"]["post"]
    body = op["requestBody"]["content"]["multipart/form-data"]["schema"]
    ref = body["$ref"].rsplit("/", 1)[-1]
    schema = openapi_spec["components"]["schemas"][ref]
    assert "file" in schema["required"]
    assert schema["properties"]["file"].get("format") == "binary" or (
        schema["properties"]["file"].get("contentMediaType") == "application/octet-stream"
    )
    assert set(schema["properties"]) == {"file", "name", "product_id"}


def test_binary_endpoints_declare_media_types(openapi_spec: dict[str, Any]) -> None:
    paths = openapi_spec["paths"]
    preview = paths[f"{API}/banner-variants/preview"]["post"]["responses"]["200"]
    assert set(preview["content"]) == {"image/png"} and "X-Preview-T-Sec" in preview["headers"]
    media = paths[f"{API}/banner-creatives/{{creative_sha256}}/media/{{media_kind}}"]["get"]["responses"][
        "200"
    ]
    assert {"image/png", "video/webm"} <= set(media["content"])
    font = paths[f"{API}/banner-fonts/{{font_name}}"]["get"]["responses"]["200"]
    assert {"font/ttf", "font/otf"} <= set(font["content"])


def test_variant_schema_carries_layers_not_text_plate(openapi_spec: dict[str, Any]) -> None:
    schemas = openapi_spec["components"]["schemas"]
    props = set(schemas["BannerVariant"]["properties"])
    assert {"layers", "timing", "ad_marking", "creatives", "version"} <= props
    assert not props & {
        "offer_text",
        "offer_text_2",
        "micro_brand_text",
        "layout_overrides",
        "style_overrides",
    }
    assert schemas["BannerLayer"]["properties"]["creative_sha256"]
    assert set(schemas["AdMarking"]["properties"]) == {"required", "erid", "advertiser_text"}
    template_layout = schemas["BannerTemplate"]["properties"]["layout"]
    assert template_layout.get("$ref", "").endswith("/BannerVariantDraft")

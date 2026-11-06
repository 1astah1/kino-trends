"""Шаблоны баннера (пресеты варианта), импорт/экспорт формы config/banner.yaml, проверка и кадр.

Решение владельца 2026-09-26: баннер — загруженный креатив, шаблон — только пресет для новых
вариантов кампании: `banner_templates.layout` = `api.schemas.banners.BannerVariantDraft` (слои,
тайминг, маркировка, формат кода).

Импорт config/banner.yaml (legacy-форма ТЗ §7) → вариант «из коробки»
(`domain.default_banner_variant`) с перенесёнными из YAML параметрами: текст, прозрачность и место
микро-бренда, место и стиль запасной плашки (креатива в YAML нет — плашка включена), тайминг
импульса и финального повтора. Геометрия legacy-файла считалась по старым мёртвым зонам, поэтому
слоты вписываются в общий профиль safe zone (`common_organic`, research §7.1). Экспорт пишет ту же
форму плюс ключ `variant` — полный черновик: импорт экспорта восстанавливает шаблон без потерь.
"""

from __future__ import annotations

from typing import Any

import psycopg
import yaml
from pydantic import ValidationError

from autoshorts.core.errors import NotFound, VersionConflict
from autoshorts.core.yamlsafe import load_untrusted_yaml
from autoshorts.domain.banner_schema import (
    CANVAS_H,
    CANVAS_W,
    BannerCreative,
    BannerLayer,
    BannerLayerKind,
    BannerTiming,
    BannerVariant,
    LayerShow,
    Rect,
    default_banner_variant,
)
from autoshorts.pipeline.banner_place import clamp_slot_into_safe_zone, load_safe_zone_catalog
from autoshorts.repos import banners as templates_repo
from autoshorts.repos import campaigns as campaigns_repo
from autoshorts.repos import products as products_repo
from autoshorts.services import audit, banner_preview
from autoshorts.services import banner_creatives as creatives_service
from autoshorts.services.audit import Actor
from autoshorts.services.campaigns import check_layer_creatives, require_variant, to_domain_variant
from autoshorts.services.products import code_url_template, field_error

DEFAULT_CODE_URL_TEMPLATE = "u.umka.chat/{code}"
MAX_YAML_BYTES = 256 * 1024


def _draft_cls() -> Any:
    # схема API — единственный источник формы черновика (поля = domain.BannerVariant)
    from autoshorts.api.schemas.banners import BannerVariantDraft

    return BannerVariantDraft


# ---------------------------------------------------------------- banner.yaml ↔ черновик


def _num(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _timing_from_yaml(raw: dict[str, Any]) -> BannerTiming:
    data: dict[str, Any] = {}
    if "hook_silence_sec" in raw:
        data["hook_silence_sec"] = _num(raw["hook_silence_sec"], 2.0)
    pct = raw.get("impulse_window_pct")
    if isinstance(pct, list | tuple) and len(pct) == 2:
        data["impulse_window_pct"] = (_num(pct[0], 0.4), _num(pct[1], 0.65))
    dur = raw.get("impulse_duration_sec")
    if isinstance(dur, list | tuple) and len(dur) == 2:
        lo, hi = sorted((_num(dur[0], 3.0), _num(dur[1], 5.0)))
        data["impulse_min_sec"], data["impulse_duration_sec"] = lo, round((lo + hi) / 2, 3)
    elif dur is not None:
        data["impulse_duration_sec"] = _num(dur, 4.0)
        data["impulse_min_sec"] = min(3.0, data["impulse_duration_sec"])
    for key in ("fade_in_ms", "fade_out_ms"):
        if key in raw:
            data[key] = int(_num(raw[key], 200))
    if "final_repeat" in raw:
        data["final_repeat"] = bool(raw["final_repeat"])
    if "final_repeat_sec" in raw:
        data["final_repeat_sec"] = _num(raw["final_repeat_sec"], 2.5)
    return BannerTiming.model_validate(data)


def draft_from_banner_yaml(data: dict[str, Any], *, offer_text: str | None = None) -> Any:
    """Форма config/banner.yaml → `BannerVariantDraft`. Ключ `variant` (наш экспорт) главнее legacy-полей."""
    draft_cls = _draft_cls()
    if isinstance(data.get("variant"), dict):
        return draft_cls.model_validate(data["variant"])
    l1 = data.get("layer_1_micro_brand") if isinstance(data.get("layer_1_micro_brand"), dict) else {}
    l2 = data.get("layer_2_offer_banner") if isinstance(data.get("layer_2_offer_banner"), dict) else {}
    brand_text = str(l1.get("text") or "umka.chat")
    kwargs: dict[str, Any] = {"micro_brand_text": brand_text}
    plate_text = offer_text or l2.get("text")
    if plate_text:
        kwargs["offer_text"] = str(plate_text)
    base = default_banner_variant(**kwargs)
    profile = load_safe_zone_catalog().get()
    layers: list[BannerLayer] = []
    for layer in base.layers:
        if layer.kind == BannerLayerKind.MICRO_BRAND and l1:
            slot = layer.slot
            pos = l1.get("position") if isinstance(l1.get("position"), dict) else {}
            slot = slot.model_copy(
                update={
                    "x": int(_num(pos.get("x"), slot.x)),
                    "y": int(_num(pos.get("y"), slot.y)),
                    "h": max(8, int(_num(l1.get("height_px"), slot.h))),
                    "opacity": min(1.0, max(0.0, _num(l1.get("opacity"), slot.opacity))),
                }
            )
            layer = layer.model_copy(
                update={
                    "slot": clamp_slot_into_safe_zone(slot, profile),
                    "enabled": bool(l1.get("enabled", True)),
                    "show": LayerShow.PERSISTENT if l1.get("persistent", True) else LayerShow.WITH_CREATIVE,
                }
            )
        elif layer.kind == BannerLayerKind.FALLBACK_PLATE and l2:
            slot = layer.slot
            pos = l2.get("position") if isinstance(l2.get("position"), dict) else {}
            if {"x1", "y1", "x2", "y2"} <= set(pos):
                r = Rect.from_edges(
                    _num(pos["x1"], 0), _num(pos["y1"], 0), _num(pos["x2"], 1), _num(pos["y2"], 1)
                )
                if not r.is_empty:
                    slot = slot.model_copy(update={"x": r.x, "y": r.y, "w": r.w, "h": r.h})
            style = layer.style
            assert style is not None
            max_px = int(_num(l2.get("text_size_px"), style.max_font_px))
            style = style.model_copy(
                update={
                    "bg_opacity": min(1.0, max(0.0, _num(l2.get("background_opacity"), style.bg_opacity))),
                    "corner_radius_px": int(_num(l2.get("corner_radius_px"), style.corner_radius_px)),
                    "max_font_px": max(style.min_font_px, min(200, max_px)),
                }
            )
            layer = layer.model_copy(
                update={
                    "slot": clamp_slot_into_safe_zone(slot, profile),
                    "style": style,
                    "enabled": bool(l2.get("enabled", True)),
                }
            )
        layers.append(layer)
    timing_raw = l2.get("timing") if isinstance(l2.get("timing"), dict) else {}
    variant = BannerVariant(
        code="A",
        name=str(data.get("name") or "banner.yaml"),
        layers=layers,
        timing=_timing_from_yaml(timing_raw),
        ad_marking=base.ad_marking,
        code_url_template=DEFAULT_CODE_URL_TEMPLATE,
    )
    return draft_cls.model_validate({**variant.model_dump(mode="json"), "code_url_template": None})


def draft_to_banner_yaml(draft: Any, *, name: str | None = None) -> str:
    """Черновик → YAML формы config/banner.yaml + полный `variant` (импорт без потерь)."""
    layers = {la.kind: la for la in draft.layers}
    out: dict[str, Any] = {"canvas": {"w": CANVAS_W, "h": CANVAS_H}}
    brand = layers.get(BannerLayerKind.MICRO_BRAND)
    if brand is not None:
        out["layer_1_micro_brand"] = {
            "enabled": brand.enabled,
            "text": brand.text,
            "height_px": brand.slot.h,
            "opacity": brand.slot.opacity,
            "position": {"x": brand.slot.x, "y": brand.slot.y},
            "persistent": brand.show == LayerShow.PERSISTENT,
        }
    offer = layers.get(BannerLayerKind.CREATIVE) or layers.get(BannerLayerKind.FALLBACK_PLATE)
    plate = layers.get(BannerLayerKind.FALLBACK_PLATE)
    t = draft.timing
    if offer is not None:
        s = offer.slot
        l2: dict[str, Any] = {
            "enabled": offer.enabled or bool(plate and plate.enabled),
            "height_px": s.h,
            "position": {"x1": s.x, "y1": s.y, "x2": s.x + s.w, "y2": s.y + s.h},
        }
        if plate is not None and plate.style is not None:
            l2.update(
                {
                    "text": plate.text,
                    "text_size_px": plate.style.max_font_px,
                    "font_weight": "bold",
                    "background_opacity": plate.style.bg_opacity,
                    "corner_radius_px": plate.style.corner_radius_px,
                }
            )
        l2["timing"] = {
            "hook_silence_sec": t.hook_silence_sec,
            "impulse_window_pct": list(t.impulse_window_pct),
            "impulse_duration_sec": [
                t.impulse_min_sec,
                round(2 * t.impulse_duration_sec - t.impulse_min_sec, 3),
            ],
            "fade_in_ms": t.fade_in_ms,
            "fade_out_ms": t.fade_out_ms,
            "final_repeat": t.final_repeat,
            "final_repeat_sec": t.final_repeat_sec,
        }
        out["layer_2_offer_banner"] = l2
    if name:
        out["name"] = name
    out["variant"] = draft.model_dump(mode="json")
    header = "# Шаблон баннера Auto Shorts: legacy-форма config/banner.yaml + полный вариант (variant)\n"
    return header + yaml.safe_dump(
        out, allow_unicode=True, sort_keys=False, default_flow_style=None, width=110
    )


def parse_banner_yaml(text: str, *, offer_text: str | None = None, loc: str = "file") -> Any:
    """YAML шаблона из загрузки оператора → черновик; ссылки `*alias` отвергаются (alias bomb)."""
    try:
        data = load_untrusted_yaml(text)
    except yaml.YAMLError as exc:
        raise field_error(loc, f"YAML не разбирается: {exc}", "yaml_invalid") from None
    if not isinstance(data, dict):
        raise field_error(loc, "YAML баннера должен быть словарём", "yaml_invalid")
    try:
        return draft_from_banner_yaml(data, offer_text=offer_text)
    except (ValidationError, ValueError) as exc:
        raise field_error(loc, f"YAML баннера не прошёл проверку: {exc}", "yaml_invalid") from None


# ---------------------------------------------------------------- шаблоны


def _layout_draft(layout: Any) -> Any:
    """Сохранённый layout → черновик; старая форма (0003, banner.yaml) переводится на лету."""
    draft_cls = _draft_cls()
    try:
        return draft_cls.model_validate(layout or {})
    except ValidationError:
        pass
    try:
        return draft_from_banner_yaml(layout if isinstance(layout, dict) else {})
    except (ValidationError, ValueError):
        return draft_cls.from_domain(default_banner_variant())


def template_to_api(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "layout": _layout_draft(row["layout"]).model_dump(mode="json"),
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _require_template(conn: Any, template_id: int, *, for_update: bool = False) -> dict[str, Any]:
    row = templates_repo.get(conn, template_id, for_update=for_update)
    if row is None:
        raise NotFound("banner_template", "Шаблон баннера не найден")
    return row


def _check_draft(conn: Any, draft: Any, loc: str = "layout") -> None:
    check_layer_creatives(conn, draft.layers, loc=f"{loc}.layers")
    banner_preview.check_fonts(draft.layers, loc=f"{loc}.layers")


def list_templates(*, status: str | None, limit: int, offset: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        rows, total = templates_repo.list_page(conn, status=status, limit=limit, offset=offset)
    return {"items": [template_to_api(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def get_template(template_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return template_to_api(_require_template(conn, template_id))


def create_template(name: str, draft: Any, actor: Actor, *, source: str = "ui") -> dict[str, Any]:
    from autoshorts.db.pool import connection

    name = name.strip()
    try:
        with connection() as conn:
            _check_draft(conn, draft)
            row = templates_repo.insert(conn, name=name, layout=draft.model_dump(mode="json"))
            audit.record(
                conn,
                actor,
                "banner_template.create",
                "banner_template",
                row["id"],
                summary=f"шаблон баннера «{name}» ({source})",
            )
            return template_to_api(row)
    except psycopg.errors.UniqueViolation as exc:
        raise field_error("name", "Шаблон с таким именем уже есть", "unique") from exc


def update_template(
    template_id: int,
    *,
    name: str | None,
    status: str | None,
    draft: Any | None,
    expected_version: int,
    actor: Actor,
) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    try:
        with connection() as conn:
            current = _require_template(conn, template_id, for_update=True)
            if int(current["version"]) != int(expected_version):
                raise VersionConflict(
                    "Шаблон уже изменили, обновите страницу",
                    details={"current_version": int(current["version"])},
                )
            if draft is not None:
                _check_draft(conn, draft)
            row = templates_repo.update(
                conn,
                template_id,
                name=name.strip() if name else None,
                status=status,
                layout=draft.model_dump(mode="json") if draft is not None else None,
            )
            audit.record(
                conn,
                actor,
                "banner_template.update",
                "banner_template",
                template_id,
                diff={"name": name, "status": status, "layout": draft is not None, "version": row["version"]},
            )
            return template_to_api(row)
    except psycopg.errors.UniqueViolation as exc:
        raise field_error("name", "Шаблон с таким именем уже есть", "unique") from exc


def duplicate_template(template_id: int, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        src = _require_template(conn, template_id)
        base = f"{src['name']} (копия)"
        name, n = base, 2
        while templates_repo.get_by_name(conn, name) is not None:
            name, n = f"{base} {n}", n + 1
        row = templates_repo.insert(conn, name=name[:200], layout=src["layout"])
        audit.record(
            conn,
            actor,
            "banner_template.duplicate",
            "banner_template",
            row["id"],
            summary=f"копия шаблона {template_id}",
            diff={"source_id": template_id},
        )
        return template_to_api(row)


def export_template_yaml(template_id: int) -> tuple[str, str]:
    """→ (YAML, имя шаблона)."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        row = _require_template(conn, template_id)
    return draft_to_banner_yaml(_layout_draft(row["layout"]), name=row["name"]), row["name"]


def import_template_yaml(text: str, *, name: str, actor: Actor) -> dict[str, Any]:
    return create_template(name, parse_banner_yaml(text), actor, source="yaml_import")


def upsert_template_from_yaml(
    text: str, *, name: str, actor: Actor, offer_text: str | None = None
) -> tuple[dict[str, Any], str]:
    """CLI `import-banner`: создать шаблон или обновить его layout, если он отличается.

    → (шаблон, действие: created | updated | unchanged).
    """
    from autoshorts.db.pool import connection

    draft = parse_banner_yaml(text, offer_text=offer_text)
    layout = draft.model_dump(mode="json")
    with connection() as conn:
        current = templates_repo.get_by_name(conn, name.strip())
        if current is None:
            action = "created"
        elif current["layout"] == layout:
            return template_to_api(current), "unchanged"
        else:
            action = "updated"
    if action == "created":
        return create_template(name, draft, actor, source="yaml_import"), action
    assert current is not None
    return update_template(
        int(current["id"]),
        name=None,
        status=None,
        draft=draft,
        expected_version=int(current["version"]),
        actor=actor,
    ), action


# ---------------------------------------------------------------- проверка и кадр (вариант и шаблон)


def resolve_check_input(
    conn: Any, body: Any, *, template_id: int | None = None
) -> tuple[BannerVariant, dict[str, BannerCreative]]:
    """Что проверять/рендерить: черновик → сохранённый вариант → layout шаблона (только для шаблона)."""
    default_url = DEFAULT_CODE_URL_TEMPLATE
    if body.campaign_id is not None:
        campaign = campaigns_repo.get_campaign(conn, body.campaign_id)
        if campaign is None:
            raise field_error("campaign_id", "Кампания не найдена", "not_found")
        default_url = code_url_template(products_repo.get(conn, campaign["product_id"]))
    if body.draft is not None:
        banner_preview.check_fonts(body.draft.layers, loc="draft.layers")
        if body.campaign_id is None and body.variant_id is not None:
            row = require_variant(conn, body.variant_id)
            default_url = code_url_template(products_repo.get(conn, row["product_id"]))
        variant = body.draft.to_domain(default_code_url_template=default_url)
    elif body.variant_id is not None:
        row = require_variant(conn, body.variant_id)
        variant = to_domain_variant(row, products_repo.get(conn, row["product_id"]))
    elif template_id is not None:
        draft = _layout_draft(_require_template(conn, template_id)["layout"])
        variant = draft.to_domain(default_code_url_template=default_url)
    else:
        raise field_error("draft", "Нужен draft (черновик) или variant_id", "missing")
    return variant, creatives_service.domain_creatives(conn, variant.creative_shas())


def validate_request(body: Any, *, template_id: int | None = None) -> dict[str, Any]:
    """`BannerValidateRequest` → `BannerValidateResponse` (как dict)."""
    from autoshorts.api.schemas.banners import BannerValidateResponse
    from autoshorts.db.pool import connection

    with connection() as conn:
        variant, creatives = resolve_check_input(conn, body, template_id=template_id)
    captions = None
    if body.captions_zone is not None:
        z = body.captions_zone
        captions = Rect.from_edges(z.x1, z.y1, z.x2, z.y2)
    result, profile_ids = banner_preview.validate_domain_variant(
        variant,
        creatives,
        platform=str(body.platform),
        duration_sec=body.duration_sec,
        profile_ids=body.profile_ids,
        captions_zone=captions,
    )
    resp = BannerValidateResponse.from_result(result, profile_ids=profile_ids, duration_sec=body.duration_sec)
    return resp.model_dump(mode="json")


def preview_request(body: Any, *, template_id: int | None = None) -> tuple[bytes, float]:
    """`BannerPreviewRequest` → (PNG, фактическое время кадра)."""
    from autoshorts.db.pool import connection

    with connection() as conn:
        variant, creatives = resolve_check_input(conn, body, template_id=template_id)
    return banner_preview.render_preview(
        variant,
        creatives,
        platform=str(body.platform),
        duration_sec=body.duration_sec,
        moment=str(body.moment),
        t_sec=body.t_sec,
        profile_id=body.profile_id,
        background=body.background,
        size=str(body.size),
        code=body.code,
    )


# ---------------------------------------------------------------- справочники редактора


_fonts_cache: dict[tuple[str, int], dict[str, Any]] = {}


def list_fonts() -> list[dict[str, Any]]:
    """Шрифты служебного текста из config/fonts (имя файла = `TextStyle.font_file`)."""
    from autoshorts.pipeline.banner_text import default_fonts_dir
    from autoshorts.services.assets import font_meta

    out: list[dict[str, Any]] = []
    fonts_dir = default_fonts_dir()
    if not fonts_dir.is_dir():
        return out
    for path in sorted(fonts_dir.iterdir()):
        if path.suffix.lower() not in (".ttf", ".otf") or not path.is_file():
            continue
        key = (str(path), path.stat().st_mtime_ns)
        meta = _fonts_cache.get(key)
        if meta is None:
            try:
                meta = font_meta(path)
            except ValueError:
                continue
            _fonts_cache[key] = meta
        out.append(
            {
                "name": path.name,
                "family": meta["family"],
                "style": meta["style"],
                "cyrillic_ok": meta["cyrillic_ok"],
                "file_url": f"/api/v1/banner-fonts/{path.name}",
            }
        )
    return out


def font_path(font_name: str) -> Any:
    from autoshorts.pipeline.banner_text import default_fonts_dir

    path = default_fonts_dir() / font_name
    if "/" in font_name or not path.is_file():
        raise NotFound("font", "Шрифт не найден")
    return path


def safe_zone_catalog() -> dict[str, Any]:
    """`GET /banner-safe-zones`: профили config/safe_zones.json + профиль по умолчанию для площадок."""
    from autoshorts.api.schemas.banners import BannerSafeZoneProfile
    from autoshorts.domain.enums import Platform
    from autoshorts.pipeline.banner_place import PLATFORM_PROFILE

    catalog = load_safe_zone_catalog()
    ids = {p.id for p in catalog.profiles}
    return {
        "version": catalog.version,
        "canvas": {"w": CANVAS_W, "h": CANVAS_H},
        "default_profile": catalog.default_profile,
        "platform_profiles": [
            {"platform": str(p), "profile_id": PLATFORM_PROFILE[str(p)]}
            for p in Platform
            if PLATFORM_PROFILE.get(str(p)) in ids
        ],
        "items": [BannerSafeZoneProfile.from_domain(p).model_dump(mode="json") for p in catalog.profiles],
    }

"""Кампании продукта и варианты баннера A/B (§5.2, решение владельца 2026-09-26 п. 1).

Вариант = слои `domain.banner_schema.BannerLayer` (загруженный креатив по `creative_sha256` +
служебный текст) + `BannerTiming` + `AdMarking`. Варианты A/B различаются креативами и/или
позициями и таймингами; вес — доля при назначении роликам.

* Новый вариант без `layers` строится из пресета: шаблон кампании (`banner_template_id`), иначе
  `domain.default_banner_variant(creative_sha256=…, micro_brand_text=<домен продукта>)`.
  Новый вариант — `draft`: роликам назначаются только `active`.
* Креативы из слоёв должны быть в библиотеке (не удалены и не `failed`), иначе 422 на слой.
* PATCH с `expected_version` (автосохранение редактора): не совпало — 409 `version_conflict`.
* Включение варианта (`active`) проверяет его `banner_validate`: ошибки (например, маркировка
  обязательна, а erid нет) — 422 с предупреждениями в `details.warnings`.
* `assign_variant` — детерминированный выбор по `video_uid` с весами (повтор стадии даёт тот же
  вариант), `pick_campaign` — активная кампания по таргетингу и приоритету.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from typing import Any

import psycopg

from autoshorts.core.errors import NotFound, ValidationFailed, VersionConflict
from autoshorts.domain.banner_schema import (
    AdMarking,
    BannerLayer,
    BannerLayerKind,
    BannerVariant,
    default_banner_variant,
)
from autoshorts.domain.enums import CampaignAllocation, VariantStatus
from autoshorts.repos import banner_creatives as creatives_repo
from autoshorts.repos import banners as templates_repo
from autoshorts.repos import campaigns as campaigns_repo
from autoshorts.repos import products as products_repo
from autoshorts.services import audit, banner_preview
from autoshorts.services import banner_creatives as creatives_service
from autoshorts.services.audit import Actor
from autoshorts.services.products import code_url_template, constraint_of, display_domain, field_error

TARGETING_KEYS = ("channel_ids", "niche_ids", "platforms", "kinds")


# ---------------------------------------------------------------- ответы


def campaign_to_api(row: dict[str, Any]) -> dict[str, Any]:
    targeting = row.get("targeting") if isinstance(row.get("targeting"), dict) else {}
    return {
        "id": row["id"],
        "product_id": row["product_id"],
        "name": row["name"],
        "status": row["status"],
        "banner_template_id": row["banner_template_id"],
        "allocation": row["allocation"],
        "priority": row["priority"],
        "targeting": {k: list(targeting.get(k) or []) for k in TARGETING_KEYS},
        "starts_at": row["starts_at"],
        "ends_at": row["ends_at"],
        "notes": row["notes"],
        "variants_count": int(row["variants_count"] or 0),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def layer_shas(layers: Sequence[Any]) -> list[str]:
    out: list[str] = []
    for layer in layers or []:
        sha = (
            layer.get("creative_sha256")
            if isinstance(layer, dict)
            else getattr(layer, "creative_sha256", None)
        )
        if sha and sha not in out:
            out.append(sha)
    return out


def variant_to_api(
    row: dict[str, Any], *, product: dict[str, Any] | None, creatives: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    layers = row.get("layers") or []
    return {
        "id": row["id"],
        "campaign_id": row["campaign_id"],
        "product_id": row["product_id"],
        "code": row["code"],
        "name": row["name"],
        "status": row["status"],
        "weight": row["weight"],
        "layers": layers,
        "timing": row.get("timing") or {},
        "ad_marking": row.get("ad_marking") or {},
        "code_url_template": row["code_url_template"],
        "effective_code_url_template": row["code_url_template"] or code_url_template(product),
        "voice_cta_text": row["voice_cta_text"],
        "pinned_comment_template": row["pinned_comment_template"],
        "description_cta_template": row["description_cta_template"],
        "notes": row["notes"],
        "creatives": [creatives_service.brief(creatives[s]) for s in layer_shas(layers) if s in creatives],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _variants_api(conn: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shas = [s for r in rows for s in layer_shas(r.get("layers") or [])]
    creatives = creatives_repo.get_many(conn, shas, include_deleted=True)
    products: dict[str, Any] = {}
    out = []
    for r in rows:
        pid = r["product_id"]
        if pid not in products:
            products[pid] = products_repo.get(conn, pid)
        out.append(variant_to_api(r, product=products[pid], creatives=creatives))
    return out


def variant_api(conn: Any, variant_id: int) -> dict[str, Any]:
    row = require_variant(conn, variant_id)
    return _variants_api(conn, [row])[0]


def to_domain_variant(row: dict[str, Any], product: dict[str, Any] | None) -> BannerVariant:
    """Строка `banner_variants` → доменная модель для banner_place / validate / overlay."""
    return BannerVariant(
        code=row["code"],
        name=row["name"],
        layers=row.get("layers") or [],
        timing=row.get("timing") or {},
        ad_marking=row.get("ad_marking") or {},
        code_url_template=row["code_url_template"] or code_url_template(product),
    )


# ---------------------------------------------------------------- кампании


def require_campaign(conn: Any, campaign_id: int, *, for_update: bool = False) -> dict[str, Any]:
    row = campaigns_repo.get_campaign(conn, campaign_id, for_update=for_update)
    if row is None:
        raise NotFound("campaign", "Кампания не найдена")
    return row


def _campaign_db_error(exc: psycopg.Error) -> Exception:
    name = constraint_of(exc)
    if "banner_template" in name:
        return field_error("banner_template_id", "Шаблон баннера не найден", "not_found")
    if "product" in name:
        return field_error("product_id", "Продукт не найден", "not_found")
    return field_error("body", "Данные кампании не прошли проверку БД", "db_check")


def list_campaigns(
    *, product_id: str | None, statuses: Sequence[str] | None, limit: int, offset: int
) -> dict:
    from autoshorts.db.pool import connection

    with connection() as conn:
        rows, total = campaigns_repo.list_campaigns(
            conn, product_id=product_id, statuses=statuses, limit=limit, offset=offset
        )
    return {"items": [campaign_to_api(r) for r in rows], "total": total, "limit": limit, "offset": offset}


def get_campaign(campaign_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return campaign_to_api(require_campaign(conn, campaign_id))


def _check_window(starts_at: Any, ends_at: Any) -> None:
    if starts_at is not None and ends_at is not None and ends_at <= starts_at:
        raise field_error("ends_at", "Конец кампании раньше начала", "range")


def create_campaign(fields: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    _check_window(fields.get("starts_at"), fields.get("ends_at"))
    try:
        with connection() as conn:
            campaign_id = campaigns_repo.insert_campaign(conn, {**fields, "created_by": actor.user_id})
            audit.record(
                conn,
                actor,
                "campaign.create",
                "campaign",
                campaign_id,
                summary=f"кампания «{fields['name']}» ({fields['product_id']})",
                diff={"product_id": fields["product_id"], "allocation": fields.get("allocation")},
            )
            return campaign_to_api(require_campaign(conn, campaign_id))
    except (psycopg.errors.ForeignKeyViolation, psycopg.errors.CheckViolation) as exc:
        raise _campaign_db_error(exc) from exc


def update_campaign(campaign_id: int, changes: dict[str, Any], actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    for key in ("name", "allocation", "priority", "targeting"):
        if key in changes and changes[key] is None:
            raise field_error(key, "Поле не может быть пустым", "none")
    try:
        with connection() as conn:
            current = require_campaign(conn, campaign_id, for_update=True)
            _check_window(
                changes.get("starts_at", current["starts_at"]), changes.get("ends_at", current["ends_at"])
            )
            campaigns_repo.update_campaign(conn, campaign_id, changes)
            audit.record(conn, actor, "campaign.update", "campaign", campaign_id, diff=dict(changes))
            return campaign_to_api(require_campaign(conn, campaign_id))
    except (psycopg.errors.ForeignKeyViolation, psycopg.errors.CheckViolation) as exc:
        raise _campaign_db_error(exc) from exc


def set_campaign_status(campaign_id: int, status: str, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        current = require_campaign(conn, campaign_id, for_update=True)
        campaigns_repo.update_campaign(conn, campaign_id, {"status": status})
        audit.record(
            conn,
            actor,
            "campaign.status",
            "campaign",
            campaign_id,
            summary=f"кампания: {current['status']} → {status}",
            diff={"from": current["status"], "to": status},
        )
        return campaign_to_api(require_campaign(conn, campaign_id))


# ---------------------------------------------------------------- варианты


def require_variant(conn: Any, variant_id: int, *, for_update: bool = False) -> dict[str, Any]:
    row = campaigns_repo.get_variant(conn, variant_id, for_update=for_update)
    if row is None:
        raise NotFound("banner_variant", "Вариант баннера не найден")
    return row


def check_layer_creatives(conn: Any, layers: Sequence[Any], *, loc: str = "layers") -> None:
    """Каждый `creative_sha256` слоёв есть в библиотеке и не сломан (processing допустим)."""
    shas = layer_shas(layers)
    found = creatives_repo.get_many(conn, shas)
    for idx, layer in enumerate(layers):
        sha = layer.get("creative_sha256") if isinstance(layer, dict) else layer.creative_sha256
        if not sha:
            continue
        row = found.get(sha)
        if row is None:
            raise field_error([loc, idx, "creative_sha256"], "Креатив не найден в библиотеке", "not_found")
        if row["status"] == "failed":
            raise field_error(
                [loc, idx, "creative_sha256"], "Креатив не прошёл нормализацию", "creative_failed"
            )


def _dump_layers(layers: Sequence[BannerLayer]) -> list[dict[str, Any]]:
    return [layer.model_dump(mode="json") for layer in layers]


def _with_creative(layers: list[BannerLayer], sha: str) -> list[BannerLayer]:
    """Поставить креатив в пресет: заменить sha у первого слоя creative или добавить его в зону оффера."""
    out = list(layers)
    idx = next((i for i, la in enumerate(out) if la.kind == BannerLayerKind.CREATIVE), None)
    if idx is not None:
        out[idx] = out[idx].model_copy(update={"creative_sha256": sha, "enabled": True})
    else:
        defaults = default_banner_variant(creative_sha256=sha).layers
        template = next(la for la in defaults if la.kind == BannerLayerKind.CREATIVE)
        plate_idx = next((i for i, la in enumerate(out) if la.kind == BannerLayerKind.FALLBACK_PLATE), None)
        slot = template.slot
        if plate_idx is not None:  # креатив встаёт в зону оффера шаблона — туда, где была плашка
            p = out[plate_idx].slot
            slot = slot.model_copy(update={"x": p.x, "y": p.y, "w": p.w, "h": p.h, "anchor": p.anchor})
        used = {la.id for la in out}
        new_id = template.id if template.id not in used else f"creative_{len(out)}"
        out.insert(
            len(out) if plate_idx is None else plate_idx,
            template.model_copy(update={"id": new_id, "slot": slot}),
        )
    return [
        la.model_copy(update={"enabled": False}) if la.kind == BannerLayerKind.FALLBACK_PLATE else la
        for la in out
    ]


def preset_for(
    conn: Any,
    campaign: dict[str, Any],
    product: dict[str, Any] | None,
    *,
    creative_sha256: str | None,
    ad_marking: AdMarking,
) -> BannerVariant:
    """Слои нового варианта: шаблон кампании или вариант «из коробки» с доменом продукта."""
    template = (
        templates_repo.get(conn, campaign["banner_template_id"]) if campaign["banner_template_id"] else None
    )
    if template is not None:
        preset = BannerVariant.model_validate({**(template["layout"] or {}), "code_url_template": "{code}"})
        layers = _with_creative(preset.layers, creative_sha256) if creative_sha256 else preset.layers
        return preset.model_copy(update={"layers": layers})
    domain = display_domain(product) or (product or {}).get("name") or "umka.chat"
    kwargs: dict[str, Any] = {
        "creative_sha256": creative_sha256,
        "micro_brand_text": domain,
        "erid": ad_marking.erid,
        "ad_marking_required": ad_marking.required,
    }
    if domain != "umka.chat":
        kwargs["offer_text"] = domain
    return default_banner_variant(**kwargs)


def _variant_db_error(exc: psycopg.Error) -> Exception:
    if isinstance(exc, psycopg.errors.UniqueViolation):
        return field_error("code", "Вариант с таким кодом в кампании уже есть", "unique")
    return field_error("body", "Данные варианта не прошли проверку БД", "db_check")


def list_variants(campaign_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        require_campaign(conn, campaign_id)
        return {"items": _variants_api(conn, campaigns_repo.list_variants(conn, campaign_id))}


def get_variant(variant_id: int) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        return variant_api(conn, variant_id)


def create_variant(campaign_id: int, body: Any, actor: Actor) -> dict[str, Any]:
    """`body` — `api.schemas.campaigns.BannerVariantCreate`."""
    from autoshorts.db.pool import connection

    try:
        with connection() as conn:
            campaign = require_campaign(conn, campaign_id, for_update=True)
            product = products_repo.get(conn, campaign["product_id"])
            if body.creative_sha256:
                check_layer_creatives(conn, [{"creative_sha256": body.creative_sha256}], loc="creative")
            if body.layers is not None:
                layers = list(body.layers)
                timing, marking = body.timing, body.ad_marking
            else:
                preset = preset_for(
                    conn, campaign, product, creative_sha256=body.creative_sha256, ad_marking=body.ad_marking
                )
                layers = preset.layers
                timing = body.timing if "timing" in body.model_fields_set else preset.timing
                marking = body.ad_marking if "ad_marking" in body.model_fields_set else preset.ad_marking
            check_layer_creatives(conn, layers)
            banner_preview.check_fonts(layers)
            if campaigns_repo.code_taken(conn, campaign_id, body.code):
                raise field_error("code", "Вариант с таким кодом в кампании уже есть", "unique")
            variant_id = campaigns_repo.insert_variant(
                conn,
                {
                    "campaign_id": campaign_id,
                    "code": body.code,
                    "name": body.name.strip(),
                    "status": str(body.status),
                    "weight": body.weight,
                    "layers": _dump_layers(layers),
                    "timing": timing.model_dump(mode="json", exclude_unset=True),
                    "ad_marking": marking.model_dump(mode="json"),
                    "code_url_template": body.code_url_template,
                    "voice_cta_text": body.voice_cta_text,
                    "pinned_comment_template": body.pinned_comment_template,
                    "description_cta_template": body.description_cta_template,
                    "notes": body.notes,
                    "created_by": actor.user_id,
                },
            )
            if body.status == VariantStatus.ACTIVE:
                # сразу в ротацию — та же проверка, что при активации (38-ФЗ: erid, мёртвые зоны)
                _require_valid(conn, variant_id)
            audit.record(
                conn,
                actor,
                "banner_variant.create",
                "banner_variant",
                variant_id,
                summary=f"вариант {body.code} кампании {campaign_id}",
                diff={
                    "campaign_id": campaign_id,
                    "code": body.code,
                    "creatives": layer_shas(_dump_layers(layers)),
                },
            )
            return variant_api(conn, variant_id)
    except psycopg.errors.UniqueViolation as exc:
        raise _variant_db_error(exc) from exc


def _json_value(key: str, value: Any) -> Any:
    if key == "layers":
        return _dump_layers(value)
    if key == "timing":
        return value.model_dump(mode="json", exclude_unset=True)
    if key == "ad_marking":
        return value.model_dump(mode="json")
    if key == "status":
        return str(value)
    return value


def update_variant(variant_id: int, body: Any, actor: Actor) -> dict[str, Any]:
    """`body` — `BannerVariantPatch`: только переданные поля; `layers` заменяются целиком."""
    from autoshorts.db.pool import connection

    changes = {k: getattr(body, k) for k in body.model_fields_set if k != "expected_version"}
    for key in ("name", "weight", "layers", "timing", "ad_marking"):
        if key in changes and changes[key] is None:
            raise field_error(key, "Поле не может быть пустым", "none")
    with connection() as conn:
        current = require_variant(conn, variant_id, for_update=True)
        if body.expected_version is not None and int(current["version"]) != int(body.expected_version):
            raise VersionConflict(
                "Вариант уже изменили в другой вкладке — обновите редактор",
                details={"current_version": int(current["version"])},
            )
        if "layers" in changes:
            check_layer_creatives(conn, changes["layers"])
            banner_preview.check_fonts(changes["layers"])
        fields = {k: _json_value(k, v) for k, v in changes.items()}
        if "name" in fields:
            fields["name"] = str(fields["name"]).strip()
        campaigns_repo.update_variant(conn, variant_id, fields)
        if current["status"] == VariantStatus.ACTIVE and ({"layers", "timing", "ad_marking"} & set(fields)):
            _require_valid(conn, variant_id)
        audit.record(
            conn,
            actor,
            "banner_variant.update",
            "banner_variant",
            variant_id,
            summary=f"правка варианта {current['code']}",
            diff={"fields": sorted(fields), "version": int(current["version"]) + 1},
        )
        return variant_api(conn, variant_id)


def variant_errors(conn: Any, variant_id: int) -> list[Any]:
    """Ошибки (severity=error) проверки варианта на YouTube-профиле, ролик 30 с — как при активации."""
    row = require_variant(conn, variant_id)
    product = products_repo.get(conn, row["product_id"])
    variant = to_domain_variant(row, product)
    creatives = creatives_service.domain_creatives(conn, variant.creative_shas())
    result, _ = banner_preview.validate_domain_variant(
        variant, creatives, platform="youtube", duration_sec=30.0
    )
    return [w for w in result.warnings if w.severity.value == "error"]


def _require_valid(conn: Any, variant_id: int) -> None:
    """Активный вариант не должен иметь ошибок проверки (на YouTube-профиле, ролик 30 с)."""
    errors = variant_errors(conn, variant_id)
    if errors:
        raise ValidationFailed(
            "У варианта есть ошибки баннера — активировать нельзя",
            details={
                "errors": [
                    {"loc": ["body", "status"], "msg": w.message or w.code.value, "type": w.code.value}
                    for w in errors
                ],
                "warnings": [w.model_dump(mode="json") for w in errors],
            },
        )


def set_variant_status(variant_id: int, status: str, actor: Actor) -> dict[str, Any]:
    from autoshorts.db.pool import connection

    with connection() as conn:
        current = require_variant(conn, variant_id, for_update=True)
        if status == VariantStatus.ACTIVE and current["status"] != VariantStatus.ACTIVE:
            _require_valid(conn, variant_id)
        campaigns_repo.update_variant(conn, variant_id, {"status": status}, bump_version=False)
        audit.record(
            conn,
            actor,
            "banner_variant.status",
            "banner_variant",
            variant_id,
            summary=f"вариант {current['code']}: {current['status']} → {status}",
            diff={"from": current["status"], "to": status},
        )
        return variant_api(conn, variant_id)


def duplicate_variant(
    variant_id: int, *, code: str, name: str | None, campaign_id: int | None, actor: Actor
) -> dict:
    from autoshorts.db.pool import connection

    try:
        with connection() as conn:
            src = require_variant(conn, variant_id)
            target = campaign_id or int(src["campaign_id"])
            if campaign_id is not None and campaigns_repo.get_campaign(conn, campaign_id) is None:
                raise field_error("campaign_id", "Кампания не найдена", "not_found")
            if campaigns_repo.code_taken(conn, target, code):
                raise field_error("code", "Вариант с таким кодом в кампании уже есть", "unique")
            new_id = campaigns_repo.insert_variant(
                conn,
                {
                    "campaign_id": target,
                    "code": code,
                    "name": (name or f"{src['name']} (копия)")[:200],
                    "status": str(VariantStatus.DRAFT),
                    "weight": src["weight"],
                    "layers": src["layers"] or [],
                    "timing": src["timing"] or {},
                    "ad_marking": src["ad_marking"] or {},
                    "code_url_template": src["code_url_template"],
                    "voice_cta_text": src["voice_cta_text"],
                    "pinned_comment_template": src["pinned_comment_template"],
                    "description_cta_template": src["description_cta_template"],
                    "notes": src["notes"],
                    "created_by": actor.user_id,
                },
            )
            audit.record(
                conn,
                actor,
                "banner_variant.duplicate",
                "banner_variant",
                new_id,
                summary=f"копия варианта {src['code']} → {code}",
                diff={"source_id": variant_id, "campaign_id": target},
            )
            return variant_api(conn, new_id)
    except psycopg.errors.UniqueViolation as exc:
        raise _variant_db_error(exc) from exc


# ---------------------------------------------------------------- назначение роликам


def choose_weighted(variants: Sequence[tuple[int, int]], key: str) -> int | None:
    """Детерминированный выбор по ключу с весами: один и тот же ключ — один и тот же вариант.

    `variants` — [(variant_id, weight)]; нулевые веса не участвуют. Точка — blake2b(key) по модулю
    суммы весов (64 бита: смещение от модуля пренебрежимо).
    """
    pool = sorted((int(v), int(w)) for v, w in variants if int(w) > 0)
    total = sum(w for _, w in pool)
    if total <= 0:
        return None
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=8).digest()
    point = int.from_bytes(digest, "big") % total
    acc = 0
    for vid, w in pool:
        acc += w
        if point < acc:
            return vid
    return pool[-1][0]


def assign_variant(conn: Any, campaign: dict[str, Any], video_uid: str) -> dict[str, Any] | None:
    """Вариант кампании для ролика: `weighted` — по `video_uid` с весами, `round_robin` — по кругу
    среди активных, `manual` — не назначается (оператор выбирает сам). Только `active` варианты."""
    allocation = campaign["allocation"]
    if allocation == CampaignAllocation.MANUAL:
        return None
    variants = campaigns_repo.list_variants(conn, int(campaign["id"]), statuses=[str(VariantStatus.ACTIVE)])
    if not variants:
        return None
    if allocation == CampaignAllocation.ROUND_ROBIN:
        pool = [v for v in variants if int(v["weight"]) > 0] or variants
        return pool[campaigns_repo.count_jobs(conn, int(campaign["id"])) % len(pool)]
    chosen = choose_weighted([(v["id"], v["weight"]) for v in variants], f"{campaign['id']}:{video_uid}")
    return next((v for v in variants if v["id"] == chosen), None)


def targeting_matches(
    targeting: dict[str, Any] | None,
    *,
    channel_id: str | None,
    niche_id: str | None,
    platform: str | None,
    kind: str | None,
) -> bool:
    t = targeting or {}
    for key, value in (
        ("channel_ids", channel_id),
        ("niche_ids", niche_id),
        ("platforms", platform),
        ("kinds", kind),
    ):
        allowed = t.get(key) or []
        if allowed and value not in allowed:
            return False
    return True


def pick_campaign(
    conn: Any,
    *,
    product_id: str | None = None,
    channel_id: str | None = None,
    niche_id: str | None = None,
    platform: str | None = None,
    kind: str | None = None,
) -> dict[str, Any] | None:
    """Активная кампания с подходящим таргетингом и наименьшим `priority`."""
    for campaign in campaigns_repo.active_campaigns(conn, product_id=product_id):
        if targeting_matches(
            campaign.get("targeting"), channel_id=channel_id, niche_id=niche_id, platform=platform, kind=kind
        ):
            return campaign
    return None


def assign_banner(
    conn: Any,
    *,
    video_uid: str,
    channel_id: str | None = None,
    niche_id: str | None = None,
    platform: str | None = None,
    kind: str | None = None,
    product_id: str | None = None,
    campaign_id: int | None = None,
) -> dict[str, Any] | None:
    """Кампания и вариант для нового ролика → `{campaign_id, product_id, banner_variant_id}` или None.

    Для стадий и `POST /jobs` (M2-B/M2-C): явная кампания главнее подбора по таргетингу.
    """
    campaign = (
        campaigns_repo.get_campaign(conn, campaign_id)
        if campaign_id is not None
        else pick_campaign(
            conn,
            product_id=product_id,
            channel_id=channel_id,
            niche_id=niche_id,
            platform=platform,
            kind=kind,
        )
    )
    if campaign is None:
        return None
    variant = assign_variant(conn, campaign, video_uid)
    return {
        "campaign_id": int(campaign["id"]),
        "product_id": campaign["product_id"],
        "banner_variant_id": int(variant["id"]) if variant else None,
    }


def variant_snapshot(conn: Any, variant_id: int) -> dict[str, Any]:
    """Замороженный вариант для `jobs.config_snapshot`: доменная модель + креативы (M2-B рендер)."""
    row = require_variant(conn, variant_id)
    product = products_repo.get(conn, row["product_id"])
    variant = to_domain_variant(row, product)
    creatives = creatives_service.domain_creatives(conn, variant.creative_shas())
    return {
        "banner_variant_id": variant_id,
        "variant": variant.model_dump(mode="json"),
        "creatives": {sha: c.model_dump(mode="json") for sha, c in creatives.items()},
    }

"""Серверная проверка баннера — источник истины для редактора и рендера (ARCHITECTURE §5.2 validate).

    result = validate_variant(variant, creatives, profiles=[common, youtube], duration_sec=21.4)
    result.warnings            # [BannerValidationWarning], сначала error, затем warn, info
    validate_upload(path)      # до приёма файла: file_too_large / unsupported_format / …

Проверки (коды латиницей, `BannerValidationCode`):
* `creative_outside_safe_zone`, `creative_in_bottom_dead_zone`, `micro_brand_in_top_bar` — по
  каждому профилю safe zone (площадке);
* `creative_too_small_to_read` (итоговая высота/ширина на канве 1080×1920 ниже порога),
  `creative_upscaled_blurry` (увеличение больше `max_upscale_factor`),
  `creative_aspect_distorted` (не должно случаться при contain — страховка), `creative_clipped_by_slot`;
* `animated_too_long` (длиннее лимита), `animated_cut_by_window` (длиннее окна показа);
* `erid_missing_when_required` — маркировка обязательна, а erid или слоя «Реклама · erid» нет;
* `banner_overlaps_captions_zone` — баннер пересекает заданную зону субтитров,
  `captions_zone_too_small` — свободного места под субтитры меньше минимума;
* `file_too_large`, `unsupported_format` (+ прочие коды приёма) — `validate_upload`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from autoshorts.domain.banner_schema import (
    CANVAS_H,
    CANVAS_W,
    BannerCreative,
    BannerLayerKind,
    BannerValidationCode,
    BannerValidationWarning,
    BannerVariant,
    CreativeKind,
    Rect,
    SafeZoneProfile,
    TimeWindow,
    ValidationThresholds,
    WarningSeverity,
)
from autoshorts.pipeline.banner_assets import (
    DEFAULT_LIMITS,
    AssetLimits,
    BannerAssetError,
    check_limits,
    sniff_file,
)
from autoshorts.pipeline.banner_overlay import BannerPlan, compute_windows
from autoshorts.pipeline.banner_place import (
    CaptionsZone,
    VariantPlacement,
    check_safe_zone,
    place_variant,
)

_SEVERITY_ORDER = {WarningSeverity.ERROR: 0, WarningSeverity.WARN: 1, WarningSeverity.INFO: 2}
_UPLOAD_CODES = {
    "file_too_large": BannerValidationCode.FILE_TOO_LARGE,
    "unsupported_format": BannerValidationCode.UNSUPPORTED_FORMAT,
    "animated_too_long": BannerValidationCode.ANIMATED_TOO_LONG,
}
_SIDE_RU = {"top": "сверху", "bottom": "снизу", "left": "слева", "right": "справа"}


@dataclass
class BannerValidateResult:
    warnings: list[BannerValidationWarning]
    placement: VariantPlacement
    captions_zone: CaptionsZone
    windows: list[TimeWindow] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(w.severity == WarningSeverity.ERROR for w in self.warnings)

    def codes(self) -> set[str]:
        return {w.code.value for w in self.warnings}


def _w(
    code: BannerValidationCode,
    message: str,
    *,
    severity: WarningSeverity = WarningSeverity.WARN,
    layer_id: str | None = None,
    profile_id: str | None = None,
    **details: object,
) -> BannerValidationWarning:
    return BannerValidationWarning(
        code=code,
        severity=severity,
        layer_id=layer_id,
        profile_id=profile_id,
        message=message,
        details=details,
    )


def validate_upload(path: Path, limits: AssetLimits = DEFAULT_LIMITS) -> list[BannerValidationWarning]:
    """Быстрая проверка файла до приёма (по сигнатуре и заголовку, без декодирования)."""
    path = Path(path)
    try:
        sniff = sniff_file(path)
        check_limits(sniff, path.stat().st_size, limits)
    except BannerAssetError as exc:
        code = _UPLOAD_CODES.get(exc.code, BannerValidationCode.UNSUPPORTED_FORMAT)
        details = {k: v for k, v in exc.details.items() if isinstance(v, str | int | float | bool | None)}
        return [_w(code, exc.message, severity=WarningSeverity.ERROR, asset_error=exc.code, **details)]
    except OSError as exc:
        return [
            _w(
                BannerValidationCode.UNSUPPORTED_FORMAT,
                "Файл не читается",
                severity=WarningSeverity.ERROR,
                error=str(exc),
            )
        ]
    return []


def _safe_zone_checks(
    placement: VariantPlacement, profiles: Sequence[SafeZoneProfile]
) -> list[BannerValidationWarning]:
    out: list[BannerValidationWarning] = []
    canvas = Rect(x=0, y=0, w=CANVAS_W, h=CANVAS_H)
    for pl in placement.layers:
        if pl.rect.is_empty:
            continue
        outside = pl.fit.canvas_clipped if pl.fit is not None else not canvas.contains(pl.rect)
        if outside:
            full = pl.fit.full_rect if pl.fit is not None else pl.rect
            out.append(
                _w(
                    BannerValidationCode.LAYER_OUTSIDE_CANVAS,
                    "Слой выходит за край кадра и будет обрезан",
                    layer_id=pl.layer_id,
                    rect=full.edges(),
                )
            )
        for prof in profiles:
            rep = check_safe_zone(pl.rect, prof)
            if rep.inside:
                continue
            sides = rep.sides
            if pl.kind == BannerLayerKind.MICRO_BRAND and "top" in sides:
                out.append(
                    _w(
                        BannerValidationCode.MICRO_BRAND_IN_TOP_BAR,
                        f"Микро-бренд заходит в верхнюю панель площадки на {rep.overflow['top']} px",
                        layer_id=pl.layer_id,
                        profile_id=prof.id,
                        overflow_px=rep.overflow["top"],
                    )
                )
                sides = [s for s in sides if s != "top"]
            if pl.kind != BannerLayerKind.MICRO_BRAND and "bottom" in sides:
                out.append(
                    _w(
                        BannerValidationCode.CREATIVE_IN_BOTTOM_DEAD_ZONE,
                        f"Нижний край на {rep.overflow['bottom']} px в зоне подписи и кнопок площадки",
                        layer_id=pl.layer_id,
                        profile_id=prof.id,
                        overflow_px=rep.overflow["bottom"],
                    )
                )
                sides = [s for s in sides if s != "bottom"]
            if sides:
                human = ", ".join(f"{_SIDE_RU[s]} {rep.overflow[s]} px" for s in sides)
                out.append(
                    _w(
                        BannerValidationCode.CREATIVE_OUTSIDE_SAFE_ZONE,
                        f"Слой выходит за безопасную зону: {human}",
                        layer_id=pl.layer_id,
                        profile_id=prof.id,
                        overflow={s: rep.overflow[s] for s in sides},
                        outside_fraction=rep.outside_fraction,
                    )
                )
    return out


def _creative_checks(
    placement: VariantPlacement,
    creatives: Mapping[str, BannerCreative],
    th: ValidationThresholds,
    windows: Sequence[TimeWindow],
    offer_max_h: int,
) -> list[BannerValidationWarning]:
    out: list[BannerValidationWarning] = []
    for pl in placement.layers:
        fit = pl.fit
        if fit is None or not pl.creative_sha256:
            continue
        creative = creatives.get(pl.creative_sha256)
        if pl.kind == BannerLayerKind.CREATIVE and (
            pl.rect.h < th.min_creative_height_px or pl.rect.w < th.min_creative_width_px
        ):
            out.append(
                _w(
                    BannerValidationCode.CREATIVE_TOO_SMALL_TO_READ,
                    f"Креатив {pl.rect.w}×{pl.rect.h} px — мелко для телефона "
                    f"(минимум {th.min_creative_width_px}×{th.min_creative_height_px})",
                    layer_id=pl.layer_id,
                    width=pl.rect.w,
                    height=pl.rect.h,
                )
            )
        if fit.upscale_factor > th.max_upscale_factor:
            out.append(
                _w(
                    BannerValidationCode.CREATIVE_UPSCALED_BLURRY,
                    f"Креатив растянут в {fit.upscale_factor:.2f} раза — будет мыльным, загрузите крупнее",
                    layer_id=pl.layer_id,
                    upscale_factor=round(fit.upscale_factor, 3),
                    source=[fit.src_w, fit.src_h],
                )
            )
        if fit.aspect_error > th.max_aspect_error:
            out.append(
                _w(
                    BannerValidationCode.CREATIVE_ASPECT_DISTORTED,
                    "Пропорции креатива искажены",
                    severity=WarningSeverity.ERROR,
                    layer_id=pl.layer_id,
                    aspect_error=round(fit.aspect_error, 4),
                )
            )
        if fit.clipped and pl.kind == BannerLayerKind.CREATIVE:
            out.append(
                _w(
                    BannerValidationCode.CREATIVE_CLIPPED_BY_SLOT,
                    "Часть креатива обрезана слотом (cover, none или масштаб больше 1)",
                    severity=WarningSeverity.INFO,
                    layer_id=pl.layer_id,
                    visible_crop=fit.src_crop.edges(),
                )
            )
        if pl.kind == BannerLayerKind.CREATIVE and pl.rect.h > offer_max_h:
            out.append(
                _w(
                    BannerValidationCode.OFFER_AREA_TOO_TALL,
                    f"Креатив выше {offer_max_h} px: плашка закрывает много кадра",
                    severity=WarningSeverity.INFO,
                    layer_id=pl.layer_id,
                    height=pl.rect.h,
                )
            )
        if creative is not None and creative.kind == CreativeKind.ANIMATED:
            if creative.duration_sec > th.max_animated_sec + 0.05:
                out.append(
                    _w(
                        BannerValidationCode.ANIMATED_TOO_LONG,
                        f"Анимация {creative.duration_sec:.1f} с, лимит {th.max_animated_sec:.0f} с",
                        layer_id=pl.layer_id,
                        duration_sec=creative.duration_sec,
                    )
                )
            shortest = min((w.duration for w in windows), default=None)
            if shortest is not None and creative.duration_sec > shortest + 0.05:
                out.append(
                    _w(
                        BannerValidationCode.ANIMATED_CUT_BY_WINDOW,
                        "Анимация длиннее окна показа: зритель увидит только начало",
                        severity=WarningSeverity.INFO,
                        layer_id=pl.layer_id,
                        animation_sec=creative.duration_sec,
                        window_sec=round(shortest, 3),
                    )
                )
    return out


def _erid_checks(variant: BannerVariant) -> list[BannerValidationWarning]:
    marking = variant.ad_marking
    erid_layers = variant.layers_of(BannerLayerKind.TEXT_ERID)
    out: list[BannerValidationWarning] = []
    if marking.required and (not marking.erid or not erid_layers):
        missing = "erid" if not marking.erid else "слой «Реклама · erid»"
        out.append(
            _w(
                BannerValidationCode.ERID_MISSING_WHEN_REQUIRED,
                f"Маркировка обязательна, но нет: {missing}",
                severity=WarningSeverity.ERROR,
                has_erid=bool(marking.erid),
                has_layer=bool(erid_layers),
            )
        )
    elif erid_layers and not marking.erid and any("{erid}" in (la.text or "") for la in erid_layers):
        out.append(
            _w(
                BannerValidationCode.ERID_MISSING_WHEN_REQUIRED,
                "Слой «Реклама · erid» включён, но erid не задан — слой не будет показан",
                layer_id=erid_layers[0].id,
            )
        )
    return out


def validate_variant(
    variant: BannerVariant,
    creatives: Mapping[str, BannerCreative],
    *,
    profiles: Sequence[SafeZoneProfile],
    duration_sec: float | None = None,
    thresholds: ValidationThresholds | None = None,
    captions_zone: Rect | None = None,
    plan: BannerPlan | None = None,
) -> BannerValidateResult:
    """Все проверки варианта. `profiles[0]` — основная площадка (по ней считается зона субтитров).

    `captions_zone` — фиксированная зона субтитров (если рендер её не подстраивает);
    `plan` — готовый план рендера: берутся точные размеры текста и его предупреждения.
    """
    if not profiles:
        raise ValueError("at least one safe zone profile is required")
    th = thresholds or ValidationThresholds()
    primary = profiles[0]
    if plan is not None:
        placement = plan.placement
        windows = plan.windows.get("with_creative", [])
    else:
        sizes = {sha: (c.width, c.height) for sha, c in creatives.items()}
        placement = place_variant(variant, sizes, primary, captions_min_h=th.min_captions_zone_h_px)
        windows = compute_windows(variant.timing, duration_sec)[0] if duration_sec else []
    warnings: list[BannerValidationWarning] = []
    warnings += _safe_zone_checks(placement, profiles)
    offer_max = primary.offer_max_height_px or th.offer_max_height_px
    warnings += _creative_checks(placement, creatives, th, windows, offer_max)
    warnings += _erid_checks(variant)
    for sha in placement.missing_creatives:
        warnings.append(
            _w(
                BannerValidationCode.CREATIVE_MISSING,
                "Креатив не найден — будет показана запасная плашка",
                creative_sha256=sha,
            )
        )
    if placement.fallback_used:
        warnings.append(
            _w(
                BannerValidationCode.FALLBACK_PLATE_USED,
                "Креатива нет — показывается запасная текстовая плашка",
                severity=WarningSeverity.INFO,
            )
        )
    zone = placement.captions_zone
    if not zone.fits:
        warnings.append(
            _w(
                BannerValidationCode.CAPTIONS_ZONE_TOO_SMALL,
                f"Под субтитры остаётся {zone.rect.h} px — меньше минимума {th.min_captions_zone_h_px}",
                profile_id=primary.id,
                zone=zone.rect.edges(),
            )
        )
    if captions_zone is not None:
        for pl in placement.layers:
            inter = pl.rect.intersect(captions_zone)
            if inter is not None:
                warnings.append(
                    _w(
                        BannerValidationCode.BANNER_OVERLAPS_CAPTIONS_ZONE,
                        f"Слой перекрывает зону субтитров на {inter.h} px по высоте",
                        layer_id=pl.layer_id,
                        overlap=inter.edges(),
                    )
                )
    if plan is not None:
        warnings += plan.warnings
    return BannerValidateResult(_dedupe(warnings), placement, zone, list(windows))


def _dedupe(warnings: list[BannerValidationWarning]) -> list[BannerValidationWarning]:
    seen: set[tuple[str, str | None, str | None]] = set()
    out: list[BannerValidationWarning] = []
    for w in warnings:
        key = (w.code.value, w.layer_id, w.profile_id)
        if key in seen:
            continue
        seen.add(key)
        out.append(w)
    return sorted(out, key=lambda w: _SEVERITY_ORDER[w.severity])

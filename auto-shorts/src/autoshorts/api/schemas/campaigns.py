"""Кампании и варианты баннера (docs/ARCHITECTURE.md §5.2, таблицы campaigns, banner_variants).

Решение владельца от 2026-09-26: вариант A/B — это набор слоёв `domain.banner_schema.BannerLayer`
(загруженный креатив по `creative_sha256` + слот + служебный текст), тайминг `BannerTiming` и
маркировка `AdMarking`. Варианты различаются креативами и/или их позициями и таймингами; вес —
доля при назначении (`services/campaigns.assign_variant`). Миграция 0011: `banner_variants.layers`,
`timing`, `code_url_template`, `version`; текстовые колонки плашки переехали в слой `fallback_plate`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from autoshorts.api.schemas.banners import (
    MAX_LAYERS,
    SHA256_PATTERN,
    VARIANT_CODE_PATTERN,
    AdMarking,
    BannerCreativeBrief,
    BannerLayer,
    BannerTiming,
    BannerVariantDraft,
    CodeUrlTemplate,
    check_domain_variant,
)
from autoshorts.api.schemas.common import ApiModel, PageMeta, RequestModel, Slug
from autoshorts.domain.banner_schema import BannerVariant as DomainBannerVariant
from autoshorts.domain.enums import CampaignAllocation, CampaignStatus, JobKind, Platform, VariantStatus

__all__ = [
    "VARIANT_CODE_PATTERN",
    "BannerVariant",
    "BannerVariantCreate",
    "BannerVariantDraft",
    "BannerVariantDuplicate",
    "BannerVariantList",
    "BannerVariantPatch",
    "Campaign",
    "CampaignCreate",
    "CampaignPage",
    "CampaignPatch",
    "CampaignStatusChange",
    "CampaignTargeting",
    "VariantStatusChange",
]


class CampaignTargeting(RequestModel):
    """Пустой список — без ограничения по измерению."""

    channel_ids: list[Slug] = Field(default_factory=list)
    niche_ids: list[Slug] = Field(default_factory=list)
    platforms: list[Platform] = Field(default_factory=list)
    kinds: list[JobKind] = Field(default_factory=list)


class Campaign(ApiModel):
    id: int
    product_id: str
    name: str
    status: CampaignStatus
    banner_template_id: int | None = Field(description="Пресет для новых вариантов (необязательно)")
    allocation: CampaignAllocation
    priority: int
    targeting: CampaignTargeting
    starts_at: datetime | None
    ends_at: datetime | None
    notes: str | None
    variants_count: int
    created_at: datetime
    updated_at: datetime


class CampaignPage(PageMeta):
    items: list[Campaign]


class CampaignCreate(RequestModel):
    product_id: Slug
    name: str = Field(min_length=1, max_length=200)
    banner_template_id: int | None = Field(default=None, description="Пресет для новых вариантов")
    allocation: CampaignAllocation = CampaignAllocation.WEIGHTED
    priority: int = Field(default=100, ge=0, le=1000)
    targeting: CampaignTargeting = Field(default_factory=CampaignTargeting)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    notes: str | None = None


class CampaignPatch(RequestModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    banner_template_id: int | None = None
    allocation: CampaignAllocation | None = None
    priority: int | None = Field(default=None, ge=0, le=1000)
    targeting: CampaignTargeting | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    notes: str | None = None


class CampaignStatusChange(RequestModel):
    status: CampaignStatus


# ---------------------------------------------------------------- варианты


class _VariantTexts(RequestModel):
    voice_cta_text: str | None = Field(
        default=None, max_length=300, description="Произносимый CTA — уходит в сценарий ролика"
    )
    pinned_comment_template: str | None = Field(default=None, max_length=2000)
    description_cta_template: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=2000)


class BannerVariantCreate(_VariantTexts):
    """Новый вариант кампании. Без `layers` сервер строит слои по умолчанию
    (`domain.banner_schema.default_banner_variant`): микро-бренд продукта, креатив `creative_sha256`
    в зоне оффера, код ролика под ним; без креатива — запасная плашка.
    """

    code: str = Field(pattern=VARIANT_CODE_PATTERN, description="A, B, C — уникален в кампании")
    name: str = Field(min_length=1, max_length=200)
    status: VariantStatus = Field(
        default=VariantStatus.DRAFT, description="draft не назначается роликам, пока не станет active"
    )
    weight: int = Field(default=50, ge=0, le=1000)
    layers: list[BannerLayer] | None = Field(default=None, max_length=MAX_LAYERS)
    creative_sha256: str | None = Field(
        default=None, pattern=SHA256_PATTERN, description="Только без layers: креатив для слоёв по умолчанию"
    )
    timing: BannerTiming = Field(default_factory=BannerTiming)
    ad_marking: AdMarking = Field(default_factory=AdMarking)
    code_url_template: CodeUrlTemplate | None = Field(
        default=None, description="«u.umka.chat/{code}»; null — `<redirect_host продукта>/{code}`"
    )

    @model_validator(mode="after")
    def _layers_rules(self) -> BannerVariantCreate:
        if self.layers is not None:
            if self.creative_sha256 is not None:
                raise ValueError("creative_sha256 is only used when layers is omitted")
            check_domain_variant(self.layers)
        return self


class BannerVariantPatch(_VariantTexts):
    """Частичная правка. `layers` заменяются целиком (редактор шлёт весь список)."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    weight: int | None = Field(default=None, ge=0, le=1000)
    layers: list[BannerLayer] | None = Field(default=None, max_length=MAX_LAYERS)
    timing: BannerTiming | None = None
    ad_marking: AdMarking | None = None
    code_url_template: CodeUrlTemplate | None = None
    expected_version: int | None = Field(
        default=None, ge=1, description="Автосохранение редактора: не совпало — 409 version_conflict"
    )

    @model_validator(mode="after")
    def _layers_rules(self) -> BannerVariantPatch:
        if self.layers is not None:
            check_domain_variant(self.layers)
        return self


class BannerVariantDuplicate(RequestModel):
    """Копия варианта для A/B: те же слои и тайминг, дальше меняется креатив или позиция."""

    code: str = Field(pattern=VARIANT_CODE_PATTERN)
    name: str | None = Field(
        default=None, min_length=1, max_length=200, description="Пусто — «<имя> (копия)»"
    )
    campaign_id: int | None = Field(default=None, description="В другую кампанию; пусто — в ту же")


class VariantStatusChange(RequestModel):
    status: VariantStatus


class BannerVariant(ApiModel):
    id: int
    campaign_id: int
    product_id: str
    code: str
    name: str
    status: VariantStatus
    weight: int
    layers: list[BannerLayer]
    timing: BannerTiming
    ad_marking: AdMarking
    code_url_template: str | None = Field(description="null — шаблон продукта")
    effective_code_url_template: str = Field(description="Что реально печатается: «u.umka.chat/{code}»")
    voice_cta_text: str | None
    pinned_comment_template: str | None
    description_cta_template: str | None
    notes: str | None
    creatives: list[BannerCreativeBrief] = Field(description="Креативы из слоёв, по порядку слоёв")
    version: int = Field(description="Передавать как expected_version при правке")
    created_at: datetime
    updated_at: datetime

    def to_draft(self) -> BannerVariantDraft:
        return BannerVariantDraft(
            code=self.code,
            name=self.name,
            layers=self.layers,
            timing=self.timing,
            ad_marking=self.ad_marking,
            code_url_template=self.code_url_template,
        )

    def to_domain(self) -> DomainBannerVariant:
        """Доменная модель для `banner_place`/`banner_validate`/`banner_overlay` и `config_snapshot`."""
        return self.to_draft().to_domain(default_code_url_template=self.effective_code_url_template)


class BannerVariantList(ApiModel):
    items: list[BannerVariant]

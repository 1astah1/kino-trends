"""Enum домена и API совпадают с CHECK-ограничениями миграций (§4.3: единый словарь)."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path

import pytest

from autoshorts.api.schemas.banners import CreativeRejectCode, CreativeStatus
from autoshorts.api.schemas.budget import BudgetReleaseReason, BudgetReservationStatus
from autoshorts.api.schemas.clipping import CandidateOrigin, OriginalityVerdict
from autoshorts.api.schemas.conversions import IngestOutcome
from autoshorts.api.schemas.metrics import CollectorReason, CollectorState
from autoshorts.api.schemas.packages import PackageTextsMode
from autoshorts.api.schemas.quota import QuotaApi
from autoshorts.domain import banner_schema as B
from autoshorts.domain import enums as E

MIGRATIONS = Path(__file__).resolve().parents[2] / "src" / "autoshorts" / "db" / "migrations"
CHECK_IN = re.compile(r"CHECK\s*\(\s*(\w+)\s+IN\s*\(([^)]*)\)\s*\)", re.S)
TABLE = re.compile(r"^\s*(?:CREATE TABLE|ALTER TABLE)\s+(\w+)", re.S | re.I)


def _strip_comments(sql: str) -> str:
    return re.sub(r"--[^\n]*", "", sql)


def _checks() -> dict[tuple[str, str], set[str]]:
    out: dict[tuple[str, str], set[str]] = {}
    for f in sorted(MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql")):
        for stmt in _strip_comments(f.read_text(encoding="utf-8")).split(";"):
            m = TABLE.match(stmt)
            if not m:
                continue
            for col, values in CHECK_IN.findall(stmt):
                out[(m.group(1), col)] = set(re.findall(r"'([^']*)'", values))
    return out


EXPECTED: list[tuple[str, str, type[StrEnum]]] = [
    ("users", "role", E.Role),
    ("audit_log", "actor_kind", E.ActorKind),
    ("provider_configs", "kind", E.ProviderKind),
    ("alerts", "severity", E.AlertSeverity),
    ("alerts", "status", E.AlertStatus),
    ("products", "kind", E.ProductKind),
    ("products", "status", E.ProductStatus),
    ("products", "redirect_mode", E.RedirectMode),
    ("products", "telegram_link_style", E.TelegramLinkStyle),
    ("niches", "status", E.NicheStatus),
    ("niche_versions", "source", E.NicheVersionSource),
    ("channels", "status", E.ChannelStatus),
    ("channels", "platform", E.Platform),
    ("channel_incidents", "kind", E.IncidentKind),
    ("assets", "kind", E.AssetKind),
    ("banner_templates", "status", E.BannerTemplateStatus),
    ("campaigns", "status", E.CampaignStatus),
    ("campaigns", "allocation", E.CampaignAllocation),
    ("banner_variants", "status", E.VariantStatus),
    ("jobs", "kind", E.JobKind),
    ("jobs", "status", E.JobStatus),
    ("jobs", "reframe_mode", E.ReframeMode),
    ("tasks", "resource_class", E.ResourceClass),
    ("tasks", "status", E.TaskStatus),
    ("schedules", "resource_class", E.ResourceClass),
    ("review_decisions", "decision", E.ReviewDecision),
    ("review_decisions", "decided_via", E.DecidedVia),
    ("uploads", "purpose", E.UploadPurpose),
    ("uploads", "status", E.UploadStatus),
    ("sources", "origin", E.SourceOrigin),
    ("sources", "status", E.SourceStatus),
    ("source_rights", "rights_type", E.RightsType),
    ("source_rights", "allowlist_status", E.AllowlistStatus),
    ("clip_candidates", "status", E.CandidateStatus),
    ("clip_candidates", "created_by_kind", E.CandidateCreatedByKind),
    ("tracking_links", "level", E.TrackingLevel),
    ("tracking_links", "platform", E.Platform),
    ("upload_packages", "platform", E.Platform),
    ("upload_packages", "status", E.PackageStatus),
    ("publications", "platform_kind", E.PlatformKind),
    ("publications", "status", E.PublicationStatus),
    ("publications", "verify_status", E.VerifyStatus),
    ("clicks", "route", E.ClickRoute),
    ("conversions", "event_type", E.ConversionEventType),
    ("metrics_snapshots", "source", E.MetricsSource),
    ("retention_curves", "failure_class", E.RetentionFailureClass),
    ("competitor_channels", "discovery_mode", E.DiscoveryMode),
    ("competitor_videos", "outlier_tier", E.OutlierTier),
    ("competitor_videos", "status", E.CompetitorVideoStatus),
    ("competitor_baselines", "age_bucket", E.AgeBucket),
    ("trend_briefs", "status", E.BriefStatus),
    # 0011: креативы баннера (решение владельца 2026-09-26)
    ("banner_creatives", "status", CreativeStatus),
    ("banner_creatives", "error_code", CreativeRejectCode),
    ("banner_creatives", "kind", B.CreativeKind),
    ("banner_creatives", "source_format", B.CreativeSourceFormat),
    # 0012–0015: нарезка и M3 (контракт M5M3-0)
    ("clip_candidates", "origin", CandidateOrigin),
    ("jobs", "originality_verdict", OriginalityVerdict),
    ("upload_packages", "texts_mode", PackageTextsMode),
    ("ingest_requests", "outcome", IngestOutcome),
    # 0016–0018: резервы бюджета, коллекторы метрик, ролл-апы (контракт M4-0)
    ("budget_reservations", "status", BudgetReservationStatus),
    ("budget_reservations", "release_reason", BudgetReleaseReason),
    ("collector_state", "collector", E.MetricsSource),
    ("collector_state", "state", CollectorState),
    ("collector_state", "reason", CollectorReason),
    ("publications", "metrics_collector", E.MetricsSource),
    ("channel_snapshots", "source", E.MetricsSource),
    ("api_quota_usage", "api", QuotaApi),
    # 0019: автозалив через API площадки (решение владельца 2026-09-27)
    ("upload_packages", "autopublish_state", E.AutopublishState),
    ("upload_packages", "autopublish_privacy", E.VideoPrivacy),
]


@pytest.mark.parametrize(
    ("table", "column", "enum_cls"), EXPECTED, ids=lambda x: getattr(x, "__name__", str(x))
)
def test_enum_matches_sql_check(table: str, column: str, enum_cls: type[StrEnum]) -> None:
    checks = _checks()
    assert (table, column) in checks, f"no CHECK ({column} IN …) for table {table}"
    assert checks[(table, column)] == {m.value for m in enum_cls}


def test_reject_reasons_extend_legacy_list() -> None:
    from autoshorts.models import REJECT_REASONS  # legacy, до M7

    expected = (set(REJECT_REASONS) - {"tehnicheskiy_sboy"}) | {
        "avtorskiy_sloy_slabyy",
        "prava_somnitelny",
        "povtor_shablona",
    }
    assert {r.value for r in E.RejectReason} == expected


def test_stage_sequences_use_known_stages() -> None:
    assert E.GENERATE_STAGES == ("script", "voice", "visuals", "render", "qc")
    assert E.CLIP_STAGES == ("clip_fetch", "clip_analyze", "author_script", "author_voice", "render", "qc")

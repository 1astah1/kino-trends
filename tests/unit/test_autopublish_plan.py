"""План автозалива канала и тело `videos.insert`: категория, язык, слоты (без сети и БД)."""

from __future__ import annotations

from datetime import time as dtime

import pytest
from pydantic import ValidationError

from autoshorts.api.schemas.channels import PublishingPlan
from autoshorts.publishers import youtube as yt
from autoshorts.services.autopublish import DEFAULT_CATEGORY_ID, plan_of


def test_plan_defaults_keep_people_and_blogs():
    plan = plan_of({"publishing_plan": {}}, "Europe/Moscow")
    assert plan.category_id == DEFAULT_CATEGORY_ID == "22"
    assert plan.language is None
    assert plan.mode == "manual"
    assert plan.privacy == "public"
    assert plan.timezone == "Europe/Moscow"
    assert len(plan.slots) == 3


def test_plan_movie_channel():
    plan = plan_of(
        {"publishing_plan": {"mode": "auto", "slots": ["19:00", "12:30"], "category_id": "1",
                             "language": "ru", "timezone": "Europe/Moscow", "videos_per_day": 2}},
        "UTC",
    )
    assert plan.mode == "auto"
    assert plan.slots == (dtime(12, 30), dtime(19, 0))
    assert plan.category_id == "1"
    assert plan.language == "ru"


@pytest.mark.parametrize("bad", ["film", "", None, 1.5])
def test_plan_bad_category_falls_back(bad):
    assert plan_of({"publishing_plan": {"category_id": bad}}, "UTC").category_id == DEFAULT_CATEGORY_ID


def test_plan_not_a_dict():
    plan = plan_of({"publishing_plan": None}, "UTC")
    assert plan.category_id == DEFAULT_CATEGORY_ID and plan.language is None


def test_request_body_movie_meta():
    meta = yt.VideoMeta(title="Сцена", description="Описание", category_id="1", language="ru")
    body = yt.request_body(meta)
    assert body["snippet"]["categoryId"] == "1"
    assert body["snippet"]["defaultLanguage"] == "ru"
    assert body["snippet"]["defaultAudioLanguage"] == "ru"
    assert body["status"]["selfDeclaredMadeForKids"] is False


def test_request_body_without_language():
    body = yt.request_body(yt.VideoMeta(title="Сцена", description=""))
    assert body["snippet"]["categoryId"] == "22"
    assert "defaultLanguage" not in body["snippet"]


def test_schema_validates_category_and_language():
    assert PublishingPlan(category_id="1", language="ru").category_id == "1"
    assert PublishingPlan(language="pt-BR").language == "pt-BR"
    with pytest.raises(ValidationError):
        PublishingPlan(category_id="film")
    with pytest.raises(ValidationError):
        PublishingPlan(language="Русский")


# ---------------------------------------------------------------- тексты пакета без публичного редиректора


def _inputs(product):
    from autoshorts.services.packages import PackageInputs

    job = {"platform": "youtube", "kind": "clip", "video_uid": "v1"}
    return PackageInputs(job=job, product_id="bpn", product=product, niche=None, variant=None, rights=None,
                         candidate=None, previous=None)


def test_package_texts_without_redirect_host_have_no_address():
    from autoshorts.services.packages import NO_REDIRECT_CTA, DraftTexts, assemble

    draft = DraftTexts("Сцена", "Тело", "template")
    texts = assemble(_inputs({"id": "bpn", "redirect_host": None}), draft, "ab12")
    assert NO_REDIRECT_CTA in texts["description"]
    assert "ab12" not in texts["description"] and "127.0.0.1" not in texts["description"]
    assert texts["pinned_comment"] == NO_REDIRECT_CTA


def test_package_texts_with_redirect_host_keep_code_url():
    from autoshorts.services.packages import DraftTexts, assemble

    draft = DraftTexts("Сцена", "Тело", "template")
    texts = assemble(_inputs({"id": "x", "redirect_host": "go.example.com"}), draft, "ab12")
    assert "go.example.com/ab12" in texts["description"]
    assert texts["pinned_comment"].startswith("go.example.com/ab12")

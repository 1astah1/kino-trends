"""YouTube Analytics: разбор отчётов, удержание на 3-й секунде, понятные ошибки подключения."""

import httpx
import pytest

from autoshorts.services import yt_analytics as analytics


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_video_stats_parses_rows_and_drops_unknown_metric():
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(dict(req.url.params))
        if "engagedViews" in req.url.params["metrics"]:
            return httpx.Response(400, json={"error": {"message": "Unknown identifier (engagedViews)", "errors": [{"reason": "badRequest"}]}})
        return httpx.Response(200, json={
            "columnHeaders": [{"name": "video"}, {"name": "views"}, {"name": "averageViewPercentage"}],
            "rows": [["abc", 120, 64.5], ["def", 3, 12.0]],
        })

    with _client(handler) as c:
        out = analytics.video_stats(c, "t", ["abc", "def"], "2026-10-27", "2026-10-29")
    assert out == {"abc": {"views": 120, "averageViewPercentage": 64.5}, "def": {"views": 3, "averageViewPercentage": 12.0}}
    assert calls[0]["filters"] == "video==abc,def" and calls[0]["ids"] == "channel==MINE"
    assert "engagedViews" not in calls[-1]["metrics"]


def test_disabled_api_and_missing_scope_are_explained():
    def disabled(req):
        return httpx.Response(403, json={"error": {"message": "YouTube Analytics API has not been used in project 1 before or it is disabled",
                                                   "errors": [{"reason": "accessNotConfigured"}]}})

    def no_scope(req):
        return httpx.Response(403, json={"error": {"message": "Request had insufficient authentication scopes.",
                                                   "errors": [{"reason": "insufficientPermissions"}]}})

    with _client(disabled) as c, pytest.raises(analytics.AnalyticsError) as e:
        analytics.video_stats(c, "t", ["abc"], "2026-10-27", "2026-10-29")
    assert e.value.reason == "api_disabled"
    with _client(no_scope) as c, pytest.raises(analytics.AnalyticsError) as e:
        analytics.video_stats(c, "t", ["abc"], "2026-10-27", "2026-10-29")
    assert e.value.reason == "no_scope"


def test_retention_at_three_seconds_and_failure_class():
    pts = [[0.0, 1.0, 0.5], [0.1, 0.5, 0.4], [0.5, 0.3, 0.4], [1.0, 0.2, 0.4]]
    assert analytics.watch_ratio_at(pts, 0.05) == pytest.approx(0.75)
    assert analytics.watch_ratio_at(pts, 3 / 30) == pytest.approx(0.5)
    assert analytics.failure_class(pts, 0.5) == "hook_drop"
    assert analytics.failure_class(pts, 0.8) == "middle_sag"
    assert analytics.failure_class([[0.0, 1.0, None], [0.5, 0.6, None], [1.0, 0.7, None]], 0.9) == "none"
    assert analytics.watch_ratio_at([], 0.1) is None

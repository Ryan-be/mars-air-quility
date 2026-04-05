"""Tests for page routes."""


def test_insights_engine_page_at_new_route(app_client):
    client, _ = app_client
    resp = client.get("/settings/insights-engine")
    assert resp.status_code == 200


def test_insights_engine_old_route_returns_404(app_client):
    client, _ = app_client
    resp = client.get("/insights-engine")
    assert resp.status_code == 404

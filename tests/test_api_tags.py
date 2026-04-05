"""Tests for GET /api/tags endpoint."""


def test_get_tags_returns_vocabulary(app_client):
    """GET /api/tags returns list of fingerprint id+label pairs."""
    client, _ = app_client
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "tags" in data
    tags = data["tags"]
    assert isinstance(tags, list)
    assert len(tags) > 0
    # Each entry must have id and label
    for t in tags:
        assert "id" in t
        assert "label" in t
    # Spot-check known fingerprint IDs
    ids = {t["id"] for t in tags}
    assert "cooking" in ids
    assert "combustion" in ids


def test_get_tags_ids_use_underscores(app_client):
    """Tag IDs must use underscores not hyphens (canonical form)."""
    client, _ = app_client
    resp = client.get("/api/tags")
    data = resp.get_json()
    for t in data["tags"]:
        assert "-" not in t["id"], f"Tag ID {t['id']!r} must not contain hyphens"

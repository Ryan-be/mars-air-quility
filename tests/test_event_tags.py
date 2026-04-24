"""Tests for event tagging system."""
import pytest
from database.db_logger import add_inference_tag, get_inference_tags, save_inference


def test_add_and_get_tags(db):
    """Test adding and retrieving tags for an inference."""
    inference_id = save_inference(
        event_type="tvoc_spike",
        severity="warning",
        title="Test Tag Inference",
        description="Test event for tag persistence.",
        action="Review indoor air quality.",
        evidence={},
        confidence=0.5,
    )
    add_inference_tag(inference_id, "cooking")
    tags = get_inference_tags(inference_id)
    assert len(tags) > 0
    assert tags[-1]["tag"] == "cooking"


def test_add_inference_tag_rejects_unknown_tag(db):
    """add_inference_tag raises ValueError for a tag not in allowed_tags."""
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    with pytest.raises(ValueError, match="Unknown tag"):
        add_inference_tag(inf_id, "not_a_real_tag", allowed_tags=frozenset(["cooking"]))


def test_add_inference_tag_accepts_valid_tag(db):
    """add_inference_tag succeeds when tag is in allowed_tags."""
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    add_inference_tag(inf_id, "cooking", allowed_tags=frozenset(["cooking"]))
    tags = get_inference_tags(inf_id)
    assert any(t["tag"] == "cooking" for t in tags)


def test_add_inference_tag_no_allowed_tags_passes_through(db):
    """add_inference_tag with no allowed_tags skips validation (backwards compat)."""
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    # No allowed_tags — should not raise
    add_inference_tag(inf_id, "anything_goes")
    tags = get_inference_tags(inf_id)
    assert any(t["tag"] == "anything_goes" for t in tags)


def test_api_post_tag_rejects_invalid(app_client, db):
    """POST /api/inferences/<id>/tags with unknown tag returns 400."""
    client, _ = app_client
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    resp = client.post(
        f"/api/inferences/{inf_id}/tags",
        json={"tag": "totally_made_up", "confidence": 1.0},
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "invalid_tag"
    assert "valid_tags" in data


def test_api_post_tag_accepts_valid(app_client, db):
    """POST /api/inferences/<id>/tags with a known fingerprint ID returns 200."""
    client, _ = app_client
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="T", description="D", action="A", evidence={}, confidence=0.5,
    )
    resp = client.post(
        f"/api/inferences/{inf_id}/tags",
        json={"tag": "cooking", "confidence": 1.0},
    )
    assert resp.status_code == 200


def test_remove_inference_tag(db):
    """remove_inference_tag deletes rows matching (inference_id, tag)."""
    from database.db_logger import (
        add_inference_tag, get_inference_tags, remove_inference_tag, save_inference
    )
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", action="", confidence=0.8, evidence={},
    )
    add_inference_tag(inf_id, "cooking")
    add_inference_tag(inf_id, "combustion")
    assert len(get_inference_tags(inf_id)) == 2

    remove_inference_tag(inf_id, "cooking")

    remaining = get_inference_tags(inf_id)
    assert len(remaining) == 1
    assert remaining[0]["tag"] == "combustion"


def test_remove_inference_tag_idempotent(db):
    """remove_inference_tag for a non-existent tag is a no-op."""
    from database.db_logger import (
        get_inference_tags, remove_inference_tag, save_inference
    )
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", action="", confidence=0.8, evidence={},
    )
    # Should not raise
    remove_inference_tag(inf_id, "cooking")
    assert get_inference_tags(inf_id) == []


def test_remove_inference_tag_removes_all_duplicates(db):
    """If the same tag was added twice (no UNIQUE constraint), remove all."""
    from database.db_logger import (
        add_inference_tag, get_inference_tags, remove_inference_tag, save_inference
    )
    inf_id = save_inference(
        event_type="tvoc_spike", severity="warning",
        title="t", description="d", action="", confidence=0.8, evidence={},
    )
    add_inference_tag(inf_id, "cooking")
    add_inference_tag(inf_id, "cooking")
    assert len(get_inference_tags(inf_id)) == 2

    remove_inference_tag(inf_id, "cooking")
    assert get_inference_tags(inf_id) == []

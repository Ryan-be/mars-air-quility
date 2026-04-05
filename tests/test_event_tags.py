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
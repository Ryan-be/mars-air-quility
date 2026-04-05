"""Tests for event tagging system."""
import pytest
from database.db_logger import add_inference_tag, get_inference_tags


def test_add_and_get_tags():
    """Test adding and retrieving tags for an inference."""
    # Assuming inference ID 1 exists
    add_inference_tag(1, "cooking")
    tags = get_inference_tags(1)
    assert len(tags) > 0
    assert tags[-1]["tag"] == "cooking"
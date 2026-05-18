from pathlib import Path

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "PLANT_GROW_UNIT_ARCHITECTURE.md"


def test_doc_exists():
    assert DOC.exists()


def test_doc_covers_dev_topics():
    text = DOC.read_text().lower()
    for topic in [
        "websocket", "bearer token", "enrollment key",
        "contracts", "package", "abc", "sensor",
        "pid", "soak window", "buffer", "telemetry_id",
    ]:
        assert topic in text, f"missing topic: {topic}"


def test_doc_links_to_related_canonical_docs():
    """The architecture doc must cross-link to the other user-facing
    grow-unit docs so a reader can navigate the doc set without
    leaving. This replaced the older assertion that the doc linked to
    `docs/superpowers/specs/2026-05-03-plant-grow-unit-system-design.md`
    — that file is no longer tracked (it was internal planning
    scratch; see commit f8e2a24) so the link was removed during the
    doc-audit sweep. The valuable invariant is the linkage to the
    DATABASE / HARDWARE / SETUP / USAGE docs, which this test pins."""
    text = DOC.read_text()
    for target in (
        "DATABASE.md",
        "PLANT_GROW_UNIT_HARDWARE.md",
        "PLANT_GROW_UNIT_SETUP.md",
        "PLANT_GROW_UNIT_USAGE.md",
    ):
        assert target in text, (
            f"PLANT_GROW_UNIT_ARCHITECTURE.md must link to {target} "
            f"(See-also section or inline)"
        )

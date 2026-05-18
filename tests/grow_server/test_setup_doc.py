"""SETUP.md covers required topics + has working internal references."""
from pathlib import Path
import re

DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "PLANT_GROW_UNIT_SETUP.md"


def test_doc_exists():
    assert DOC.exists()


def test_doc_covers_required_topics():
    text = DOC.read_text().lower()
    for topic in [
        "prerequisites", "enrollment", "first unit",
        "/boot/mlss-grow.yaml", "install.sh", "troubleshooting",
    ]:
        assert topic in text, f"missing topic: {topic}"


def test_doc_links_to_hardware_doc():
    text = DOC.read_text()
    assert "PLANT_GROW_UNIT_HARDWARE.md" in text


def test_doc_includes_install_oneliner_example():
    text = DOC.read_text()
    assert re.search(r"curl.*-k.*api/grow/install\.sh.*sudo bash", text)


def test_doc_no_obvious_placeholders():
    text = DOC.read_text()
    for bad in ["TBD", "TODO", "XXX", "FIXME"]:
        assert bad not in text, f"placeholder {bad} found"


def test_setup_doc_mentions_admin_role_for_key_reveal():
    """The empty-state panel exposes the master enrollment key, but only to
    admin sessions (server-side guard in api_grow_dist.peek_once). The
    setup doc has to make this requirement explicit, otherwise a viewer
    follows the steps, sees no key, and is stuck.

    We assert the word 'admin' appears within the same paragraph as the
    enrollment-key reveal instruction so the requirement is co-located
    with the instruction, not buried in a different section.
    """
    text = DOC.read_text()
    paragraphs = text.split("\n\n")
    matches = [
        p for p in paragraphs
        if "enrollment key" in p.lower() and "admin" in p.lower()
    ]
    assert matches, (
        "no paragraph mentions both 'enrollment key' and 'admin' — the "
        "admin-role requirement for revealing the key must be stated "
        "alongside the reveal instruction in PLANT_GROW_UNIT_SETUP.md"
    )


def test_setup_doc_explains_why_admin_only():
    """The 'why' matters for trust. An admin reading the doc should
    understand that revealing the key would let any holder rotate any
    unit's bearer token via the idempotent enroll endpoint. Without the
    'why', future contributors might lower the role check thinking it's
    cosmetic."""
    text = DOC.read_text().lower()
    # We're loose about exact wording — accept either explicit
    # 'rotate' / 'rotation' or the broader 'bearer token' phrase as
    # evidence the rationale is documented.
    assert "rotat" in text or "bearer token" in text, (
        "setup doc should explain WHY only admins see the enrollment key "
        "(it gates idempotent enroll → bearer token rotation)"
    )

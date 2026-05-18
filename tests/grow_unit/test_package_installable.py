"""Smoke test: the mlss_grow package can be imported and reports its version."""

def test_can_import_package():
    import mlss_grow
    assert hasattr(mlss_grow, "__version__")
    assert mlss_grow.__version__ == "0.1.0"


def test_can_import_contracts_from_grow():
    """The grow package depends on mlss_contracts as a path dep."""
    import mlss_contracts
    assert hasattr(mlss_contracts, "__version__")

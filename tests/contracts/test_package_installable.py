"""Smoke test: the mlss_contracts package can be imported."""

def test_can_import_package():
    import mlss_contracts
    assert hasattr(mlss_contracts, "__version__")

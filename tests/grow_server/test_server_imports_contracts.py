"""The MLSS server can import shared contract schemas."""

def test_server_can_import_contracts():
    import mlss_contracts
    assert hasattr(mlss_contracts, "__version__")


def test_server_has_websockets():
    import websockets
    assert websockets is not None

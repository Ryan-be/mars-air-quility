"""Config loaders for firstboot YAML and persisted token."""
from mlss_grow.config import (
    load_firstboot_config, save_token, load_token, FirstbootConfig,
)


def test_load_firstboot_config_parses_yaml(tmp_path):
    yaml_path = tmp_path / "mlss-grow.yaml"
    yaml_path.write_text("""
mlss_host: mlss.local
enrollment_key: abc-123-key
plant:
  name: Tomato 3
  type: tomato
  medium: soil
""")
    cfg = load_firstboot_config(str(yaml_path))
    assert cfg.mlss_host == "mlss.local"
    assert cfg.enrollment_key == "abc-123-key"
    assert cfg.plant_name == "Tomato 3"
    assert cfg.plant_type == "tomato"
    assert cfg.medium == "soil"


def test_load_firstboot_config_defaults_for_optional_fields(tmp_path):
    yaml_path = tmp_path / "min.yaml"
    yaml_path.write_text("""
mlss_host: mlss.local
enrollment_key: abc
plant:
  name: X
""")
    cfg = load_firstboot_config(str(yaml_path))
    assert cfg.plant_type == "generic"
    assert cfg.medium == "soil"


def test_load_firstboot_returns_none_if_file_missing(tmp_path):
    assert load_firstboot_config(str(tmp_path / "missing.yaml")) is None


def test_save_and_load_token_round_trip(tmp_path):
    token_path = str(tmp_path / "grow.token")
    save_token(token_path, unit_id=42, token="secret-token-xyz")
    loaded = load_token(token_path)
    assert loaded == (42, "secret-token-xyz")


def test_load_token_returns_none_if_file_missing(tmp_path):
    assert load_token(str(tmp_path / "missing.token")) is None


def test_save_token_sets_mode_0600(tmp_path):
    import os
    import stat
    token_path = str(tmp_path / "grow.token")
    save_token(token_path, unit_id=1, token="x")
    mode = stat.S_IMODE(os.stat(token_path).st_mode)
    # On Windows the chmod is a no-op; check on POSIX systems only
    if os.name == "posix":
        assert mode == 0o600


# ---------------------------------------------------------------------------
# server_cert_path — pinned-cert TLS for enroll + WS (C2/C3 fix)
#
# install.sh fetches the MLSS server cert at install time and writes it to
# /etc/mlss/server.crt. The firmware uses that as the trust anchor for both
# the HTTPS enrollment POST and the WSS persistent connection. Tests/dev can
# override the path via YAML.
# ---------------------------------------------------------------------------

def test_firstboot_config_default_cert_path_is_etc_mlss(tmp_path):
    """When the YAML omits server_cert_path, the default points at the
    install.sh-managed location."""
    yaml_path = tmp_path / "fb.yaml"
    yaml_path.write_text(
        "mlss_host: mlss.local\n"
        "enrollment_key: k\n"
        "plant:\n"
        "  name: P\n"
    )
    cfg = load_firstboot_config(str(yaml_path))
    assert cfg.server_cert_path == "/etc/mlss/server.crt"


def test_firstboot_config_yaml_can_override_cert_path(tmp_path):
    """An operator running on a non-standard path (test rig, custom layout)
    can override via YAML."""
    yaml_path = tmp_path / "fb.yaml"
    yaml_path.write_text(
        "mlss_host: mlss.local\n"
        "enrollment_key: k\n"
        "server_cert_path: /tmp/custom.crt\n"
        "plant:\n"
        "  name: P\n"
    )
    cfg = load_firstboot_config(str(yaml_path))
    assert cfg.server_cert_path == "/tmp/custom.crt"


def test_firstboot_config_dataclass_default_is_etc_mlss():
    """Direct construction (not via YAML) still gets the secure default."""
    cfg = FirstbootConfig(mlss_host="x", enrollment_key="k", plant_name="P")
    assert cfg.server_cert_path == "/etc/mlss/server.crt"

"""Strip a path-baked Requires-Dist from a built wheel's METADATA.

Background: `poetry build` of a package that has a path-dep
(`{path = "...", develop = true}`) bakes the ABSOLUTE path of the
build host into the wheel's METADATA as
`Requires-Dist: mlss-contracts @ file:///home/.../contracts`.

When that wheel is installed on a different host (e.g. a Pi Zero
running the firmware), pip tries to read the absolute path and
errors with "No such file or directory". Stripping the path-dep
and replacing it with a normal version constraint lets pip resolve
mlss-contracts from --find-links (where the build script also ships
the mlss-contracts wheel) instead.

Usage:
    python3 scripts/_strip_pathdep.py <path-to-mlss_grow-*.whl>

The script:
  - Reads the wheel
  - Replaces `Requires-Dist: mlss-contracts @ file:...` lines with
    `Requires-Dist: mlss-contracts<1.0.0,>=0.1.0` (PEP 440 spelling)
  - Recomputes the RECORD entry for METADATA so pip's optional
    integrity check doesn't fail
  - Writes the wheel back atomically

Idempotent: re-running on an already-patched wheel is a no-op.
"""
import base64
import hashlib
import re
import shutil
import sys
import zipfile
from pathlib import Path


def _b64sha256(data: bytes) -> str:
    """Wheel-spec base64 digest: urlsafe, unpadded."""
    return base64.urlsafe_b64encode(
        hashlib.sha256(data).digest()
    ).rstrip(b"=").decode("ascii")


def patch_wheel(wheel_path: Path) -> bool:
    """Strip path-baked Requires-Dist from the wheel. Returns True if
    anything was changed, False if the wheel was already clean."""
    files: dict[str, tuple[zipfile.ZipInfo, bytes]] = {}
    with zipfile.ZipFile(wheel_path, "r") as zin:
        for info in zin.infolist():
            files[info.filename] = (info, zin.read(info.filename))

    # Find METADATA + RECORD inside the dist-info dir
    metadata_name = next(
        (n for n in files if n.endswith(".dist-info/METADATA")), None
    )
    record_name = next(
        (n for n in files if n.endswith(".dist-info/RECORD")), None
    )
    if metadata_name is None or record_name is None:
        print(f"ERROR: {wheel_path} missing dist-info/METADATA or RECORD")
        return False

    metadata_info, metadata_bytes = files[metadata_name]
    metadata_text = metadata_bytes.decode("utf-8")

    # Replace any Requires-Dist line with a path-baked file: URL
    new_metadata_text, n_subs = re.subn(
        r"^Requires-Dist: mlss-contracts @ file:.*$",
        "Requires-Dist: mlss-contracts<1.0.0,>=0.1.0",
        metadata_text,
        flags=re.MULTILINE,
    )
    if n_subs == 0:
        # Already patched, or never had a path-dep — nothing to do
        return False

    new_metadata_bytes = new_metadata_text.encode("utf-8")
    files[metadata_name] = (metadata_info, new_metadata_bytes)

    # Update RECORD's line for METADATA so the embedded sha256+size
    # matches the new content. RECORD format is `path,sha256=<b64>,<size>`
    record_info, record_bytes = files[record_name]
    record_text = record_bytes.decode("utf-8")
    new_record_lines: list[str] = []
    for line in record_text.splitlines():
        parts = line.split(",")
        if parts and parts[0] == metadata_name:
            new_record_lines.append(
                f"{metadata_name},"
                f"sha256={_b64sha256(new_metadata_bytes)},"
                f"{len(new_metadata_bytes)}"
            )
        else:
            new_record_lines.append(line)
    new_record_bytes = ("\n".join(new_record_lines) + "\n").encode("utf-8")
    files[record_name] = (record_info, new_record_bytes)

    # Atomic rewrite
    tmp_path = wheel_path.with_suffix(wheel_path.suffix + ".new")
    with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for _, (info, data) in files.items():
            zout.writestr(info, data)
    shutil.move(str(tmp_path), str(wheel_path))
    return True


def main():
    if len(sys.argv) != 2:
        print("usage: python3 scripts/_strip_pathdep.py <wheel-path>",
              file=sys.stderr)
        sys.exit(2)
    wheel_path = Path(sys.argv[1])
    if not wheel_path.is_file():
        print(f"ERROR: {wheel_path} does not exist", file=sys.stderr)
        sys.exit(2)
    if patch_wheel(wheel_path):
        print(f"patched {wheel_path.name}: stripped path-dep from METADATA")
    else:
        print(f"{wheel_path.name}: no path-dep found (already clean)")


if __name__ == "__main__":
    main()

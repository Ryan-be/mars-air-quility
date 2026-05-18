# Release Process

How to cut a local / private release of `mlss-contracts` and `mlss-grow`
for offline install or for baking into the Pi SD-card image.

[Back to main README](../readme.md)

> **Status:** local-only. We are NOT publishing to PyPI today. This is a
> deliberate posture — wheels live in the repo build artefacts (and on
> the MLSS server's `/api/grow/dist/` endpoint), not on a public index.
> Re-evaluating that decision is a separate ticket; nothing in this
> document assumes a public package index.

---

## Packages

This repo builds two Python packages:

| Package | Path | What it is |
|---|---|---|
| `mlss-contracts` | `contracts/` | Pure-data pydantic schemas shared between server and firmware |
| `mlss-grow` | `grow_unit/` | Plant Grow Unit firmware (runs on Pi Zero / Zero 2 W) |

`mlss-grow` depends on `mlss-contracts`, so when bumping both together
build `mlss-contracts` first (the local-wheels script does this in
order automatically — you only have to think about ordering when
manually running `poetry build`).

The MLSS server itself (`mlss_monitor/`) is not packaged — it's deployed
in-place via `bin/deploy` from a clone of the repo.

---

## Versioning (semver)

Both packages use [Semantic Versioning](https://semver.org/) — `MAJOR.MINOR.PATCH`:

| Bump | When |
|---|---|
| **PATCH** (`0.1.0` → `0.1.1`) | Bug fixes, no API changes |
| **MINOR** (`0.1.0` → `0.2.0`) | New features, no breaking changes |
| **MAJOR** (`0.1.0` → `1.0.0`) | Breaking API change in pydantic models or installed firmware behavior |

While both packages are < 1.0.0, treat MINOR bumps as licence-to-break.
Coordinate the next MINOR jump across the two packages and the MLSS
server simultaneously, otherwise an old grow unit will hit a breaking
contract change after a server upgrade.

---

## Cutting a release

### 1. Bump versions

Update the `version =` line in `contracts/pyproject.toml` and/or
`grow_unit/pyproject.toml`:

```toml
[tool.poetry]
name = "mlss-grow"
version = "0.2.0"   # was "0.1.0"
```

If `mlss-grow` requires a new `mlss-contracts`, also bump the version
constraint in `scripts/prepare_pypi_release.py`'s `PATH_TO_PYPI` map
(currently `"^0.1.0"`). That script's regex still rewrites the path-dep
in `grow_unit/pyproject.toml` to a versioned dep — used by the local
wheel build path too, so the constraint number matters even though
nothing here is going to a public index.

Commit:

```bash
git add contracts/pyproject.toml grow_unit/pyproject.toml scripts/prepare_pypi_release.py
git commit -m "Bump mlss-grow 0.1.0 → 0.2.0, mlss-contracts 0.1.0 → 0.2.0"
```

### 2. Build local wheels

```bash
bash scripts/build_local_wheels.sh
```

This produces both wheels (with the path-dep stripped) into
`dist/wheels/`:

```
dist/wheels/mlss_contracts-0.2.0-py3-none-any.whl
dist/wheels/mlss_grow-0.2.0-py3-none-any.whl
```

Smoke-test the wheels offline-installable on a clean venv:

```bash
python3 -m venv /tmp/release-check
source /tmp/release-check/bin/activate
pip install --no-index --find-links dist/wheels mlss-grow
python -c "import mlss_grow; print(mlss_grow.__file__)"
```

Sister script `scripts/build_grow_wheel.sh` does the same build but
copies into `static/grow_dist/` so the MLSS HTTP server can serve them
to Pi Zeros via `install.sh`. The two scripts produce identical wheel
content; they only differ in output location.

### 3. Tag (optional, local reference only)

A git tag is just a bookmark for "this commit was the 0.2.0 build".
Nothing automatic happens on tag push — there is no publish workflow.

```bash
git tag mlss-grow-v0.2.0
git push origin mlss-grow-v0.2.0   # remote bookmark, nothing more
```

If you don't care about the tag, skip this step. Versioning lives in
`pyproject.toml`; the tag is documentation.

### 4. Bake into the Pi image (when cutting an image release)

The Pi SD-card image build pipeline calls `build_local_wheels.sh`
itself and bakes the resulting wheels into the image at
`/tmp/wheels/`, where the chroot stage script runs
`pip install --find-links /tmp/wheels mlss-grow mlss-contracts`. This
is fully self-contained — no public index lookup at provision time
for our two packages (transitive deps still come from PyPI / piwheels;
see [`docs/PI_IMAGE_BUILD.md`](PI_IMAGE_BUILD.md) for why).

```bash
# Linux box only
IMAGE_VERSION=0.2.0 bash scripts/build_pi_image.sh
```

---

## What `build_local_wheels.sh` does

1. **Build mlss-contracts** — `poetry build -f wheel` in `contracts/`,
   copy result to `dist/wheels/`.
2. **Build mlss-grow** — `poetry build -f wheel` in `grow_unit/`, copy
   result to `dist/wheels/`.
3. **Strip the path-dep** — `scripts/_strip_pathdep.py` patches the
   mlss-grow wheel's METADATA, replacing the `Requires-Dist:
   mlss-contracts @ file:///abs/path/to/contracts` line that poetry
   bakes in with a normal `Requires-Dist: mlss-contracts<1.0.0,>=0.1.0`.
   Without this step, `pip install` of the wheel on a different host
   errors with "No such file or directory" trying to read the build
   host's path.

The script is intentionally network-free at build time — every step
operates on local files only.

---

## Distribution surfaces

Today there are two ways an mlss-grow wheel reaches a Pi:

| Surface | Build step | Install path |
|---|---|---|
| MLSS server's `/api/grow/dist/` endpoint | `bash scripts/build_grow_wheel.sh` (writes to `static/grow_dist/`) | `grow_unit/install.sh` — fetches with TOFU cert pinning + SHA256 verification |
| Pi SD-card image | `bash scripts/build_pi_image.sh` (calls `build_local_wheels.sh`, bakes into image) | First-boot of a flashed image — no install step at runtime, the venv is pre-populated |

Both surfaces work without a public package index. Operators who want
ad-hoc offline install on a non-Pi machine can use `dist/wheels/`
directly.

---

## Path-dep rewriter

[`scripts/prepare_pypi_release.py`](../scripts/prepare_pypi_release.py)
(legacy name from when we were targeting PyPI) is the script that
rewrites the path-dep in `grow_unit/pyproject.toml` to a versioned
dep before `poetry build`. The `_strip_pathdep.py` post-build wheel
patcher is a defence-in-depth: it strips any path-baked URL that
slipped through, regardless of whether `prepare_pypi_release.py` was
run first.

For the local-wheels flow we rely on the wheel patcher only —
`build_local_wheels.sh` doesn't call the `prepare_pypi_release.py`
prepare/restore dance because (a) the wheel patcher catches the same
problem at the same blast radius and (b) it's simpler not to have a
mid-build mutation of the source pyproject.toml. The
`prepare_pypi_release.py` script is kept around because (a) it has
test coverage worth preserving and (b) if a future ticket revisits
publishing publicly, the rewriter is the right tool for that flow.

---

## Future: re-evaluating PyPI publication

If we change our minds and decide to publish publicly, the work is:

1. Add a GitHub Actions workflow per package (build + `twine upload`).
2. Add a `PYPI_API_TOKEN` repo secret.
3. Decide whether to keep the `prepare_pypi_release.py` rewrite step
   or rely on the wheel patcher only — both work for PyPI uploads.
4. Update this doc to describe the dual flow.

That's a separate ticket. Don't add publish infra speculatively.

---

## Related

- [`scripts/build_local_wheels.sh`](../scripts/build_local_wheels.sh) — build wheels for offline / Pi-image use
- [`scripts/build_grow_wheel.sh`](../scripts/build_grow_wheel.sh) — same wheels, copied to MLSS HTTP server's dist endpoint
- [`scripts/_strip_pathdep.py`](../scripts/_strip_pathdep.py) — wheel-level path-dep stripper (post-build)
- [`scripts/prepare_pypi_release.py`](../scripts/prepare_pypi_release.py) — pyproject-level path-dep rewriter (pre-build, kept for future use)
- [`grow_unit/install.sh`](../grow_unit/install.sh) — Pi-side installer for the MLSS-served wheel path
- [`docs/PI_IMAGE_BUILD.md`](PI_IMAGE_BUILD.md) — Pi SD-card image build (consumes `build_local_wheels.sh` output)
- [`docs/PLANT_GROW_UNIT_ARCHITECTURE.md`](PLANT_GROW_UNIT_ARCHITECTURE.md) — system architecture for the packages this releases
- [`grow_unit/README.md`](../grow_unit/README.md) and [`contracts/README.md`](../contracts/README.md) — module maps for the two packages

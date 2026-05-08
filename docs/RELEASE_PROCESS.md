# Release Process

How to cut a public PyPI release of `mlss-contracts` and `mlss-grow`.

[Back to main README](../readme.md)

---

## Packages

This repo publishes two PyPI packages:

| Package | Path | What it is |
|---|---|---|
| `mlss-contracts` | `contracts/` | Pure-data pydantic schemas shared between server and firmware |
| `mlss-grow` | `grow_unit/` | Plant Grow Unit firmware (runs on Pi Zero / Zero 2 W) |

`mlss-grow` depends on `mlss-contracts`, so when bumping both together
release `mlss-contracts` first.

The MLSS server itself (`mlss_monitor/`) is not published — it's deployed
in-place via `bin/deploy` from a clone of the repo. That posture works
because the server runs on a Pi we control end-to-end; the grow units
are anyone's hardware so their firmware needs to be `pip install`-able
from the public index.

---

## One-time maintainer setup

Before the first release, the maintainer has to:

1. **Create the project on PyPI.** PyPI rejects API tokens scoped to a
   project that doesn't exist yet. The bootstrapping path is:
   - On a maintainer laptop, run the manual-fallback recipe (below) to
     do the very first upload of each package. PyPI auto-creates the
     project on that first push.
   - For the manual upload, use a temporary user-scoped token from
     <https://pypi.org/manage/account/token/>.

2. **Mint a project-scoped API token** for each package:
   - Visit <https://pypi.org/manage/account/token/>
   - Click "Add API token", scope to "Project: mlss-grow" (and again for
     "Project: mlss-contracts"). Two separate tokens — least-privilege.
   - Copy the token (it's only shown once).

3. **Add the tokens as repository secrets:**
   - Go to <https://github.com/Ryan-be/mars-air-quility/settings/secrets/actions>
   - Add `PYPI_API_TOKEN` (used by both publish workflows). If you'd
     prefer separate tokens per package, the workflow files reference
     a single secret name today — pick one and copy the higher-privilege
     token there. (A future refinement: split into
     `PYPI_API_TOKEN_GROW` and `PYPI_API_TOKEN_CONTRACTS`.)

After that, every subsequent release is just a tag push.

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
(currently `"^0.1.0"`).

Commit:

```bash
git add contracts/pyproject.toml grow_unit/pyproject.toml scripts/prepare_pypi_release.py
git commit -m "Bump mlss-grow 0.1.0 → 0.2.0, mlss-contracts 0.1.0 → 0.2.0"
```

### 2. Tag

The publish workflows trigger on tags matching `mlss-{package}-v*`. Order
matters when both move together — `mlss-contracts` must land first so
`mlss-grow`'s build can resolve it.

```bash
# Contracts first
git tag mlss-contracts-v0.2.0
git push origin mlss-contracts-v0.2.0
# Wait ~2 minutes for the workflow to finish (watch in the Actions tab)

# Then grow
git tag mlss-grow-v0.2.0
git push origin mlss-grow-v0.2.0
```

### 3. Verify

After both workflows go green:

```bash
# Confirm both packages are on PyPI
pip install mlss-grow==0.2.0 --dry-run

# Sanity check on a fresh Pi or VM
python3 -m venv /tmp/release-check
source /tmp/release-check/bin/activate
pip install mlss-grow
mlss-grow --help
```

The GitHub Release page (<https://github.com/Ryan-be/mars-air-quility/releases>)
should also have a new entry with the wheel attached as a release asset.

---

## What the workflow does

`.github/workflows/publish-mlss-grow.yml`:

1. **Checkout** the tag.
2. **Install poetry + twine.**
3. **Prepare** — runs `scripts/prepare_pypi_release.py grow_unit/pyproject.toml prepare`,
   which rewrites the `mlss-contracts` path-dep into a versioned dep
   (PyPI rejects path-deps in published wheels). The original is
   stashed at `grow_unit/pyproject.toml.bak`.
4. **Build** — `poetry build` produces a wheel + sdist in `grow_unit/dist/`.
5. **twine check** — sanity-validates wheel metadata.
6. **Publish** — `twine upload grow_unit/dist/*` with the `PYPI_API_TOKEN` secret.
7. **Restore** — runs `prepare_pypi_release.py ... restore` (always,
   even on failure) so the in-tree pyproject is untouched.
8. **GitHub Release asset upload** — mirrors the wheel onto the tag's
   GitHub Release for `install.sh` to fall back on if PyPI is down.

`.github/workflows/publish-mlss-contracts.yml` is the same but skips
the prepare/restore step (contracts has no path-deps of its own).

---

## Manual fallback (if the workflow fails)

If a workflow run is stuck or PyPI is timing out, a maintainer can
reproduce the workflow locally:

```bash
# === mlss-contracts ===
cd contracts
poetry build
twine check dist/*
twine upload dist/* -u __token__ -p $PYPI_API_TOKEN
cd ..

# === mlss-grow ===
python scripts/prepare_pypi_release.py grow_unit/pyproject.toml prepare
cd grow_unit
poetry build
twine check dist/*
twine upload dist/* -u __token__ -p $PYPI_API_TOKEN
cd ..
python scripts/prepare_pypi_release.py grow_unit/pyproject.toml restore

# Re-tag if the previous tag run got partial. Otherwise skip the tag step
# since it already exists.
git tag mlss-grow-v0.2.0
git push origin mlss-grow-v0.2.0
```

You'll need a user-scoped token in `$PYPI_API_TOKEN` for this — the
project-scoped CI token works too if your local pypi.org account is
the same one that owns the project.

---

## Rolling back a bad release

PyPI doesn't allow re-uploading a version (immutability is a feature, not
a bug — pip would otherwise lock-pin to a moved target). The recovery
options are:

1. **Yank the version** — <https://pypi.org/manage/project/mlss-grow/release/X.Y.Z/>
   has a "yank" button. Yanked versions remain installable when
   pinned exactly but disappear from `pip install mlss-grow` (the
   bare command resolves to the next-most-recent non-yanked release).
   This is the right call for a broken release: it doesn't break
   downstreams that pinned, but pulls the broken version out of the
   default upgrade path.

2. **Cut a fix release** with an incremented PATCH version. Always
   prefer this over yanking — yanks are a stopgap.

3. **Delete the version** (admin only) — destroys the upload. Use only
   for security incidents (leaked credential in the wheel, etc.).
   Yanking is preferred even for buggy releases.

---

## Related

- [`scripts/prepare_pypi_release.py`](../scripts/prepare_pypi_release.py) — the path-dep rewriter
- [`grow_unit/install.sh`](../grow_unit/install.sh) — Pi-side installer (post-PyPI: can use `pip install mlss-grow` instead of fetching from MLSS)
- [`docs/PI_IMAGE_BUILD.md`](PI_IMAGE_BUILD.md) — Pi SD-card image build (depends on PyPI being live, item #3)

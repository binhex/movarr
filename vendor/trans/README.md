# vendored `trans` 2.1.0

Verbatim copy of the single-module [`trans`](https://github.com/zzzsochi/trans)
transliteration library, vendored so that dependency resolution no longer depends on a
bzip2 source distribution.

## Why this exists

`trans` is a transitive dependency (`movarr` -> `imdbpie` -> `trans`). Version 2.1.0 is
only published on PyPI as `trans-2.1.0.tar.bz2`, and uv 0.12.0 dropped support for
legacy source distribution formats such as `.tar.bz2` and `.tar.xz`
([astral-sh/uv#18927](https://github.com/astral-sh/uv/pull/18927)). Installing from that
artifact fails with:

```text
Archive contains a file with an unsupported compression method; files must be compressed with 'stored', 'DEFLATE', or 'zstd'
```

A fresh resolution with uv >= 0.12 is worse than a failure: it silently downgrades
`imdbpie` to 5.6.2 (2018), which is not importable on Python 3.12. Vendoring the module
keeps `imdbpie` at its current version and makes the lock file installable again.

The root `pyproject.toml` points uv at this directory:

```toml
[tool.uv]
override-dependencies = ["trans>=2.1.0"]

[tool.uv.sources]
trans = { path = "vendor/trans" }
```

The `override-dependencies` entry replaces the constraint declared by `imdbpie`, and the
source entry resolves it from this directory instead of PyPI.

## Provenance

| Item | Value |
| --- | --- |
| PyPI sdist | `https://files.pythonhosted.org/packages/3e/c1/368daee29d1c9b081c8c1eeda2335879c484021d795b12bd116bd134d98f/trans-2.1.0.tar.bz2` |
| sdist sha256 | `417d8f7862a4a01470bdf0ab2a96b60983e459d202c1b12e8df3fa563146b71f` |
| `trans.py` sha256 | `a8a08c1a9622d961c62b48d1a023e894984639d602afef960b8e12bcd85ca8cb` |
| LICENSE sha256 | `0ac305983c934194afdd267a6e38fc30f79ffbdec9c5ce50f5d32b2330a5f76b` |
| Upstream | `https://github.com/zzzsochi/trans` (branch `master`) |
| License | BSD 2-Clause (see `LICENSE`) |

`trans.py` is byte-identical to the file inside the published sdist and to the file in
the wheel that `pip wheel trans==2.1.0` builds from it. Nothing in this directory has
been modified; `pyproject.toml` and `README.md` are the only files added here.

## Maintenance

`trans` has had no release since 2016 and no further version is expected. If it ever is
updated upstream, replace `trans.py` (and `LICENSE`), update `version` and the hashes
above, then run `uv lock`.

# CLI Environment Variable Overrides (Unraid) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CLI options for the settings Unraid users need most, so a container can be configured entirely via environment variables without ever editing movarr.yml; also improve the auto-generated config with a sensible 1080p-only default search.

**Architecture:** All new options follow the existing override pattern — `None` default means "not supplied, use YAML value". After `load_config()` the CLI applies non-None values onto the loaded config object before passing it to the scheduler. A single `_apply_cli_overrides()` helper keeps `cli()` readable. The default-config improvement is a one-line change to `IndexSiteConfig`.

**Tech Stack:** Python 3.12, Click 8, Pydantic v2, pytest, movarr's existing `Config` model hierarchy.

---

## File Map

| File | Change |
|------|--------|
| `src/movarr/cli.py` | Add 12 new `@click.option` decorators + `_apply_cli_overrides()` helper |
| `src/movarr/config.py` | Change `IndexSiteConfig.search` default to 1080p-only |
| `tests/unit/test_cli.py` | New test class `TestCliOverrides` covering every new option |

No new files. No migrations needed (no YAML schema change — all new options purely override existing fields at runtime).

---

### Task 1: Change auto-generated config default search to 1080p only

The current default generates both `1080p` and `2160p` search tiers. New installs should start with just `1080p` (category `2000,5000`) so the out-of-the-box experience is conservative.

**Files:**
- Modify: `src/movarr/config.py` — `IndexSiteConfig.search` field default

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_config.py` inside an existing or new class:

```python
class TestDefaultSearchConfig:
    """Auto-generated config has a single 1080p search tier."""

    def test_default_search_has_one_tier(self) -> None:
        cfg = Config()
        assert len(cfg.index_site.search) == 1

    def test_default_search_criteria_is_1080p(self) -> None:
        cfg = Config()
        assert cfg.index_site.search[0].criteria == "1080p"

    def test_default_search_category_is_2000_5000(self) -> None:
        cfg = Config()
        assert cfg.index_site.search[0].category == "2000,5000"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_config.py::TestDefaultSearchConfig -v
```

Expected: `FAILED` — `AssertionError: assert 2 == 1` (current default has two tiers).

- [ ] **Step 3: Change the default**

In `src/movarr/config.py`, find `IndexSiteConfig` and replace the `search` field default:

```python
class IndexSiteConfig(BaseModel):
    """Per-indexer search configuration."""

    jackett_indexer: str = "all"
    prowlarr_indexer: str = "all"
    ignore_list: list[str] = Field(default_factory=list)
    search: list[SearchCriteriaConfig] = Field(
        default_factory=lambda: [
            SearchCriteriaConfig(criteria="1080p", category="2000,5000", minimum_size_mb=3000, maximum_size_mb=20000, minimum_bitrate_mb=50),
        ]
    )
    override_search: dict[str, dict[str, str]] = Field(default_factory=dict)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_config.py::TestDefaultSearchConfig -v
```

Expected: all 3 `PASSED`.

- [ ] **Step 5: Make sure full suite still passes**

```bash
uv run pytest tests/unit/ -q
```

Expected: all green (existing migration tests that check `index_site.search` may need no change since they don't assert count).

- [ ] **Step 6: Commit**

```bash
git add src/movarr/config.py tests/unit/test_config.py
git commit -m "feat(config): default search tier is 1080p only (category 2000,5000)"
```

---

### Task 2: Add `_apply_cli_overrides()` helper and `--db-path` option

Introduce the override helper function and wire up the first new option as a pattern check.

**Files:**
- Modify: `src/movarr/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_cli.py`:

```python
class TestCliOverrides:
    """CLI options override the corresponding config values when supplied."""

    def _invoke(
        self,
        mocker: "MockerFixture",
        args: list[str],
        log_level_console: str = "info",
        log_path: str = "",
        pid_path: str = "",
        daemon_mode: str = "foreground",
    ) -> "MagicMock":
        """Invoke CLI, return the config mock passed to scheduler.run()."""
        from unittest.mock import MagicMock
        mocker.patch("movarr.cli.create_logger")
        cfg = MagicMock()
        cfg.general.log_level_console = log_level_console
        cfg.general.log_path = log_path
        cfg.general.pid_path = pid_path
        cfg.general.daemon_mode = daemon_mode
        cfg.general.library_path_list = []
        cfg.general.db_path = "db/movarr.db"
        cfg.torrent_client.qbittorrent.host = "localhost"
        cfg.torrent_client.qbittorrent.port = 8080
        cfg.torrent_client.qbittorrent.username = "admin"
        cfg.torrent_client.qbittorrent.password = "adminadmin"
        cfg.index_proxy.selected = "jackett"
        cfg.index_proxy.jackett.host = "localhost"
        cfg.index_proxy.jackett.port = 9117
        cfg.index_proxy.jackett.api_key = ""
        cfg.index_proxy.prowlarr.host = "localhost"
        cfg.index_proxy.prowlarr.port = 9696
        cfg.index_proxy.prowlarr.api_key = ""
        mocker.patch("movarr.config.load_config", return_value=cfg)
        mock_run = mocker.patch("movarr.scheduler.run")
        from click.testing import CliRunner
        from movarr.cli import cli
        CliRunner().invoke(cli, args)
        if mock_run.called:
            return mock_run.call_args[0][0]
        return cfg

    def test_db_path_overrides_config(self, mocker: "MockerFixture") -> None:
        cfg = self._invoke(mocker, ["--db-path", "/data/movarr.db", "--test"])
        assert cfg.general.db_path == "/data/movarr.db"

    def test_db_path_absent_leaves_config_unchanged(self, mocker: "MockerFixture") -> None:
        cfg = self._invoke(mocker, ["--test"])
        assert cfg.general.db_path == "db/movarr.db"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides::test_db_path_overrides_config -v
```

Expected: `FAILED` — `--db-path` is not a recognised option.

- [ ] **Step 3: Add `--db-path` option and `_apply_cli_overrides()` to `cli.py`**

```python
# src/movarr/cli.py  — add the helper before the @click.command() decorator

def _apply_cli_overrides(config: "Config", **overrides: object) -> None:
    """Apply non-None CLI override values onto *config* in-place.

    Any kwarg whose value is ``None`` is skipped (user did not supply it).
    """
    if overrides.get("db_path") is not None:
        config.general.db_path = str(overrides["db_path"])
```

Add the new option to the decorator chain (after `--log-path`):

```python
@click.option(
    "--db-path",
    type=click.Path(file_okay=True, dir_okay=False, resolve_path=True),
    default=None,
    show_default=False,
    metavar="<path>",
    help="Override the database file path from config.",
)
```

Add `db_path: str | None` to the `cli()` function signature and call the helper just before the logger setup:

```python
def cli(
    config_path: str,
    log_path: str | None,
    log_level: str | None,
    db_path: str | None,
    daemon: bool,
    test: bool,
) -> None:
    ...
    from movarr.config import load_config  # noqa: PLC0415
    config = load_config(config_path)

    if daemon:
        config.general.daemon_mode = "background"

    _apply_cli_overrides(config, db_path=db_path)
    ...
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides -v
```

Expected: both `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add src/movarr/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add --db-path CLI override"
```

---

### Task 3: Add `--library-path-list` option

Accepts a comma-separated list (e.g. `"/media/movies,/media/4k"`) and splits it into `config.general.library_path_list`.

**Files:**
- Modify: `src/movarr/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests**

Add to `TestCliOverrides` in `tests/unit/test_cli.py`:

```python
def test_library_path_list_single_path(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--library-path-list", "/media/movies", "--test"])
    assert cfg.general.library_path_list == ["/media/movies"]

def test_library_path_list_multiple_paths(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--library-path-list", "/media/movies,/media/4k", "--test"])
    assert cfg.general.library_path_list == ["/media/movies", "/media/4k"]

def test_library_path_list_strips_whitespace(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--library-path-list", "/media/movies, /media/4k", "--test"])
    assert cfg.general.library_path_list == ["/media/movies", "/media/4k"]

def test_library_path_list_absent_leaves_config_unchanged(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--test"])
    assert cfg.general.library_path_list == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides::test_library_path_list_single_path -v
```

Expected: `FAILED` — unrecognised option.

- [ ] **Step 3: Add option and wire it up**

Add decorator:

```python
@click.option(
    "--library-path-list",
    default=None,
    show_default=False,
    metavar="<path[,path...]>",
    help="Comma-separated library paths, overrides config (e.g. /media/movies,/media/4k).",
)
```

Extend `_apply_cli_overrides()`:

```python
def _apply_cli_overrides(config: "Config", **overrides: object) -> None:
    if overrides.get("db_path") is not None:
        config.general.db_path = str(overrides["db_path"])
    if overrides.get("library_path_list") is not None:
        config.general.library_path_list = [
            p.strip() for p in str(overrides["library_path_list"]).split(",") if p.strip()
        ]
```

Add `library_path_list: str | None` to `cli()` signature; add it to the `_apply_cli_overrides` call:

```python
_apply_cli_overrides(config, db_path=db_path, library_path_list=library_path_list)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides -v
```

Expected: all `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add src/movarr/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add --library-path-list CLI override (comma-separated)"
```

---

### Task 4: Add qBittorrent connection options

Four options: `--qbt-host`, `--qbt-port`, `--qbt-username`, `--qbt-password`.

**Files:**
- Modify: `src/movarr/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests**

Add to `TestCliOverrides`:

```python
def test_qbt_host_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--qbt-host", "192.168.1.50", "--test"])
    assert cfg.torrent_client.qbittorrent.host == "192.168.1.50"

def test_qbt_port_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--qbt-port", "8090", "--test"])
    assert cfg.torrent_client.qbittorrent.port == 8090

def test_qbt_username_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--qbt-username", "myuser", "--test"])
    assert cfg.torrent_client.qbittorrent.username == "myuser"

def test_qbt_password_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--qbt-password", "s3cr3t", "--test"])
    assert cfg.torrent_client.qbittorrent.password == "s3cr3t"

def test_qbt_options_absent_leave_config_unchanged(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--test"])
    assert cfg.torrent_client.qbittorrent.host == "localhost"
    assert cfg.torrent_client.qbittorrent.port == 8080
    assert cfg.torrent_client.qbittorrent.username == "admin"
    assert cfg.torrent_client.qbittorrent.password == "adminadmin"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides::test_qbt_host_overrides_config -v
```

Expected: `FAILED` — unrecognised option.

- [ ] **Step 3: Add options and wire them up**

Add four decorators:

```python
@click.option(
    "--qbt-host",
    default=None,
    show_default=False,
    metavar="<host>",
    help="Override qBittorrent host from config.",
)
@click.option(
    "--qbt-port",
    type=int,
    default=None,
    show_default=False,
    metavar="<port>",
    help="Override qBittorrent WebUI port from config.",
)
@click.option(
    "--qbt-username",
    default=None,
    show_default=False,
    metavar="<user>",
    help="Override qBittorrent username from config.",
)
@click.option(
    "--qbt-password",
    default=None,
    show_default=False,
    metavar="<pass>",
    help="Override qBittorrent password from config.",
)
```

Extend `_apply_cli_overrides()`:

```python
    if overrides.get("qbt_host") is not None:
        config.torrent_client.qbittorrent.host = str(overrides["qbt_host"])
    if overrides.get("qbt_port") is not None:
        config.torrent_client.qbittorrent.port = int(overrides["qbt_port"])  # type: ignore[arg-type]
    if overrides.get("qbt_username") is not None:
        config.torrent_client.qbittorrent.username = str(overrides["qbt_username"])
    if overrides.get("qbt_password") is not None:
        config.torrent_client.qbittorrent.password = str(overrides["qbt_password"])
```

Add four parameters to `cli()` signature: `qbt_host: str | None`, `qbt_port: int | None`, `qbt_username: str | None`, `qbt_password: str | None`.

Update `_apply_cli_overrides` call:

```python
_apply_cli_overrides(
    config,
    db_path=db_path,
    library_path_list=library_path_list,
    qbt_host=qbt_host,
    qbt_port=qbt_port,
    qbt_username=qbt_username,
    qbt_password=qbt_password,
)
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides -v
```

Expected: all `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add src/movarr/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add --qbt-host/port/username/password CLI overrides"
```

---

### Task 5: Add index proxy selector and Jackett connection options

Options: `--index-proxy` (choice), `--jackett-host`, `--jackett-port`, `--jackett-api-key`.

**Files:**
- Modify: `src/movarr/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests**

Add to `TestCliOverrides`:

```python
def test_index_proxy_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--index-proxy", "prowlarr", "--test"])
    assert cfg.index_proxy.selected == "prowlarr"

def test_index_proxy_invalid_choice_exits_nonzero(self) -> None:
    from click.testing import CliRunner
    from movarr.cli import cli
    result = CliRunner().invoke(cli, ["--index-proxy", "sonarr"])
    assert result.exit_code != 0

def test_jackett_host_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--jackett-host", "192.168.1.60", "--test"])
    assert cfg.index_proxy.jackett.host == "192.168.1.60"

def test_jackett_port_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--jackett-port", "9118", "--test"])
    assert cfg.index_proxy.jackett.port == 9118

def test_jackett_api_key_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--jackett-api-key", "abc123", "--test"])
    assert cfg.index_proxy.jackett.api_key == "abc123"

def test_jackett_options_absent_leave_config_unchanged(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--test"])
    assert cfg.index_proxy.jackett.host == "localhost"
    assert cfg.index_proxy.jackett.port == 9117
    assert cfg.index_proxy.jackett.api_key == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides::test_index_proxy_overrides_config -v
```

Expected: `FAILED` — unrecognised option.

- [ ] **Step 3: Add options and wire them up**

Add four decorators:

```python
@click.option(
    "--index-proxy",
    type=click.Choice(["jackett", "prowlarr"], case_sensitive=False),
    default=None,
    show_default=False,
    metavar="<proxy>",
    help="Override index proxy selection from config (jackett or prowlarr).",
)
@click.option(
    "--jackett-host",
    default=None,
    show_default=False,
    metavar="<host>",
    help="Override Jackett host from config.",
)
@click.option(
    "--jackett-port",
    type=int,
    default=None,
    show_default=False,
    metavar="<port>",
    help="Override Jackett port from config.",
)
@click.option(
    "--jackett-api-key",
    default=None,
    show_default=False,
    metavar="<key>",
    help="Override Jackett API key from config.",
)
```

Extend `_apply_cli_overrides()`:

```python
    if overrides.get("index_proxy") is not None:
        config.index_proxy.selected = str(overrides["index_proxy"]).lower()
    if overrides.get("jackett_host") is not None:
        config.index_proxy.jackett.host = str(overrides["jackett_host"])
    if overrides.get("jackett_port") is not None:
        config.index_proxy.jackett.port = int(overrides["jackett_port"])  # type: ignore[arg-type]
    if overrides.get("jackett_api_key") is not None:
        config.index_proxy.jackett.api_key = str(overrides["jackett_api_key"])
```

Add four parameters to `cli()` signature: `index_proxy: str | None`, `jackett_host: str | None`, `jackett_port: int | None`, `jackett_api_key: str | None`. Include them in the `_apply_cli_overrides` call.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides -v
```

Expected: all `PASSED`.

- [ ] **Step 5: Commit**

```bash
git add src/movarr/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add --index-proxy and --jackett-host/port/api-key CLI overrides"
```

---

### Task 6: Add Prowlarr connection options

Options: `--prowlarr-host`, `--prowlarr-port`, `--prowlarr-api-key`.

**Files:**
- Modify: `src/movarr/cli.py`
- Modify: `tests/unit/test_cli.py`

- [ ] **Step 1: Write failing tests**

Add to `TestCliOverrides`:

```python
def test_prowlarr_host_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--prowlarr-host", "192.168.1.70", "--test"])
    assert cfg.index_proxy.prowlarr.host == "192.168.1.70"

def test_prowlarr_port_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--prowlarr-port", "9697", "--test"])
    assert cfg.index_proxy.prowlarr.port == 9697

def test_prowlarr_api_key_overrides_config(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--prowlarr-api-key", "xyz789", "--test"])
    assert cfg.index_proxy.prowlarr.api_key == "xyz789"

def test_prowlarr_options_absent_leave_config_unchanged(self, mocker: "MockerFixture") -> None:
    cfg = self._invoke(mocker, ["--test"])
    assert cfg.index_proxy.prowlarr.host == "localhost"
    assert cfg.index_proxy.prowlarr.port == 9696
    assert cfg.index_proxy.prowlarr.api_key == ""
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides::test_prowlarr_host_overrides_config -v
```

Expected: `FAILED` — unrecognised option.

- [ ] **Step 3: Add options and wire them up**

Add three decorators:

```python
@click.option(
    "--prowlarr-host",
    default=None,
    show_default=False,
    metavar="<host>",
    help="Override Prowlarr host from config.",
)
@click.option(
    "--prowlarr-port",
    type=int,
    default=None,
    show_default=False,
    metavar="<port>",
    help="Override Prowlarr port from config.",
)
@click.option(
    "--prowlarr-api-key",
    default=None,
    show_default=False,
    metavar="<key>",
    help="Override Prowlarr API key from config.",
)
```

Extend `_apply_cli_overrides()`:

```python
    if overrides.get("prowlarr_host") is not None:
        config.index_proxy.prowlarr.host = str(overrides["prowlarr_host"])
    if overrides.get("prowlarr_port") is not None:
        config.index_proxy.prowlarr.port = int(overrides["prowlarr_port"])  # type: ignore[arg-type]
    if overrides.get("prowlarr_api_key") is not None:
        config.index_proxy.prowlarr.api_key = str(overrides["prowlarr_api_key"])
```

Add three parameters to `cli()` signature and include them in `_apply_cli_overrides` call.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides -v
```

Expected: all `PASSED`.

- [ ] **Step 5: Full suite + pre-commit**

```bash
uv run pytest tests/unit/ -q
pre-commit run --all-files
```

Expected: all green, all hooks pass.

- [ ] **Step 6: Commit**

```bash
git add src/movarr/cli.py tests/unit/test_cli.py
git commit -m "feat(cli): add --prowlarr-host/port/api-key CLI overrides"
```

---

### Task 7: Final integration test + push

Verify the complete feature end-to-end and check the help output.

**Files:**
- Modify: `tests/unit/test_cli.py` — one integration-style smoke test

- [ ] **Step 1: Write the integration test**

Add to `TestCliOverrides` in `tests/unit/test_cli.py`:

```python
def test_all_overrides_applied_together(self, mocker: "MockerFixture") -> None:
    """All CLI overrides can be supplied simultaneously."""
    cfg = self._invoke(
        mocker,
        [
            "--db-path", "/data/movarr.db",
            "--library-path-list", "/media/movies,/media/4k",
            "--qbt-host", "10.0.0.5",
            "--qbt-port", "8090",
            "--qbt-username", "admin2",
            "--qbt-password", "pass2",
            "--index-proxy", "prowlarr",
            "--jackett-host", "10.0.0.6",
            "--jackett-port", "9118",
            "--jackett-api-key", "jkey",
            "--prowlarr-host", "10.0.0.7",
            "--prowlarr-port", "9697",
            "--prowlarr-api-key", "pkey",
            "--test",
        ],
    )
    assert cfg.general.db_path == "/data/movarr.db"
    assert cfg.general.library_path_list == ["/media/movies", "/media/4k"]
    assert cfg.torrent_client.qbittorrent.host == "10.0.0.5"
    assert cfg.torrent_client.qbittorrent.port == 8090
    assert cfg.torrent_client.qbittorrent.username == "admin2"
    assert cfg.torrent_client.qbittorrent.password == "pass2"
    assert cfg.index_proxy.selected == "prowlarr"
    assert cfg.index_proxy.jackett.host == "10.0.0.6"
    assert cfg.index_proxy.jackett.port == 9118
    assert cfg.index_proxy.jackett.api_key == "jkey"
    assert cfg.index_proxy.prowlarr.host == "10.0.0.7"
    assert cfg.index_proxy.prowlarr.port == 9697
    assert cfg.index_proxy.prowlarr.api_key == "pkey"

def test_help_shows_all_new_options(self) -> None:
    from click.testing import CliRunner
    from movarr.cli import cli
    result = CliRunner().invoke(cli, ["--help"])
    for opt in (
        "--db-path", "--library-path-list",
        "--qbt-host", "--qbt-port", "--qbt-username", "--qbt-password",
        "--index-proxy",
        "--jackett-host", "--jackett-port", "--jackett-api-key",
        "--prowlarr-host", "--prowlarr-port", "--prowlarr-api-key",
    ):
        assert opt in result.output, f"{opt!r} missing from --help"
```

- [ ] **Step 2: Run test**

```bash
uv run pytest tests/unit/test_cli.py::TestCliOverrides::test_all_overrides_applied_together tests/unit/test_cli.py::TestCliOverrides::test_help_shows_all_new_options -v
```

Expected: both `PASSED`.

- [ ] **Step 3: Run full suite + pre-commit**

```bash
uv run pytest tests/unit/ -q
pre-commit run --all-files
```

Expected: all green, all hooks pass.

- [ ] **Step 4: Final commit and push**

```bash
git add -A
git commit -m "test(cli): integration test for all CLI overrides applied simultaneously"
git push
```

---

## Self-Review

**Spec coverage:**

| Requirement | Task |
|------------|------|
| `general/library_path_list` comma-separated CLI | Task 3 |
| `general/db_path` CLI | Task 2 |
| `torrent_client/qbittorrent` host, port, username, password | Task 4 |
| `index_proxy/selected` choice (jackett/prowlarr) | Task 5 |
| `index_proxy/jackett` host, port, api_key | Task 5 |
| `index_proxy/prowlarr` host, port, api_key | Task 6 |
| All options override YAML; absent = use YAML | All tasks (None default + `_apply_cli_overrides`) |
| Default auto-generated config: 1080p, category 2000,5000 | Task 1 |

No gaps found.

**Placeholder scan:** No TBD, TODO, or vague steps. All code blocks contain complete implementations.

**Type consistency:** `_apply_cli_overrides` kwargs use Python `snake_case` throughout (matching Click's automatic `--jackett-api-key` → `jackett_api_key` conversion). The `int()` cast on port values is consistent across qBittorrent, Jackett, and Prowlarr.

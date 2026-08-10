# Prowlarr Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Prowlarr as an additive alternative to Jackett, selected via `config.index_proxy.selected`, without changing any existing Jackett behaviour.

**Architecture:** A new `IndexProxyProtocol` (structural typing) defines the shared `search()` / `is_reachable()` interface. A `get_indexer_client(config)` factory in `indexer.py` returns the appropriate client. `search.py` consumes the protocol type, replacing the hard-coded `JackettClient` import.

**Tech Stack:** Python 3.11+, Pydantic v2, `requests` via `HttpClient`, `loguru`, `pytest-mock`.

---

## File Map

| File | Action |
|---|---|
| `src/movarr/indexer.py` | **Create** — `IndexProxyProtocol` + `get_indexer_client()` |
| `src/movarr/prowlarr.py` | **Create** — `ProwlarrClient` + `ProwlarrError` |
| `src/movarr/config.py` | **Modify** — add `ProwlarrConfig`, update `IndexProxyConfig` + `IndexSiteConfig`, add migration v2.4.0 → v2.5.0 |
| `src/movarr/search.py` | **Modify** — swap hard-coded `JackettClient` → factory; rename `_SearchSession.jackett` → `indexer` |
| `tests/unit/test_indexer.py` | **Create** — factory tests |
| `tests/unit/test_prowlarr.py` | **Create** — `ProwlarrClient` tests |
| `tests/unit/test_config.py` | **Modify** — add migration v2.4.0 → v2.5.0 tests |
| `tests/unit/test_search.py` | **Modify** — update `jackett=` → `indexer=`, update patches |
| `README.md` | **Modify** — document new Prowlarr config fields |

---

## Task 1: `IndexProxyProtocol` + factory (`indexer.py`)

**Files:**
- Create: `src/movarr/indexer.py`
- Create: `tests/unit/test_indexer.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_indexer.py
"""Unit tests for movarr.indexer — protocol factory."""

from __future__ import annotations

import pytest

from movarr.config import Config
from movarr.indexer import get_indexer_client
from movarr.jackett import JackettClient
from movarr.prowlarr import ProwlarrClient


class TestGetIndexerClient:
    """Tests for get_indexer_client factory."""

    def test_returns_jackett_client_when_selected_is_jackett(self) -> None:
        """Returns a JackettClient when index_proxy.selected == 'jackett'."""
        cfg = Config()
        cfg.index_proxy.selected = "jackett"
        client = get_indexer_client(cfg)
        assert isinstance(client, JackettClient)

    def test_returns_prowlarr_client_when_selected_is_prowlarr(self) -> None:
        """Returns a ProwlarrClient when index_proxy.selected == 'prowlarr'."""
        cfg = Config()
        cfg.index_proxy.selected = "prowlarr"
        client = get_indexer_client(cfg)
        assert isinstance(client, ProwlarrClient)

    def test_raises_value_error_for_unknown_value(self) -> None:
        """Raises ValueError for an unrecognised index proxy name."""
        cfg = Config()
        cfg.index_proxy.selected = "unknown_proxy"
        with pytest.raises(ValueError, match="Unknown index proxy"):
            get_indexer_client(cfg)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_indexer.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError` for `movarr.indexer` and `movarr.prowlarr`.

- [ ] **Step 3: Create `src/movarr/indexer.py`**

```python
"""Index proxy protocol and client factory for movarr."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Generator

    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["IndexProxyProtocol", "get_indexer_client"]


@runtime_checkable
class IndexProxyProtocol(Protocol):
    """Protocol satisfied by any indexer proxy client (Jackett, Prowlarr, …)."""

    def is_reachable(self) -> bool: ...

    def search(
        self,
        index_site: str,
        criteria: str,
        category: str,
    ) -> Generator[ResultDict, None, None]: ...


def get_indexer_client(config: Config) -> IndexProxyProtocol:
    """Return the configured indexer proxy client.

    Args:
        config: Application configuration.

    Raises:
        ValueError: If ``config.index_proxy.selected`` is not a supported value.
    """
    selected = config.index_proxy.selected
    if selected == "jackett":
        from movarr.jackett import JackettClient

        return JackettClient(config)
    if selected == "prowlarr":
        from movarr.prowlarr import ProwlarrClient

        return ProwlarrClient(config)
    raise ValueError(
        f"Unknown index proxy '{selected}'. Supported values: 'jackett', 'prowlarr'."
    )
```

- [ ] **Step 4: Run tests to verify they pass (prowlarr test still fails — that's expected)**

```bash
cd /data/movarr && uv run pytest tests/unit/test_indexer.py::TestGetIndexerClient::test_returns_jackett_client_when_selected_is_jackett tests/unit/test_indexer.py::TestGetIndexerClient::test_raises_value_error_for_unknown_value -v 2>&1 | tail -10
```

Expected: 2 PASS, 1 fail (prowlarr not yet implemented).

- [ ] **Step 5: Commit the protocol skeleton**

```bash
cd /data/movarr && git add src/movarr/indexer.py tests/unit/test_indexer.py && git commit -m "feat: add IndexProxyProtocol and get_indexer_client factory

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 2: `ProwlarrClient` (`prowlarr.py`)

**Files:**
- Create: `src/movarr/prowlarr.py`
- Create: `tests/unit/test_prowlarr.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/test_prowlarr.py
"""Unit tests for movarr.prowlarr — Prowlarr JSON REST API client."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from movarr.config import Config
from movarr.downloader import HttpError
from movarr.prowlarr import ProwlarrClient

if TYPE_CHECKING:
    from pytest_mock import MockerFixture


def _make_client(mocker: MockerFixture) -> tuple[ProwlarrClient, Any]:
    """Return a ProwlarrClient with a mocked HttpClient."""
    mock_http_cls = mocker.patch("movarr.prowlarr.HttpClient")
    cfg = Config()
    client = ProwlarrClient(cfg)
    return client, mock_http_cls.return_value


class TestToMb:
    """Tests for ProwlarrClient._to_mb."""

    def test_converts_bytes_to_mb(self) -> None:
        assert ProwlarrClient._to_mb(8_589_934_592) == "8589"

    def test_truncates_remainder(self) -> None:
        assert ProwlarrClient._to_mb(1_000_001) == "1"

    def test_zero_returns_zero(self) -> None:
        assert ProwlarrClient._to_mb(0) == "0"

    def test_non_numeric_returns_zero(self) -> None:
        assert ProwlarrClient._to_mb("bad") == "0"  # type: ignore[arg-type]


class TestResolveIndexerId:
    """Tests for ProwlarrClient._resolve_indexer_id."""

    def test_all_returns_minus_one(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        assert client._resolve_indexer_id("all") == -1

    def test_numeric_string_returns_int(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        assert client._resolve_indexer_id("7") == 7

    def test_non_numeric_returns_none_and_warns(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        result = client._resolve_indexer_id("notanumber")
        assert result is None
        mock_warn.assert_called_once()


class TestParseResult:
    """Tests for ProwlarrClient._parse_result."""

    def _item(self, **kwargs: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "title": "Movie 2023 1080p BluRay",
            "indexer": "MyTracker",
            "publishDate": "2023-01-01",
            "infoUrl": "http://example.com/info",
            "seeders": 42,
            "leechers": 10,
            "size": 8_589_934_592,
            "downloadUrl": "http://example.com/dl.torrent",
            "magnetUrl": "magnet:?xt=urn:btih:abc123",
        }
        base.update(kwargs)
        return base

    def test_returns_result_dict_with_all_fields(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item())
        assert result is not None
        assert result["index_title"] == "Movie 2023 1080p BluRay"
        assert result["index_tracker"] == "MyTracker"
        assert result["index_seeders"] == "42"
        assert result["index_peers"] == "10"
        assert result["index_size"] == "8589934592"
        assert result["index_size_mb"] == "8589"
        assert result["torrent_url"] == "http://example.com/dl.torrent"
        assert result["magnet_url"] == "magnet:?xt=urn:btih:abc123"
        assert result["result"] == "Passed"

    def test_returns_none_for_missing_title(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        assert client._parse_result({"indexer": "x"}) is None

    def test_imdb_id_formatted_with_tt_prefix(self, mocker: MockerFixture) -> None:
        """Prowlarr imdbId integer 113627 → 'tt0113627'."""
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item(imdbId=113627))
        assert result is not None
        assert result.get("imdb_id") == "tt0113627"

    def test_imdb_id_zero_padded_to_seven_digits(self, mocker: MockerFixture) -> None:
        """Prowlarr imdbId integer 7 → 'tt0000007'."""
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item(imdbId=7))
        assert result is not None
        assert result.get("imdb_id") == "tt0000007"

    def test_imdb_id_absent_when_not_in_item(self, mocker: MockerFixture) -> None:
        """imdb_id key is not added when imdbId is absent."""
        client, _ = _make_client(mocker)
        result = client._parse_result(self._item())
        assert result is not None
        assert "imdb_id" not in result

    def test_missing_optional_fields_default_to_empty(self, mocker: MockerFixture) -> None:
        """Optional fields that are absent or null default to '' or '0'."""
        client, _ = _make_client(mocker)
        result = client._parse_result({"title": "Movie 2023 1080p"})
        assert result is not None
        assert result["index_tracker"] == ""
        assert result["torrent_url"] == ""
        assert result["magnet_url"] == ""
        assert result["index_size"] == "0"
        assert result["index_size_mb"] == "0"


class TestSearch:
    """Tests for ProwlarrClient.search."""

    def test_yields_results_on_success(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = [
            {"title": "Movie 2023 1080p", "seeders": 5, "size": 1_000_000_000},
        ]
        mock_http.get.return_value = mock_resp
        results = list(client.search("all", "1080p", "2000"))
        assert len(results) == 1
        assert results[0]["index_title"] == "Movie 2023 1080p"

    def test_empty_list_yields_nothing(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = []
        mock_http.get.return_value = mock_resp
        assert list(client.search("all", "1080p", "2000")) == []

    def test_http_error_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = HttpError("503 Service Unavailable")
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("all", "1080p", "2000")) == []
        mock_warn.assert_called()

    def test_json_parse_error_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.side_effect = ValueError("not JSON")
        mock_http.get.return_value = mock_resp
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("all", "1080p", "2000")) == []
        mock_warn.assert_called()

    def test_non_numeric_indexer_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, _ = _make_client(mocker)
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("not-a-number", "1080p", "2000")) == []
        mock_warn.assert_called()

    def test_non_list_response_yields_nothing_and_warns(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = {"error": "bad"}
        mock_http.get.return_value = mock_resp
        mock_warn = mocker.patch("movarr.prowlarr._logger.warning")
        assert list(client.search("all", "1080p", "2000")) == []
        mock_warn.assert_called()

    def test_url_uses_minus_one_for_all_indexer(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = []
        mock_http.get.return_value = mock_resp
        list(client.search("all", "1080p", "2000"))
        url = mock_http.get.call_args[0][0]
        assert "indexerIds=-1" in url

    def test_url_uses_numeric_id_for_specific_indexer(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = []
        mock_http.get.return_value = mock_resp
        list(client.search("7", "1080p", "2000"))
        url = mock_http.get.call_args[0][0]
        assert "indexerIds=7" in url

    def test_skips_items_with_no_title(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_resp = mocker.MagicMock()
        mock_resp.json.return_value = [{"indexer": "x", "size": 1000}]
        mock_http.get.return_value = mock_resp
        assert list(client.search("all", "1080p", "2000")) == []


class TestIsReachable:
    """Tests for ProwlarrClient.is_reachable."""

    def test_returns_true_on_successful_get(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.return_value = mocker.MagicMock()
        assert client.is_reachable() is True

    def test_returns_false_on_http_error(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = HttpError("connection refused")
        assert client.is_reachable() is False

    def test_returns_false_on_generic_exception(self, mocker: MockerFixture) -> None:
        client, mock_http = _make_client(mocker)
        mock_http.get.side_effect = OSError("network unreachable")
        assert client.is_reachable() is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_prowlarr.py -v 2>&1 | tail -20
```

Expected: `ModuleNotFoundError` for `movarr.prowlarr`.

- [ ] **Step 3: Create `src/movarr/prowlarr.py`**

```python
"""Prowlarr JSON REST API client for movarr."""

from __future__ import annotations

import urllib.parse
from typing import TYPE_CHECKING

from loguru import logger as _logger

from movarr.downloader import HttpClient, HttpError

if TYPE_CHECKING:
    from collections.abc import Generator

    from movarr.config import Config
    from movarr.models import ResultDict

__all__ = ["ProwlarrClient", "ProwlarrError"]


class ProwlarrError(Exception):
    """Raised when Prowlarr returns an unusable response."""


class ProwlarrClient:
    """Fetches search results from the Prowlarr JSON REST API.

    Args:
        config: Application configuration.
    """

    def __init__(self, config: Config) -> None:
        self._cfg = config.index_proxy.prowlarr
        self._http = HttpClient(
            connect_timeout=30.0,
            read_timeout=self._cfg.read_timeout,
        )

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def is_reachable(self) -> bool:
        """Return True if the Prowlarr API responds to the indexer list request."""
        url = (
            f"http://{self._cfg.host}:{self._cfg.port}"
            f"/api/v1/indexer?apiKey={self._cfg.api_key}"
        )
        try:
            self._http.get(url, read_timeout=self._cfg.read_timeout)
            return True
        except (HttpError, Exception) as exc:
            _logger.warning("Prowlarr health check failed: {}.", exc)
            return False

    def search(
        self,
        index_site: str,
        criteria: str,
        category: str,
    ) -> Generator[ResultDict, None, None]:
        """Yield one :class:`~movarr.models.ResultDict` per search result.

        Args:
            index_site: ``"all"`` or a numeric Prowlarr indexer ID string (e.g. ``"7"``).
            criteria: Quality/keyword search string (e.g. ``"1080p"``).
            category: Torznab category IDs (e.g. ``"2000,5000"``).
        """
        _logger.info(
            "Searching Prowlarr indexer '{}' for '{}' in category '{}'.",
            index_site,
            criteria,
            category,
        )
        indexer_id = self._resolve_indexer_id(index_site)
        if indexer_id is None:
            return

        encoded_criteria = urllib.parse.quote_plus(criteria.replace(",", " "))
        url = (
            f"http://{self._cfg.host}:{self._cfg.port}"
            f"/api/v1/search"
            f"?query={encoded_criteria}"
            f"&indexerIds={indexer_id}"
            f"&type=search"
            f"&categories={category}"
            f"&apiKey={self._cfg.api_key}"
        )
        try:
            response = self._http.get(url, read_timeout=self._cfg.read_timeout)
            items = response.json()
        except HttpError as exc:
            _logger.warning("Prowlarr HTTP error for '{}': {}.", index_site, exc)
            return
        except (ValueError, TypeError) as exc:
            _logger.warning("Prowlarr JSON parse error for '{}': {}.", index_site, exc)
            return
        except Exception as exc:
            _logger.warning("Prowlarr request failed for '{}': {}.", index_site, exc)
            return

        if not isinstance(items, list):
            _logger.warning("Prowlarr returned unexpected response type for '{}'.", index_site)
            return

        for item in items:
            result = self._parse_result(item)
            if result is not None:
                yield result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _resolve_indexer_id(self, index_site: str) -> int | None:
        """Return the numeric Prowlarr indexer ID for *index_site*.

        Returns -1 for ``"all"``, the integer value for a numeric string,
        or ``None`` (with a warning log) for non-numeric non-all values.
        """
        if index_site == "all":
            return -1
        try:
            return int(index_site)
        except ValueError:
            _logger.warning(
                "Prowlarr indexer '{}' is not numeric and not 'all'; skipping.",
                index_site,
            )
            return None

    def _parse_result(self, item: dict) -> ResultDict | None:
        """Extract a :class:`~movarr.models.ResultDict` from one Prowlarr JSON result."""
        index_title: str | None = item.get("title")
        if not index_title:
            return None

        size_bytes = item.get("size", 0) or 0
        result: ResultDict = {
            "index_title": index_title,
            "index_tracker": item.get("indexer", ""),
            "index_pubdate": item.get("publishDate", ""),
            "index_details": item.get("infoUrl", ""),
            "index_seeders": str(item.get("seeders", "")),
            "index_peers": str(item.get("leechers", "")),
            "index_size": str(size_bytes),
            "index_size_mb": self._to_mb(size_bytes),
            "torrent_url": item.get("downloadUrl", "") or "",
            "magnet_url": item.get("magnetUrl", "") or "",
            "category": "",
            "result": "Passed",
            "result_details": [],
        }

        imdb_id_raw = item.get("imdbId")
        if imdb_id_raw:
            try:
                result["imdb_id"] = f"tt{int(imdb_id_raw):07d}"
            except (ValueError, TypeError):
                pass

        return result

    @staticmethod
    def _to_mb(size_bytes: int | float) -> str:
        """Convert bytes to a decimal megabyte string (integer, truncated)."""
        try:
            return str(int(size_bytes) // 1_000_000)
        except (ValueError, TypeError):
            return "0"
```

- [ ] **Step 4: Run all prowlarr + indexer tests**

```bash
cd /data/movarr && uv run pytest tests/unit/test_prowlarr.py tests/unit/test_indexer.py -v 2>&1 | tail -30
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
cd /data/movarr && git add src/movarr/prowlarr.py tests/unit/test_prowlarr.py && git commit -m "feat: add ProwlarrClient with JSON REST API search

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 3: Config — `ProwlarrConfig`, `IndexProxyConfig`, migration v2.4.0 → v2.5.0

**Files:**
- Modify: `src/movarr/config.py`
- Modify: `tests/unit/test_config.py`

- [ ] **Step 1: Write the failing migration tests**

Add this class to `tests/unit/test_config.py` after the existing `TestConfigMigration` class:

```python
class TestMigrationV24toV25:
    """Migration v2.4.0 → v2.5.0 adds Prowlarr config and prowlarr_indexer."""

    def _v24_config(self, tmp_path: Path) -> Path:
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.4.0'\n"
            "notification:\n  apprise_urls: []\n"
        )
        return cfg_file

    def test_v24_config_migrated_to_v25(self, tmp_path: Path) -> None:
        """A v2.4.0 config is migrated to v2.5.0, adding Prowlarr fields."""
        cfg_file = self._v24_config(tmp_path)
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"
        assert cfg.index_proxy.prowlarr.host == "localhost"
        assert cfg.index_proxy.prowlarr.port == 9696
        assert cfg.index_site.prowlarr_indexer == "all"

    def test_v24_migration_creates_backup(self, tmp_path: Path) -> None:
        """A backup file config.yml.bak.2.4.0 is created before migration."""
        cfg_file = self._v24_config(tmp_path)
        load_config(str(cfg_file))
        assert (tmp_path / "config.yml.bak.2.4.0").exists()

    def test_v24_migration_writes_prowlarr_block_to_disk(self, tmp_path: Path) -> None:
        """After migration the on-disk YAML contains the prowlarr block."""
        cfg_file = self._v24_config(tmp_path)
        load_config(str(cfg_file))
        raw = yaml.safe_load(cfg_file.read_text())
        assert "prowlarr" in raw.get("index_proxy", {})
        assert raw["index_proxy"]["prowlarr"]["port"] == 9696

    def test_v24_migration_writes_prowlarr_indexer_to_disk(self, tmp_path: Path) -> None:
        """After migration the on-disk YAML contains prowlarr_indexer: all."""
        cfg_file = self._v24_config(tmp_path)
        load_config(str(cfg_file))
        raw = yaml.safe_load(cfg_file.read_text())
        assert raw.get("index_site", {}).get("prowlarr_indexer") == "all"

    def test_v24_migration_preserves_existing_jackett_config(self, tmp_path: Path) -> None:
        """Existing jackett config values are not overwritten by migration."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.4.0'\n"
            "index_proxy:\n"
            "  selected: jackett\n"
            "  jackett:\n"
            "    host: myjackett\n"
            "    port: 9117\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.index_proxy.jackett.host == "myjackett"

    def test_existing_config_at_v25_needs_no_migration(self, tmp_path: Path) -> None:
        """A config already at v2.5.0 is not re-migrated."""
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text(
            "general:\n  config_version: '2.5.0'\n"
        )
        cfg = load_config(str(cfg_file))
        assert cfg.general.config_version == "2.5.0"
        backup = tmp_path / "config.yml.bak.2.5.0"
        assert not backup.exists()
```

Also add `ProwlarrConfig` to the import in `test_config.py`:
```python
from movarr.config import (
    Config,
    DatabaseConfig,
    GeneralConfig,
    ProwlarrConfig,
    QueueManagementConfig,
    ScheduleTaskConfig,
    load_config,
)
```

And add a defaults test class:
```python
class TestProwlarrConfigDefaults:
    """ProwlarrConfig must have sane defaults."""

    def test_default_host(self) -> None:
        cfg = ProwlarrConfig()
        assert cfg.host == "localhost"

    def test_default_port(self) -> None:
        cfg = ProwlarrConfig()
        assert cfg.port == 9696

    def test_default_api_key_is_empty(self) -> None:
        cfg = ProwlarrConfig()
        assert cfg.api_key == ""

    def test_default_read_timeout(self) -> None:
        cfg = ProwlarrConfig()
        assert cfg.read_timeout == 60.0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py::TestMigrationV24toV25 tests/unit/test_config.py::TestProwlarrConfigDefaults -v 2>&1 | tail -20
```

Expected: `ImportError` (ProwlarrConfig not yet in config.py) or `AssertionError` on version check.

- [ ] **Step 3: Modify `src/movarr/config.py`**

**3a.** Update `_CONFIG_VERSION` at line 18:
```python
_CONFIG_VERSION = "2.5.0"
```

**3b.** Add `ProwlarrConfig` after `JackettConfig` (after line 179):
```python
class ProwlarrConfig(BaseModel):
    """Prowlarr indexer proxy settings."""

    host: str = "localhost"
    port: int = 9696
    api_key: str = ""
    read_timeout: float = 60.0
```

**3c.** Update `IndexProxyConfig` to add the `prowlarr` field:
```python
class IndexProxyConfig(BaseModel):
    """Index proxy selection and settings."""

    selected: str = "jackett"
    jackett: JackettConfig = Field(default_factory=JackettConfig)
    prowlarr: ProwlarrConfig = Field(default_factory=ProwlarrConfig)
```

**3d.** Update `IndexSiteConfig` to add `prowlarr_indexer`:
```python
class IndexSiteConfig(BaseModel):
    """Per-indexer search configuration."""

    jackett_indexer: str = "all"
    prowlarr_indexer: str = "all"
    ignore_list: list[str] = Field(default_factory=list)
    search: list[SearchCriteriaConfig] = Field(
        default_factory=lambda: [
            SearchCriteriaConfig(criteria="1080p", minimum_size_mb=3000, maximum_size_mb=20000, minimum_bitrate_mb=50),
            SearchCriteriaConfig(
                criteria="2160p", minimum_size_mb=7000, maximum_size_mb=170000, minimum_bitrate_mb=115
            ),
        ]
    )
    override_search: dict[str, dict[str, str]] = Field(default_factory=dict)
```

**3e.** Add migration function after `_migrate_v23_to_v24` (after line 57):
```python
def _migrate_v24_to_v25(raw: dict[str, Any]) -> dict[str, Any]:
    """Migrate v2.4.0 → v2.5.0: add Prowlarr config block and prowlarr_indexer."""
    raw.setdefault("index_proxy", {}).setdefault("prowlarr", {
        "host": "localhost",
        "port": 9696,
        "api_key": "",
        "read_timeout": 60.0,
    })
    raw.setdefault("index_site", {}).setdefault("prowlarr_indexer", "all")
    raw.setdefault("general", {})["config_version"] = "2.5.0"
    return raw
```

**3f.** Add the migration to `MIGRATIONS`:
```python
MIGRATIONS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "1.0.0": _migrate_v1_to_v2,
    "2.0.0": _migrate_v2_to_v21,
    "2.1.0": _migrate_v21_to_v22,
    "2.2.0": _migrate_v22_to_v23,
    "2.3.0": _migrate_v23_to_v24,
    "2.4.0": _migrate_v24_to_v25,
}
```

**3g.** Update the `__all__` export to include `ProwlarrConfig`:
```python
__all__ = ["Config", "ProwlarrConfig", "load_config"]
```

- [ ] **Step 4: Run new config tests**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py::TestMigrationV24toV25 tests/unit/test_config.py::TestProwlarrConfigDefaults -v 2>&1 | tail -20
```

Expected: all PASS.

- [ ] **Step 5: Run full test suite to confirm no regressions**

```bash
cd /data/movarr && uv run pytest tests/unit/test_config.py -v 2>&1 | tail -20
```

Expected: all PASS. Note: previously passing migration tests will now report `"2.5.0"` as the final version — update any that assert `"2.4.0"` as the terminal version.

> **Note:** Every test in `TestConfigMigration` that asserts `cfg.general.config_version == "2.4.0"` (e.g. `test_v1_config_is_migrated_to_v2`, `test_v2_config_needs_no_migration`, `test_v2_config_migrated_to_v21`, etc.) must be updated to assert `"2.5.0"` instead. The tests are:
> - `test_v1_config_is_migrated_to_v2`: change `"2.4.0"` → `"2.5.0"`
> - `test_no_version_key_treated_as_v1`: change `"2.4.0"` → `"2.5.0"`
> - `test_v2_config_needs_no_migration`: change `"2.4.0"` → `"2.5.0"`
> - `test_v2_config_migrated_to_v21`: change `"2.4.0"` → `"2.5.0"`
> - `test_v21_config_migrated_to_v22`: change `"2.4.0"` → `"2.5.0"`
> - `test_v22_config_migrated_to_v23`: change `"2.4.0"` → `"2.5.0"`
> - `test_v23_config_migrated_to_v24`: change `"2.4.0"` → `"2.5.0"`

- [ ] **Step 6: Commit**

```bash
cd /data/movarr && git add src/movarr/config.py tests/unit/test_config.py && git commit -m "feat: add ProwlarrConfig and config migration v2.4.0 → v2.5.0

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 4: Update `search.py` + `test_search.py`

**Files:**
- Modify: `src/movarr/search.py`
- Modify: `tests/unit/test_search.py`

- [ ] **Step 1: Update the failing tests in `test_search.py`**

Make the following targeted changes:

**4a.** Update the import at the top of `test_search.py` — replace `movarr.search.JackettClient` patches with `movarr.search.get_indexer_client` and rename the `_SearchSession` field from `jackett=` to `indexer=`.

Change line 15:
```python
from movarr.search import _enrich_index_metadata, _process_criteria, _SearchSession, run_search
```
(no change needed here, just confirming the import)

**4b.** In `TestRunSearch._call` helper (lines 189-201), rename `jackett=jackett` → `indexer=jackett` (the variable name stays `jackett` for now to minimise churn):

```python
def _call(
    self,
    mocker: MockerFixture,
    jackett: Any,
    qbt: Any,
    db: Any,
    config: Config | None = None,
) -> None:
    session = _SearchSession(
        config=config or Config(),
        indexer=jackett,
        qbt=qbt,
        db=db,
        library_walk=None,
    )
    _process_criteria(
        criteria_cfg=self._criteria_cfg(),
        category="2000",
        indexer="all",
        session=session,
    )
```

**4c.** In `TestRunSearch`, replace `mocker.patch("movarr.search.JackettClient")` → `mocker.patch("movarr.search.get_indexer_client")` in these three tests:

```python
def test_no_search_criteria_skips_jackett(self, mocker: MockerFixture) -> None:
    cfg = Config()
    cfg.index_site.search = []
    mock_client_factory = mocker.patch("movarr.search.get_indexer_client")
    qbt = mocker.MagicMock()
    db = mocker.MagicMock()

    run_search(cfg, qbt, db)

    mock_client_factory.assert_not_called()

def test_jackett_not_reachable_skips_criteria_processing(self, mocker: MockerFixture) -> None:
    cfg = Config()
    mock_client_factory = mocker.patch("movarr.search.get_indexer_client")
    mock_client_factory.return_value.is_reachable.return_value = False
    mocker.patch("movarr.search._process_criteria")
    qbt = mocker.MagicMock()
    db = mocker.MagicMock()

    run_search(cfg, qbt, db)

    mocker.patch("movarr.search._process_criteria").assert_not_called()

def test_processes_each_criteria_tier(self, mocker: MockerFixture) -> None:
    cfg = Config()
    mock_client_factory = mocker.patch("movarr.search.get_indexer_client")
    mock_client_factory.return_value.is_reachable.return_value = True
    mock_process = mocker.patch("movarr.search._process_criteria")
    qbt = mocker.MagicMock()
    db = mocker.MagicMock()

    run_search(cfg, qbt, db)

    assert mock_process.call_count == len(cfg.index_site.search)

def test_passes_jackett_instance_to_process_criteria(self, mocker: MockerFixture) -> None:
    cfg = Config()
    mock_client_factory = mocker.patch("movarr.search.get_indexer_client")
    mock_client_factory.return_value.is_reachable.return_value = True
    mock_process = mocker.patch("movarr.search._process_criteria")
    qbt = mocker.MagicMock()
    db = mocker.MagicMock()

    run_search(cfg, qbt, db)

    call_kwargs = mock_process.call_args_list[0][1]
    assert call_kwargs["session"].indexer is mock_client_factory.return_value
```

**4d.** In `TestPassedAllFiltersLogLevel.test_passed_all_filters_logs_at_success` (line 540), change:
```python
session = _SearchSession(config=cfg, jackett=mock_jackett, qbt=mock_qbt, db=mock_db, library_walk=None)
```
→
```python
session = _SearchSession(config=cfg, indexer=mock_jackett, qbt=mock_qbt, db=mock_db, library_walk=None)
```

**4e.** In `TestDbDeduplication._make_session` (line 562-569), change:
```python
return _SearchSession(config=cfg, jackett=mock_jackett, qbt=mock_qbt, db=mock_db, library_walk=None), mock_db
```
→
```python
return _SearchSession(config=cfg, indexer=mock_jackett, qbt=mock_qbt, db=mock_db, library_walk=None), mock_db
```

**4f.** In `TestRunSearchLibraryWalkAndOverride._make_base` (line 631), change:
```python
mock_jackett_cls = mocker.patch("movarr.search.JackettClient")
mock_jackett_cls.return_value.is_reachable.return_value = True
```
→
```python
mock_client_factory = mocker.patch("movarr.search.get_indexer_client")
mock_client_factory.return_value.is_reachable.return_value = True
```

Also update `test_override_search_replaces_category` (line 652):
```python
mocker.patch("movarr.search.get_indexer_client").return_value.is_reachable.return_value = True
```

**4g.** In `TestProcessCriteriaNoYear._make_session` (line 666-677), change:
```python
return _SearchSession(config=cfg, jackett=mock_jackett, qbt=qbt, db=db, library_walk=None)
```
→
```python
return _SearchSession(config=cfg, indexer=mock_jackett, qbt=qbt, db=db, library_walk=None)
```

- [ ] **Step 2: Run test_search.py to verify failures**

```bash
cd /data/movarr && uv run pytest tests/unit/test_search.py -v 2>&1 | grep -E "PASS|FAIL|ERROR" | head -30
```

Expected: failures related to `jackett=` keyword arg and `JackettClient` patch not found.

- [ ] **Step 3: Update `src/movarr/search.py`**

**3a.** Replace the import block at the top. Change:
```python
from movarr.jackett import JackettClient
```
→
```python
from movarr.indexer import IndexProxyProtocol, get_indexer_client
```

(Remove the `JackettClient` import entirely.)

**3b.** Update `_SearchSession` dataclass — rename `jackett: JackettClient` → `indexer: IndexProxyProtocol`:

```python
@dataclass(frozen=True)
class _SearchSession:
    """Immutable session-level dependencies shared across all criteria tiers."""

    config: Config
    indexer: IndexProxyProtocol
    qbt: QBittorrentClient
    db: Database
    library_walk: list | None
```

**3c.** Update `run_search` — replace the `JackettClient` instantiation and reachability check, and update the indexer selection logic:

```python
def run_search(config: Config, qbt: QBittorrentClient, db: Database) -> None:
    """Run the full search pipeline for all configured criteria tiers.

    Args:
        config: Application configuration.
        qbt: An already-connected ``QBittorrentClient`` instance.
        db: Open database instance.
    """
    site_cfg = config.index_site
    if not site_cfg.search:
        logger.info("No search criteria configured; skipping search.")
        return

    indexer_client = get_indexer_client(config)
    if not indexer_client.is_reachable():
        logger.warning(
            "{} is not reachable; skipping search.",
            config.index_proxy.selected.capitalize(),
        )
        return

    library_walk: list[tuple[str, list[str], list[str]]] | None = None
    if config.general.library_path_list:
        library_walk = list(walk_library(config.general.library_path_list))

    session = _SearchSession(
        config=config,
        indexer=indexer_client,
        qbt=qbt,
        db=db,
        library_walk=library_walk,
    )

    for criteria_cfg in site_cfg.search:
        index_site = (
            site_cfg.jackett_indexer
            if config.index_proxy.selected == "jackett"
            else site_cfg.prowlarr_indexer
        )
        category = criteria_cfg.category
        if index_site in site_cfg.override_search:
            overrides = site_cfg.override_search[index_site]
            if "category" in overrides:
                category = overrides["category"]

        logger.info(
            "Searching indexer '{}' for '{}' (category '{}').",
            index_site,
            criteria_cfg.criteria,
            category,
        )
        _process_criteria(criteria_cfg=criteria_cfg, category=category, indexer=index_site, session=session)
```

**3d.** Update `_process_criteria` — change `session.jackett.search` → `session.indexer.search` and update the docstring:

```python
def _process_criteria(
    criteria_cfg: SearchCriteriaConfig,
    category: str,
    indexer: str,
    session: _SearchSession,
) -> None:
    """Fetch and process all indexer results for one criteria tier."""
    site_dict = criteria_cfg.model_dump()

    for result in session.indexer.search(indexer, criteria_cfg.criteria, category):
        result = _enrich_index_metadata(result)
        # ... (rest of the function body is unchanged)
```

- [ ] **Step 4: Run full test_search.py suite**

```bash
cd /data/movarr && uv run pytest tests/unit/test_search.py -v 2>&1 | tail -30
```

Expected: all PASS.

- [ ] **Step 5: Run the full test suite**

```bash
cd /data/movarr && uv run pytest --tb=short 2>&1 | tail -20
```

Expected: all PASS, 100% coverage.

- [ ] **Step 6: Commit**

```bash
cd /data/movarr && git add src/movarr/search.py tests/unit/test_search.py && git commit -m "refactor: replace JackettClient with IndexProxyProtocol in search pipeline

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 5: README documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add Prowlarr config documentation**

Find the existing `index_proxy` section in `README.md` and expand it. The section currently documents only `jackett`. Add the `prowlarr` sub-block and explain the `selected` field:

```markdown
### Index Proxy (`index_proxy`)

Controls which indexer proxy movarr uses to search for torrents.

| Field | Default | Description |
|---|---|---|
| `selected` | `jackett` | Which proxy to use: `jackett` or `prowlarr` |

#### Jackett (`index_proxy.jackett`)

| Field | Default | Description |
|---|---|---|
| `host` | `localhost` | Jackett hostname |
| `port` | `9117` | Jackett port |
| `api_key` | `` | Jackett API key |
| `read_timeout` | `60.0` | Seconds to wait for a response |
| `limit` | `500` | Results per page |
| `offset` | `0` | Max pagination offset |

#### Prowlarr (`index_proxy.prowlarr`)

| Field | Default | Description |
|---|---|---|
| `host` | `localhost` | Prowlarr hostname |
| `port` | `9696` | Prowlarr port |
| `api_key` | `` | Prowlarr API key (Settings → General → API Key) |
| `read_timeout` | `60.0` | Seconds to wait for a response |

### Index Site (`index_site`)

| Field | Default | Description |
|---|---|---|
| `jackett_indexer` | `all` | Jackett indexer slug (`all` or specific slug e.g. `rarbg`) |
| `prowlarr_indexer` | `all` | Prowlarr indexer: `all` (searches all configured indexers) or a numeric indexer ID (e.g. `7`) |
```

- [ ] **Step 2: Verify README renders correctly (optional manual check)**

```bash
cd /data/movarr && grep -A5 "prowlarr" README.md | head -30
```

- [ ] **Step 3: Commit**

```bash
cd /data/movarr && git add README.md && git commit -m "docs: document Prowlarr config fields in README

Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

---

## Task 6: Final verification

- [ ] **Step 1: Run the full test suite with coverage**

```bash
cd /data/movarr && uv run pytest --cov=src/movarr --cov-report=term-missing --tb=short 2>&1 | tail -30
```

Expected: all PASS, 100% coverage, no uncovered lines in `prowlarr.py` or `indexer.py`.

- [ ] **Step 2: Verify `movarr` starts cleanly with default config (Jackett path)**

```bash
cd /data/movarr && uv run movarr --help 2>&1 | head -5
```

Expected: help text prints without import errors.

- [ ] **Step 3: Verify config migration runs cleanly from v2.4.0 to v2.5.0**

```bash
cd /data/movarr && python -c "
from movarr.config import load_config
import tempfile, pathlib, yaml
with tempfile.TemporaryDirectory() as d:
    p = pathlib.Path(d) / 'cfg.yml'
    p.write_text('general:\n  config_version: \"2.4.0\"\n')
    cfg = load_config(str(p))
    print('version:', cfg.general.config_version)
    print('prowlarr host:', cfg.index_proxy.prowlarr.host)
    print('prowlarr_indexer:', cfg.index_site.prowlarr_indexer)
" 2>&1 | grep -v "^$"
```

Expected output:
```
version: 2.5.0
prowlarr host: localhost
prowlarr_indexer: all
```

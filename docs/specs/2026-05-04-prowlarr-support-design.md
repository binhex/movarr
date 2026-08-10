# Prowlarr Support Design

**Date:** 2026-05-04
**Status:** Approved
**Scope:** Add Prowlarr as an alternative index proxy alongside the existing Jackett support.

---

## Problem

movarr currently supports only Jackett as an indexer proxy. Prowlarr is a widely-used
alternative that exposes a cleaner JSON REST API and is increasingly preferred by users.
Prowlarr support must be additive — all existing Jackett behaviour is preserved unchanged.

---

## Architecture

```
config.index_proxy.selected = "jackett" | "prowlarr"
               │
               ▼
   get_indexer_client(config)        ← factory in src/movarr/indexer.py
        │              │
        ▼              ▼
 JackettClient   ProwlarrClient      ← both satisfy IndexProxyProtocol
        │              │
        └──────┬───────┘
               ▼
     IndexProxyProtocol              ← Protocol: search() + is_reachable()
               │
               ▼
          search.py                  ← consumes protocol type; no client-specific logic
```

### Files changed

| File | Change |
|---|---|
| `src/movarr/indexer.py` | **New** — `IndexProxyProtocol` + `get_indexer_client()` factory |
| `src/movarr/prowlarr.py` | **New** — `ProwlarrClient` + `ProwlarrError` |
| `src/movarr/jackett.py` | Unchanged |
| `src/movarr/search.py` | Swap `JackettClient(config)` → `get_indexer_client(config)`; update type annotation |
| `src/movarr/config.py` | Add `ProwlarrConfig`; add `prowlarr` field on `IndexProxyConfig`; add `prowlarr_indexer` on `IndexSiteConfig`; migration v2.4.0 → v2.5.0 |
| `README.md` | Document new `index_proxy.prowlarr` config block and `prowlarr_indexer` field |

---

## Protocol

```python
class IndexProxyProtocol(Protocol):
    def is_reachable(self) -> bool: ...
    def search(self, index_site: str, criteria: str, category: str) -> Generator[ResultDict, None, None]: ...
```

Both `JackettClient` and `ProwlarrClient` implicitly satisfy this protocol.
`get_indexer_client(config)` returns `IndexProxyProtocol` and raises `ValueError`
for unknown `selected` values at startup.

---

## Config

### New YAML shape

```yaml
index_proxy:
  selected: prowlarr          # "jackett" (default) or "prowlarr"
  jackett:
    host: localhost
    port: 9117
    api_key: ""
    read_timeout: 60.0
    limit: 500
    offset: 0
  prowlarr:                   # new block
    host: localhost
    port: 9696
    api_key: ""
    read_timeout: 60.0

index_site:
  jackett_indexer: all        # existing — untouched
  prowlarr_indexer: all       # new — "all" or numeric indexer ID string e.g. "7"
```

### ProwlarrConfig model

```python
class ProwlarrConfig(BaseModel):
    host: str = "localhost"
    port: int = 9696
    api_key: str = ""
    read_timeout: float = 60.0
```

### Config migration v2.4.0 → v2.5.0

- Writes `prowlarr` block with defaults under `index_proxy` if absent.
- Writes `prowlarr_indexer: all` under `index_site` if absent.
- Creates a `.bak.2.4.0` backup before modifying.

---

## ProwlarrClient

### Search API call

```
GET http://{host}:{port}/api/v1/search
    ?query={encoded_criteria}
    &indexerIds={id}          # -1 for "all"; numeric int for specific indexer
    &type=search
    &categories={category}
    &apiKey={api_key}
```

Prowlarr does not paginate this endpoint (returns all results in one call, up to the
server-side limit). No offset/limit loop is needed.

### Reachability check

```
GET http://{host}:{port}/api/v1/indexer?apiKey={api_key}
```

Returns a JSON array of configured indexers. Any non-exception HTTP response
indicates the service is reachable. Returns `False` + warning log on any failure.

### Result mapping

| Prowlarr JSON field | `ResultDict` key | Notes |
|---|---|---|
| `title` | `index_title` | |
| `indexer` | `index_tracker` | |
| `publishDate` | `index_pubdate` | |
| `infoUrl` | `index_details` | |
| `seeders` | `index_seeders` | string-cast |
| `leechers` | `index_peers` | string-cast |
| `size` | `index_size` + `index_size_mb` | bytes → MB (÷ 1,000,000) |
| `downloadUrl` | `torrent_url` | |
| `magnetUrl` | `magnet_url` | |
| `imdbId` | `imdb_id` | optional integer; converted to `"tt{n:07d}"` format (e.g. `113627` → `"tt0113627"`) |

Fields absent or null in the Prowlarr response default to `""`.

### Indexer ID resolution

- `prowlarr_indexer == "all"` → `indexerIds=-1`
- Any other value → cast to `int` and pass as `indexerIds={n}`; log a warning and skip if non-numeric

---

## Error Handling

- `ProwlarrError` raised on HTTP failure or unparseable JSON response.
- `is_reachable()` catches all exceptions; logs a warning; returns `False`.
- `get_indexer_client()` raises `ValueError` immediately if `selected` is not `"jackett"` or `"prowlarr"`.
- Existing internet-connectivity checks (used by stalled torrent logic) cover Prowlarr transparently — no extra wiring needed.

---

## Testing

| Test file | Coverage |
|---|---|
| `tests/unit/test_prowlarr.py` | `ProwlarrClient`: search success, empty results, HTTP error, JSON parse error, `is_reachable()` pass/fail, `"all"` vs numeric indexer ID, `imdbId` extraction, missing optional fields |
| `tests/unit/test_indexer.py` | Factory returns `JackettClient` for `"jackett"`, `ProwlarrClient` for `"prowlarr"`, raises `ValueError` for unknown value |
| `tests/unit/test_config.py` | Migration v2.4.0 → v2.5.0: adds `prowlarr` block; adds `prowlarr_indexer`; backup created; existing values untouched |
| `tests/unit/test_search.py` | Updated to inject a mock satisfying `IndexProxyProtocol` instead of a real `JackettClient` |

Target: 100% coverage maintained.

---

## Out of Scope

- Prowlarr tag-based indexer filtering (can be added later).
- Authentication beyond API key (Prowlarr supports forms auth; not needed for API key usage).
- Deluge torrent client support (separate feature).

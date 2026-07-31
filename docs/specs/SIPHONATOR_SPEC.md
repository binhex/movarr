# Siphonator — Functional Specification

> This document is a concise, thorough analysis of the Siphonator codebase as of commit `49b9964`. It is intended to serve as the basis for a ground-up rewrite (**Movarr**).

---

## 1. Overview

Siphonator is a Python-based automated movie torrent acquisition and post-processing daemon. It:

1. Polls Jackett (indexer proxy) for movie torrents matching configured search criteria.
2. Filters candidates by index metadata (size, keywords, etc) and IMDb metadata (rating, genre, runtime, language, country, cast/crew).
3. Adds passing torrents to qBittorrent (tagged with a category).
4. Monitors the qBittorrent queue for stalled/metadata-stuck torrents and deletes them.
5. Post-processes completed downloads: copies files to a destination library path, with routing based on **genre + resolution + BBFC certification**.
6. Persists every decision to an SQLite history database.
7. Sends email notifications for accepted torrents.

---

## 2. Architecture

### 2.1 Entry Point — `siphonator.py`

- **Python 3.11+ required.**
- Parses CLI args:
  | Arg | Default | Description |
  |-----|---------|-------------|
  | `-lp` / `--log-path` | `./logs` | Log directory |
  | `-ll` / `--log-level` | `INFO` | Console log level |
  | `-pp` / `--pid-path` | `./pid` | PID file directory |
  | `-cp` / `--config-path` | `./configs` | Config directory |
  | `-dp` / `--db-path` | `./db` | Database directory |
  | `-fp` / `--ffprobe-path` | (system PATH) | ffprobe binary path |
  | `--daemon` | (foreground) | Daemonise process |
  | `-t` / `--test` | — | Run test mode |
  | `-v` / `--version` | — | Print version |
- Initialises `init_dict` carrying bootstrap paths, version strings, and `user_agent`.
- Creates logger, config manager, DB, and qBittorrent client.
- If `--daemon` is passed, daemonises via `daemonize.Daemonize` and starts an APScheduler (`BackgroundScheduler`).
- Otherwise runs a single pass (foreground mode).
- Schedules three independent interval-based tasks:

| Task | Default Interval | Purpose |
|------|-----------------|---------|
| `siphonator_task` | 30 min | Main acquisition loop (search → filter → add) |
| `queue_management_task` | 5 min | Delete stalled / metadata-stuck torrents |
| `post_processing_task` | 5 min | Copy completed downloads to library |

- Tracks DB schema version (`db_version = 7`). Runs `create_tables()` on first start, `upgrade_database()` on version mismatch.
- All three tasks create their own DB instances (SQLite thread-safety requirement).

### 2.2 Module Map

| Module | Role |
|--------|------|
| `siphonator.py` | Entry point, CLI, scheduler orchestration |
| `config_manager.py` | Load / create / write YAML config (`config.yml`) |
| `config_validate.py` | Pydantic models for config validation (WIP, not imported at runtime) |
| `db_sqlite.py` | SQLite history table CRUD, schema versioning, vacuum |
| `tools_logging.py` | `app_logging()` — rotating file + console handler setup |
| `tools_various.py` | Time helpers, path walking, YAML/JSON pretty-print, file copy, ffprobe wrapper |
| `tools_filters.py` | Regex-heavy string parsing: extract title/year/resolution/group, sanitise, compare, SQLite query building |
| `tools_downloader.py` | `http_client()` with exponential backoff (requests + backoff lib) |
| `index_proxy.py` | Jackett Torznab XML feed parser; iterates items; drives the full per-torrent pipeline |
| `search_imdb.py` | Find IMDb ID via `imdbpie` title search |
| `search_tmdb.py` | Find IMDb ID via TMDb API (title → TMDb ID → IMDb ID) |
| `search_omdb.py` | Find IMDb ID via OMDb API |
| `search_google.py` | Fallback IMDb ID search via Google scrape |
| `search_all.py` | Orchestrates ID search: IMDb → TMDb → OMDb → Google |
| `imdb_imdbpie.py` | Fetches full IMDb metadata (title, year, rating, votes, runtime, genres, cast/crew, certification, etc) via `imdbpie` |
| `imdb_omdb.py` | Fallback IMDb metadata fetch via OMDb API |
| `filter_movies.py` | **Two-phase filtering:** index-level filters (size, keywords, library dupes) and IMDb-level filters (rating, genre, runtime, language, country, cast/crew overrides) |
| `filter_movies_new.py` | **WIP rewrite** of `filter_movies.py` (not imported). Key behavioural diff: moves library duplicate check to the **end** of `filter_imdb_movies()`, using the canonical IMDb title instead of the raw index title. Evaluate for rewrite adoption. |
| `torrent_clients.py` | qBittorrent API wrapper: connection checks, add torrent, delete stalled, grace-period logic |
| `queue_management.py` | Schedules deletion of torrents stuck in `metaDL` or `stalledDL` states beyond configured timeouts |
| `post_processing.py` | Copies completed torrent files to library; **genre/resolution/certification routing** |
| `notification_email.py` | Sends rich HTML email via `nmdmail` for each accepted torrent |

---

## 3. Data Flow

### 3.1 Main Acquisition Loop (`siphonator_task`)

```
siphonator.py:run()
  ├─ check_qbittorrent() ──→ qBittorrent Client
  ├─ check_jackett() ──────→ Jackett indexers XML
  ├─ library_path_walk() ──→ walk existing library paths (dedup)
  │
  └─ For each Jackett indexer (configured + not in ignore_list):
        └─ For each search criteria (e.g. "1080p", "2160p", "2160p remux"):
              └─ index_proxy.py:jackett()
                    ├─ HTTP GET Jackett Torznab feed
                    ├─ XML parse → iterate <item> nodes
                    │
                    ├─ Per item:
                    │    ├─ Deduplication check (SQLite simple + advanced match) ──→ BEFORE parsing
                    │    ├─ Parse index metadata (title, size, seeders, peers, magnet, torrent URL, imdbid)
                    │    ├─ tools_filters.index_name() ──→ extract movie_title, year, resolution, group
                    │    │
                    │    ├─ If title/year unparseable → write Failed to DB, continue
                    │    │
                    │    ├─ filter_movies.filter_index_movies()
                    │    │    ├─ size min/max
                    │    │    ├─ bad keywords in index title
                    │    │    ├─ bad type (TV season/episode detection)
                    │    │    ├─ bad movie title list
                    │    │    └─ library duplicate check
                    │    │
                    │    ├─ If Passed → search_all.search() for IMDb ID
                    │    │    ├─ IMDbPie title search
                    │    │    ├─ TMDb title search
                    │    │    ├─ OMDb title search
                    │    │    └─ Google scrape
                    │    │
                    │    ├─ If IMDb ID found → imdb_imdbpie.imdb_json_api()
                    │    │    ├─ Full metadata: title, year, rating, votes, runtime, genres,
                    │    │    │   cast, director, writer, characters, languages, countries,
                    │    │    │   certification, plot, poster, trailer
                    │    │    └─ If fails → fallback to imdb_omdb.omdb_json_api()
                    │    │
                    │    ├─ filter_movies.filter_imdb_movies()
                    │    │    ├─ good title type (movie, video, tvmovie)
                    │    │    ├─ bad genre list
                    │    │    ├─ bitrate check (size / runtime)
                    │    │    ├─ minimum year
                    │    │    ├─ minimum runtime
                    │    │    ├─ good language list
                    │    │    ├─ good country list
                    │    │    ├─ override chain (character → director → writer → cast → movie title → genre)
                    │    │    │   (If any override matches, lower rating/vote thresholds apply)
                    │    │    ├─ minimum rating (with genre-specific overrides)
                    │    │    └─ minimum votes (with genre-specific overrides)
                    │    │
                    │    ├─ If Passed → send email notification (BEFORE torrent add; crash here prevents add)
                    │    ├─ Add torrent to qBittorrent (category = "movies-siphonator")
                    │    └─ Write result to SQLite history
                    │
                    └─ Increment offset, repeat until max_offset reached
```

### 3.2 Queue Management Loop (`queue_management_task`)

```
queue_management.run()
  ├─ check_qbittorrent() connection + grace period
  ├─ qbittorrent_list_torrents() ──→ all torrents with category
  ├─ delete_stalled_torrents('metadata', 'metaDL', 'added_on')
  │    └─ Delete torrents stuck in metaDL > metadata_delete_torrent_max_mins (30 min)
  └─ delete_stalled_torrents('stalled', 'stalledDL', 'last_activity')
       └─ Delete torrents stuck in stalledDL > stalled_delete_torrent_max_mins (120 min)
```

### 3.3 Post-Processing Loop (`post_processing_task`)

```
post_processing.run()
  ├─ check_qbittorrent()
  ├─ Iterate two status filters: ['stopped', 'completed']
  ├─ For each torrent in status_filter:
  │    ├─ If already verified in DB → skip
  │    ├─ If copy_completed enabled → copy_files_dst()
  │    │    (SHA256 pre-check: skip if dest exists and matches; delete+recopy if different)
  │    ├─ If status == 'stopped' AND remove_completed enabled → delete torrent + data from qBittorrent
  │    ├─ If status == 'completed' → leave in client (copy only)
  │    │    ├─ Lookup DB record for torrent tag
  │    │    ├─ Parse genres from DB (JSON or Python repr)
  │    │    ├─ Re-derive resolution from stored index_title (1080→HD, 2160→UHD)
  │    │    ├─ _resolve_copy_destination()
  │    │    │    ├─ Score each rule by # of overlapping genres
  │    │    │    ├─ Highest unique scorer wins
  │    │    │    ├─ Tie or zero match → default_copy_library
  │    │    │    ├─ Optional max_certification check (BBFC ordering)
  │    │    │    └─ Returns hd_path or uhd_path depending on resolution
  │    │    ├─ Build destination path: <base_path>/<imdb_title> (<imdb_year>)/
  │    │    ├─ Handle multi-file torrents (largest file rename, exclude patterns)
  │    │    └─ Copy all non-excluded files; rename largest file to parent dir name.
  │    │        Mark verified='true' in DB ONLY after entire copy loop succeeds.
  │    │        (Partial copy → verified remains unset; next run will retry)
  │    └─ schedule_next_run() to recheck in 1 min
  └─ schedule_next_run() to recheck in N minutes
```

---

## 4. Configuration (`config.yml`)

### 4.1 Top-Level Sections

| Section | Purpose |
|---------|---------|
| `general` | Daemon mode, log levels, library paths |
| `schedule` | Task intervals (siphonator, queue, post-process) |
| `filters` | Rating, runtime, votes, genre overrides, country/language lists, bad keyword/title lists, preferred quality/group lists. **Note:** `minimum_seeders` is dead code (parsed but never enforced). |
| `torrent_client` | qBittorrent host/port/credentials, add_paused, category |
| `notification` | Email SMTP settings |
| `index_proxy` | Jackett host/port/API key, timeouts |
| `credentials` | TMDb + OMDb API keys |
| `index_site` | Search criteria per quality tier, ignore list, per-site category overrides |
| `queue_management` | Stalled/metadata timeout thresholds, grace periods. **Note:** contains dynamically-written keys `connection_down_datetime` and `internet_connection_down_datetime` (app mutates its own config file at runtime). |
| `post_process` | Copy/remove flags, genre/resolution/cert routing rules, exclude file/folder patterns |

### 4.2 Post-Process Routing Rules (New Feature)

```yaml
post_process:
  copy_library_rules:
    - name: "Paul"
      genres: ["Sci-Fi", "Action"]
      max_certification:        # null = no cert check
      hd_path: "/media/Movies/HD/Paul"
      uhd_path: "/media/Movies/UHD/Paul"
    - name: "Zack"
      genres: ["Animation", "Family"]
      max_certification: "12A"   # BBFC ceiling
      hd_path: "/media/Movies/HD/Zack"
      uhd_path: "/media/Movies/UHD/Zack"
  default_copy_library:
    hd_path: "/media/Movies/HD/Paul"
    uhd_path: "/media/Movies/UHD/Paul"
```

- **Scoring:** Each rule scores = count of overlapping genres. Highest unique score wins.
- **Tie / zero match:** Falls back to `default_copy_library`.
- **Certification:** `_BBFC_ORDER = ['U', 'PG', '12', '12A', '15', '18', 'R18']` (hardcoded, no config override). If `max_certification` is set on the winning rule and the movie's cert is NOT in `_BBFC_ORDER` → fallback to default. **Note:** OMDb fallback stores MPAA-style ratings (e.g. `PG-13`) which will NOT match BBFC ordering and will always fall to default.
- **Resolution:** 2160p → `uhd_path`, anything else → `hd_path`.
- **Backward compat:** Old `copy_library_path` key is detected and used as both hd/uhd path with a warning.

---

## 5. Database Schema (`history` table)

| Column | Type | Notes |
|--------|------|-------|
| `id` | int | PK, auto-increment |
| `index_title` | str | Raw indexer title |
| `result` | str | `Passed` / `Failed` |
| `result_details` | str | Human-readable chain of pass/fail reasons |
| `index_details` | str | Indexer comments URL |
| `index_pubdate` | str | RSS pubDate |
| `index_seeders` | str | Seeders count |
| `index_peers` | str | Peers count |
| `index_size` | str | Size in bytes |
| `index_size_mb` | str | Size in MB |
| `torrent_url` | str | .torrent URL |
| `torrent_tag` | str | UUID tag assigned per torrent |
| `magnet_url` | str | Magnet link |
| `category` | str | Jackett category |
| `verified` | str | `true` after post-processing copy |
| `imdb_id` | str | tt-number |
| `imdb_title` | str | Canonical title |
| `imdb_year` | str | Release year |
| `imdb_poster_url` | str | Poster image URL |
| `imdb_trailer_url` | str | YouTube trailer URL |
| `imdb_plot_summary` | str | Full plot |
| `imdb_plot_outline` | str | Short plot |
| `imdb_rating` | str | IMDb rating (e.g. "7.5") |
| `imdb_votes` | str | Vote count |
| `imdb_title_type` | str | `movie`, `video`, `tvmovie` |
| `imdb_running_time_in_minutes` | str | Runtime |
| `imdb_genres_list` | str | JSON/repr list of genres |
| `imdb_credits_director_list` | str | Directors |
| `imdb_credits_writer_list` | str | Writers |
| `imdb_credits_cast_list` | str | Cast |
| `imdb_credits_character_list` | str | Characters |
| `imdb_language_list` | str | Languages |
| `imdb_country_list` | str | Countries |
| `imdb_certification` | str | BBFC cert (added v7) |

### 5.1 Schema Evolution (Migrations)

| Version | Change |
|---------|--------|
| v1 → v2 | Added `imdb_country_origins_list` |
| v2 → v3 | Renamed `imdb_country_origins_list` → `imdb_country_list`, `imdb_spoken_languages_list` → `imdb_language_list` ⚠️ **Note:** `imdb_spoken_languages_list` was never in `create_tables()`, so this rename would fail on a true v1→v2 upgraded DB |
| v3 → v4 | Added `imdb_trailer_url` |
| v4 → v5 | Added `torrent_tag` |
| v5 → v6 | Added `verified` |
| v6 → v7 | Added `imdb_certification` |

### 5.2 Deduplication Logic

1. **Simple match:** `SELECT * FROM history WHERE index_title = '<title>'` — exact string match.
2. **Advanced match:** Sanitise title → regex-replace separators with `%` → `LIKE '%%<sanitised>%%'`. Catches minor formatting differences. The Python-side `in` check makes this effectively a **substring** match, not exact.

Both checks run **before** `index_name()` parsing (an optimisation to skip badly-formed titles early).

**Caveat:** `read_database_simple()` interpolates table/column names directly into SQL strings (commented as "subject to sqlite injection" in code).

---

## 6. Key Filtering Logic

### 6.1 Index-Level Filters (`filter_index_movies()`)

| Filter | Description |
|--------|-------------|
| Search criteria match | Criteria string is space-tokenized; **all tokens** must be present in the index title (e.g. "2160p remux" requires both "2160p" AND "remux") |
| Size min/max | `index_size_mb` must be within configured bounds per quality tier |
| Bad keywords | Rejects if index title contains any word from `bad_index_title_list` |
| Bad type | Rejects if title looks like TV (season/episode regex) |
| Bad movie title | Rejects if title matches `bad_movie_title_list` |
| Library duplicate | Two-phase walk: (1) file-level match by title+year in filenames, (2) directory-level match by title+year in dir names. Resolution comparison: index < library → Fail; index > library → Pass. If equal resolution, `filter_quality_check()` scores resolution (10–50) + source type (10–80) + audio (10–30) + preferred group bonus (+10) + special edition bonus (+10). Index score > library score → Pass. ffprobe fallback if filename lacks resolution. |

### 6.2 IMDb-Level Filters (`filter_imdb_movies()`)

| Filter | Description |
|--------|-------------|
| Good title type | Must be in `good_imdb_title_type_list` |
| Bad genre | Rejects if any genre in `bad_genre_list` |
| Bitrate | `size_mb / runtime` (actually **MB per minute**, not true Mbps) must exceed `minimum_bitrate_mb` |
| Minimum year | `imdb_year >= minimum_year` |
| Minimum runtime | `imdb_running_time_in_minutes >= minimum_runtime_mins` |
| Good language | At least one language in `good_language_list` |
| Good country | At least one country in `good_country_list` |
| Override chain | If movie matches any override (character/director/writer/cast/title/genre), lower rating/vote thresholds apply |
| Minimum rating | `imdb_rating >= minimum_rating` (genre-specific overrides possible) |
| Minimum votes | `imdb_votes >= minimum_votes` (genre-specific overrides possible) |

**Override cascade (critical distinction):**

| Override Type | Behaviour |
|---------------|-----------|
| **character → director → writer → cast → movie title** | **Hard short-circuit pass.** If ANY person/title override matches, rating and vote checks are **skipped entirely**. A Steven Spielberg movie with 2.0 rating gets accepted. |
| **genre** | **Relaxed thresholds only.** Lowers `minimum_rating` and `minimum_votes` thresholds but still requires the movie to pass them. |

The cascade order is: character → director → writer → cast → movie title → genre. If a person/title override matches, the movie passes immediately. Only if ALL person/title overrides fail does the genre override (and then standard rating/votes) apply.

---

## 7. External Dependencies

| Service/Library | Purpose |
|-----------------|---------|
| **Jackett** | Indexer proxy (Torznab API) |
| **qBittorrent** | Download client (WebUI API) |
| **IMDbPie** | IMDb metadata scraping |
| **TMDb API** | IMDb ID resolution |
| **OMDb API** | IMDb metadata + ID fallback |
| **Google Search** | Last-resort IMDb ID scrape |
| **APScheduler** | Background task scheduling |
| **daemonize** | Daemon mode |
| **qbittorrent-api** | qBittorrent WebUI client |
| **sqlite-utils** | SQLite ORM-ish interface |
| **nmdmail** | HTML email sending |
| **requests + backoff** | HTTP client with retry |
| **xmltodict** | XML parsing (uses hardcoded Torznab namespace URI as dict key) |
| **ffmpeg-python** | ffprobe wrapper for media inspection (only maps widths 1280→720, 1920→1080, 3840→2160) |

---

## 8. Known Issues / Technical Debt

### 8.1 Critical Bugs (will crash scheduler threads)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| 1 | `filter_imdb_good_type()` calls `.lower()` on `None` if IMDb title type is missing | `filter_movies.py:1183` | `AttributeError` kills acquisition task |
| 2 | `notification_email.py` calls `.join()` on `None` director/cast lists from OMDb fallback | `notification_email.py:29-31` | `TypeError` kills task; torrent never added |
| 3 | `http_client()` ignores Jackett `read_timeout` config — hardcoded 30s always used | `tools_downloader.py:82-85` | 60s Jackett timeout in config is dead |
| 4 | `index_proxy.py` offset increments by hardcoded 100 regardless of `limit` | `index_proxy.py:356` | 400-result overlap when limit=500 |

### 8.2 Design / Security Issues

| # | Issue | Location |
|---|-------|----------|
| 5 | All HTTPS calls disable certificate verification (`verify=False`) and suppress urllib3 warnings | `tools_downloader.py:15,97` |
| 6 | App mutates its own `config.yml` at runtime to persist downtime timestamps | `torrent_clients.py:32`, `config_manager.write_config()` |
| 7 | `read_database_simple()` interpolates table/column names into SQL (injection risk, though table/column are hardcoded) | `db_sqlite.py:132-144` |
| 8 | OMDb returns MPAA-style certs; stored in same DB column as BBFC certs from IMDbPie → post-processing routing inconsistency | `imdb_omdb.py`, `post_processing.py` |

### 8.3 Dead Code / Config Drift

| # | Issue | Location |
|---|-------|----------|
| 9 | `minimum_seeders` defined in config but never enforced | `config.yml:27`, `filter_movies.py` (absent) |
| 10 | `client_startup_grace_mins` / `qbittorrent_uptime()` broken and commented out | `queue_management.py:24-26`, `torrent_clients.py:243-259` |
| 11 | `config_manager.py` default values differ from example `config.yml` (e.g. `exclude_file_min_kb` 10 MB vs 1.5 GB) | `config_manager.py:131` vs `config.yml:315` |
| 12 | `filter_movies_new.py` is a WIP rewrite with different library-check timing (post-IMDb, using canonical title) | `filter_movies_new.py:110` |
| 13 | `config_validate.py` has Pydantic models but is not imported at runtime | `config_validate.py` |
| 14 | `result_dict` keys `index_title_full_compare`, `index_title_no_year`, `index_year_regex` are referenced but never populated | `search_google.py`, `search_all.py` |

### 8.4 Minor Issues

| # | Issue | Location |
|---|-------|----------|
| 15 | `search_google.py` has unmatched `)` in query string | `search_google.py:22` |
| 16 | `index_proxy.py` returns `1` on errors but `True` on success; caller doesn't check return value | `index_proxy.py:35-358` |
| 17 | `copy_files_dst()` calls `helper_get_largest_parent_dir()` twice redundantly | `post_processing.py:394,400` |
| 18 | `exclude_folder_regex_match` logs raw match object instead of pattern string | `post_processing.py:293` |
| 19 | Google search library has known timeout/hang issue | `search_google.py:21` (commented TODO) |
| 20 | IMDbPie frequently throws exceptions; OMDb fallback heavily relied upon | `imdb_imdbpie.py`, `imdb_omdb.py` |
| 21 | Mixed `dict['key']` and `dict.get('key')` access patterns throughout | Multiple files |
| 22 | Thread-local SQLite requires per-thread DB instances | `siphonator.py`, `index_proxy.py` |

1. **Thread-local SQLite:** DB connections must be created in the same thread they're used. The scheduler spawns threads, so each task creates its own `DbSqlite` instance.
2. **Config write race:** `queue_management` writes connection-down timestamps directly to `config.yml` via `config_manager.write_config()`.
3. **WIP file:** `filter_movies_new.py` is a large parallel rewrite of filtering logic that is **not imported** by the main app.
4. **Config validation:** `config_validate.py` has Pydantic models but is not enforced at runtime.
5. **Mixed dict access:** Code uses both `dict['key']` and `dict.get('key')` inconsistently; some KeyError crashes possible on malformed configs.
6. **Google search timeout:** Known issue with `googlesearch` library hanging (commented TODO in code).
7. **IMDbPie reliability:** Frequently throws exceptions; OMDb fallback is heavily relied upon.

---

## 9. Data Model Summary

```
result_dict (the universal carrier)
  ├─ index_title, index_size_mb, index_seeders, index_peers, index_pubdate, index_details
  ├─ index_title_sanitised, index_title_group, index_title_resolution, index_title_after_year_to_end
  ├─ index_title_compare
  ├─ movie_title, movie_title_year, movie_title_and_year_search
  ├─ movie_title_compare, movie_title_and_year_compare
  ├─ torrent_url, magnet_url, category
  ├─ imdb_id
  ├─ imdb_title, imdb_year, imdb_rating, imdb_votes
  ├─ imdb_title_type, imdb_running_time_in_minutes
  ├─ imdb_genres_list, imdb_credits_cast_list, imdb_credits_director_list,
  │   imdb_credits_writer_list, imdb_credits_character_list
  ├─ imdb_language_list, imdb_country_list
  ├─ imdb_certification
  ├─ imdb_poster_url, imdb_trailer_url, imdb_plot_summary, imdb_plot_outline
  ├─ result: "Passed" | "Failed"
  ├─ result_details: [str, str, ...]
  ├─ torrent_tag
  └─ verified
```

**Dead/unused keys (do not carry into rewrite):** `index_title_full_compare`, `index_title_no_year`, `index_year_regex` — referenced in `search_google.py` and `search_all.py` but never populated by `index_name()`.

Every pipeline stage mutates `result_dict` and appends to `result_details`. On failure at any stage, the dict is written to the DB and the torrent is skipped.

---

## 10. Constants, Defaults, and Hardcoded Values

| Value | Location | Description |
|-------|----------|-------------|
| Jackett offset increment | `index_proxy.py:356` | Hardcoded `100` per page (should equal `limit`) |
| Jackett defaults | `index_proxy.py:58-64` | `limit=500`, `offset=0`, `read_timeout=30.0` if missing from config |
| HTTP timeouts | `tools_downloader.py:82-85` | `connect_timeout=30.0`, `read_timeout=30.0` (Jackett config value is **ignored**) |
| Cast/crew caps | `imdb_imdbpie.py:52-88` | Hard-capped at 20 entries each |
| BBFC ordering | `post_processing.py:12` | `['U', 'PG', '12', '12A', '15', '18', 'R18']` — no config override |
| Resolution mapping | `tools_various.py:78-91` | Width 1280→720, 1920→1080, 3840→2160; other widths fall through to raw height |
| Copy file idempotency | `tools_various.py:105-162` | Pre-copy SHA256 check against existing dest; post-copy SHA256 verification |
| `verified` lifecycle | `post_processing.py`, `tools_various.py` | Checked before copy (skip if already 'true'); set only after full copy loop succeeds |

---

## 11. Config Key Reference (All Keys)

| Config Path | Type | Example | Used By | Notes |
|-------------|------|---------|---------|-------|
| `general.config_version` | str | `"1.0.0"` | — | Version tracking |
| `general.daemon_mode` | str | `"foreground"` | `siphonator.py` | `"foreground"` or `"background"` |
| `general.log_level_console` | str | `"info"` | `tools_logging.py` | |
| `general.log_level_file` | str | `"debug"` | `tools_logging.py` | |
| `general.library_path_list` | list[str] | `["/media/Movies"]` | `filter_movies.py`, `index_proxy.py` | Walked for dedup + quality scoring |
| `schedule.*.enabled` | bool | `true` | `siphonator.py` | Per-task enable switch |
| `schedule.*.schedule_time_units` | str | `"minutes"` | `siphonator.py` | |
| `schedule.*.schedule_time_mins` | int | `30` | `siphonator.py` | Interval |
| `filters.minimum_year` | int | `1970` | `filter_movies.py` | |
| `filters.minimum_runtime_mins` | int | `60` | `filter_movies.py` | |
| `filters.minimum_rating` | float | `7.0` | `filter_movies.py` | Base threshold |
| `filters.minimum_votes` | int | `5000` | `filter_movies.py` | Base threshold |
| `filters.minimum_seeders` | int | `1` | **Dead code** | Parsed but never enforced |
| `filters.override_genre.*` | dict | `{"animation": {"minimum_rating": 7.0}}` | `filter_movies.py` | Genre-specific rating/vote overrides |
| `filters.good_imdb_title_type_list` | list[str] | `["movie", "video"]` | `filter_movies.py` | |
| `filters.good_country_list` | list[str] | `["gb", "us"]` | `filter_movies.py` | 2-letter country codes |
| `filters.good_language_list` | list[str] | `["en"]` | `filter_movies.py` | 2-letter language codes |
| `filters.bad_index_title_list` | list[str] | `["extras", "cam"]` | `filter_movies.py` | Substring reject list |
| `filters.bad_genre_list` | list[str] | `["Music"]` | `filter_movies.py` | Exact genre reject list |
| `filters.bad_movie_title_list` | list[str] | `["12 monkeys"]` | `filter_movies.py` | Sanitised title reject list |
| `filters.override_cast_list` | list[str] | `["Tom Hanks"]` | `filter_movies.py` | Hard-pass if cast member present |
| `filters.override_writer_list` | list[str] | — | `filter_movies.py` | Hard-pass if writer present |
| `filters.override_director_list` | list[str] | `["Steven Spielberg"]` | `filter_movies.py` | Hard-pass if director present |
| `filters.override_movie_title_list` | list[str] | `["Star Trek"]` | `filter_movies.py` | Hard-pass if title contains |
| `filters.override_character_list` | list[str] | `["James Bond"]` | `filter_movies.py` | Hard-pass if character present |
| `filters.preferred_index_quality_list` | list[str] | `["remastered"]` | `filter_movies.py` | Quality bonus (+10 pts) |
| `filters.preferred_index_group_list` | list[str] | `["fgt"]` | `filter_movies.py` | Group bonus (+10 pts) |
| `torrent_client.selected` | str | `"qbittorrent"` | `siphonator.py` | |
| `torrent_client.qbittorrent.*` | dict | host, port, username, password | `torrent_clients.py` | |
| `notification.email.*` | dict | host, port, tls, ssl, user, pass, from, to | `notification_email.py` | SMTP settings |
| `index_proxy.selected` | str | `"jackett"` | `index_proxy.py` | |
| `index_proxy.jackett.*` | dict | host, port, api_key, read_timeout, limit, offset | `index_proxy.py` | `read_timeout` config value is **ignored** by HTTP client |
| `credentials.tmdb.api_key` | str | — | `search_tmdb.py` | |
| `credentials.omdb.api_key` | str | — | `search_omdb.py`, `imdb_omdb.py` | |
| `index_site.ignore_list` | list[str] | `["showrss"]` | `siphonator.py` | Skip these indexers |
| `index_site.search` | list[dict] | criteria, category, min/max size, bitrate | `siphonator.py`, `index_proxy.py` | Quality-tier search configs |
| `index_site.override_search` | dict | `{site: {category: "8000"}}` | `siphonator.py` | Per-indexer category override |
| `queue_management.queue_management_enabled` | bool | `true` | `queue_management.py` | |
| `queue_management.metadata_monitor_enabled` | bool | `true` | `queue_management.py` | |
| `queue_management.stalled_monitor_enabled` | bool | `true` | `queue_management.py` | |
| `queue_management.stalled_delete_torrent_data` | bool | `true` | `torrent_clients.py` | Delete data with stalled torrent? |
| `queue_management.stalled_delete_torrent_max_mins` | int | `120` | `queue_management.py` | |
| `queue_management.metadata_delete_torrent_max_mins` | int | `30` | `queue_management.py` | |
| `queue_management.connection_down_grace_mins` | int | `30` | `queue_management.py` | |
| `queue_management.connection_down_datetime` | str | `"2025-03-16 09:43:10"` | `torrent_clients.py` | **Dynamically written** at runtime |
| `queue_management.client_startup_grace_mins` | int | `30` | — | **Dead code** (broken feature) |
| `queue_management.internet_connection_down_datetime` | str | `"2025-12-26 22:14:38"` | `torrent_clients.py` | **Dynamically written** at runtime |
| `post_process.post_process_enabled` | bool | `true` | `post_processing.py` | |
| `post_process.copy_completed` | bool | `true` | `post_processing.py` | |
| `post_process.remove_completed` | bool | `true` | `post_processing.py` | Only applies to `stopped` torrents |
| `post_process.copy_library_rules` | list[dict] | name, genres, max_cert, hd/uhd path | `post_processing.py` | Genre routing rules |
| `post_process.default_copy_library` | dict | hd_path, uhd_path | `post_processing.py` | Fallback destination |
| `post_process.exclude_file_min_kb` | int | `1500000` | `post_processing.py` | Files below this size excluded from copy |
| `post_process.exclude_file_regex_list` | list[str] | `["sample"]` | `post_processing.py` | Filename exclusions |
| `post_process.exclude_folder_regex_list` | list[str] | `["subs"]` | `post_processing.py` | Folder exclusions |

---

*End of specification. Ready for Movarr design.*

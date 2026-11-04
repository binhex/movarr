# CRAP debt register (Step 2b scope decision, 2026-11-03)

**Status:** Accepted debt — approved by the user during tech-debt remediation.

## Why this exists

`pytest-crap` reports CRAP as `CC^2 * (1 - coverage)^3 + CC`, where *coverage* is
`covered statement lines / **all physical lines** in the function span`. Docstrings, blank lines
and multi-line call continuations are therefore counted as permanently uncovered.

Statement coverage of the suite is **100%**, and a per-function analysis confirmed that **none**
of the flagged functions can reach CRAP <= 9 by adding tests: at 100% coverage of every executable
line the score is unchanged (`best == current` for every row).

Removing the remaining debt therefore requires reducing radon **cyclomatic complexity**, not adding
tests. The `tech-debt` skill's Iron Rule forbids silent scope reduction, so the user was asked and
chose:

> Refactor only the functions whose complexity is genuinely high (CC >= 10); record the rest as
> named debt with their measured CRAP, CC and physical-line spans.

## Refactored under that decision (all now CC <= 9)

- `post_processor._process_one` (CC 17), `_delete_superseded_loop` (CC 18)
- `config._unknown_key_paths` (CC 12), `_run_migrations` (CC 12)
- `search._release_matches_metadata_titles` (CC 12), `_collect_supersede_matches` (CC 11),
  `_supersede` (CC 10), `_metadata_matches_release` (CC 10)
- `filters._build_title_variants` (CC 11), `_match_library_file` (CC 10), `_override_genre` (CC 10)
- `imdb_metadata._build_imdbpie_payload` (CC 11)
- `prowlarr._parse_result` (CC 11)
- `service_health._on_unhealthy` (CC 11)

## Remaining debt (recorded, not fixed)

45 functions remain at CRAP >= 9 with CC <= 9. Max CRAP is 41.57. Each row is
stable: the score cannot fall below 9 at 100% executable coverage, so the only lever is further
complexity reduction of already-simple functions.

| CRAP | CC | Reported coverage | Physical lines | Executable lines | Function | File |
| --- | --- | --- | --- | --- | --- | --- |
| 41.57 | 9 | 26.2% | 42 | 11 | `_maybe_delete_superseded` | `post_processor.py)` |
| 38.47 | 8 | 21.9% | 73 | 16 | `_delete_superseded_files` | `post_processor.py)` |
| 26.43 | 8 | 34.0% | 53 | 18 | `_copy_phase` | `post_processor.py)` |
| 23.93 | 7 | 29.8% | 57 | 17 | `add_torrent` | `qbittorrent.py)` |
| 23.42 | 8 | 37.8% | 45 | 17 | `_supersede` | `search.py)` |
| 21.53 | 6 | 24.4% | 90 | 22 | `cli` | `cli.py)` |
| 21.29 | 9 | 46.7% | 15 | 7 | `_title_words` | `search.py)` |
| 21.03 | 8 | 41.2% | 34 | 14 | `_copy_files` | `post_processor.py)` |
| 21.03 | 8 | 41.2% | 17 | 7 | `_release_matches_metadata_titles` | `search.py)` |
| 20.67 | 6 | 25.9% | 58 | 15 | `create_logger` | `logger.py)` |
| 19.24 | 8 | 44.0% | 25 | 11 | `_parse_result` | `prowlarr.py)` |
| 19.12 | 9 | 50.0% | 42 | 21 | `_build_copy_target` | `post_processor.py)` |
| 19.12 | 9 | 50.0% | 22 | 11 | `_metadata_matches_release` | `search.py)` |
| 18.51 | 8 | 45.2% | 42 | 19 | `_migrate_v217_to_v218` | `config.py)` |
| 18.39 | 8 | 45.5% | 22 | 10 | `_same_title` | `post_processor.py)` |
| 18.18 | 7 | 38.9% | 54 | 21 | `_parse_omdb_canonical` | `imdb_metadata.py)` |
| 18.17 | 8 | 45.8% | 24 | 11 | `_process_one_no_copy` | `post_processor.py)` |
| 18.02 | 7 | 39.2% | 74 | 29 | `run_search` | `search.py)` |
| 17.89 | 8 | 46.3% | 41 | 19 | `_dispatch_apprise` | `notifications.py)` |
| 17.86 | 7 | 39.5% | 38 | 15 | `_delete_superseded_loop` | `post_processor.py)` |
| 17.84 | 8 | 46.4% | 28 | 13 | `_check_reject_genre_exclusive` | `filters.py)` |
| 17.81 | 6 | 31.0% | 58 | 18 | `run_queue_management` | `queue_manager.py)` |
| 17.77 | 7 | 39.7% | 58 | 23 | `_evaluate_single_library_file` | `filters.py)` |
| 17.30 | 7 | 40.5% | 37 | 15 | `_process_criteria` | `search.py)` |
| 17.20 | 7 | 40.7% | 27 | 11 | `normalise_imdb_id` | `parsing.py)` |
| 17.04 | 6 | 32.6% | 43 | 14 | `filter_by_imdb` | `filters.py)` |
| 17.00 | 8 | 48.0% | 25 | 12 | `_strip_path_basename` | `config.py)` |
| 16.91 | 7 | 41.3% | 46 | 19 | `load_config` | `config.py)` |
| 16.61 | 9 | 54.5% | 44 | 24 | `_enrich_result` | `search.py)` |
| 16.14 | 7 | 42.9% | 42 | 18 | `search` | `prowlarr.py)` |
| 16.14 | 7 | 42.9% | 42 | 18 | `_parse_item` | `jackett.py)` |
| 16.00 | 8 | 50.0% | 12 | 6 | `_title_key` | `post_processor.py)` |
| 15.90 | 9 | 56.0% | 25 | 14 | `_validate_omdb_title` | `imdb_search.py)` |
| 15.43 | 8 | 51.2% | 41 | 21 | `_delete_stuck` | `queue_manager.py)` |
| 15.20 | 8 | 51.7% | 29 | 15 | `_find_canonical_matches` | `filters.py)` |
| 14.90 | 5 | 26.6% | 64 | 17 | `_run_hook` | `post_processor.py)` |
| 14.79 | 7 | 45.8% | 24 | 11 | `_resolve_destination` | `post_processor.py)` |
| 14.79 | 7 | 45.8% | 24 | 11 | `_match_by_filename_or_folder` | `filters.py)` |
| 14.48 | 9 | 59.3% | 27 | 16 | `_title_remainder` | `post_processor.py)` |
| 14.42 | 4 | 13.3% | 30 | 4 | `_apply_metadata` | `imdb_metadata.py)` |
| 14.39 | 6 | 38.5% | 52 | 20 | `search` | `jackett.py)` |
| 14.28 | 5 | 28.1% | 32 | 9 | `_post_copy_actions` | `post_processor.py)` |
| 14.14 | 7 | 47.4% | 19 | 9 | `_library_resolution_is_higher` | `post_processor.py)` |
| 14.11 | 5 | 28.6% | 28 | 8 | `_try_folder_fallback` | `filters.py)` |
| 14.09 | 8 | 54.3% | 46 | 25 | `_collect_supersede_matches` | `search.py)` |

> Note: this table is computed with the same formula from radon `cc_visit` + the coverage JSON.
> It is a conservative superset of the 40 rows the plugin prints (the plugin only reports
> functions whose radon node it can match by start line).

## Re-check

```bash
uv run --with pytest-crap pytest --cov --crap --crap-threshold=9
```

Any function that re-enters at **CC >= 10** should be refactored again; the rows above are the
accepted set.

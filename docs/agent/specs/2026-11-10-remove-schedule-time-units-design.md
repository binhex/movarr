# Remove the obsolete `schedule_time_units` key — design

**Status:** Approved (2026-11-10)
**Scope:** configuration migration only. No scheduler or CLI behaviour changes.

## Problem

A movarr config can carry `schedule_time_units: minutes` inside `schedule.acquisition`,
`schedule.queue_management` and `schedule.post_processing`. The key is a siphonator leftover (it is documented in
the legacy `docs/specs/SIPHONATOR_SPEC.md`), and movarr has never read it: it is absent from `ScheduleTaskConfig`,
`PostProcessingScheduleConfig`, `ScheduleConfig`, the README key tables, and every migration.

Because the key is not in the schema, `_unknown_key_paths` reports it and `load_config` logs a warning on every
start:

```text
Unknown config keys (will be ignored): schedule.acquisition.schedule_time_units,
schedule.post_processing.schedule_time_units, schedule.queue_management.schedule_time_units
```

The value already says "minutes" in the key it accompanies (`schedule_time_mins`), so the extra key adds nothing,
and the permanent warning trains operators to ignore the unknown-key diagnostic that exists to catch real typos.

## Goals

- Remove `schedule_time_units` from the three task blocks during config migration, so the dead key and its warning
  are gone after one upgrade.
- Keep the change invisible to runtime behaviour: no model field, no scheduler change, no CLI change.
- Leave every other key — including adjacent ones in the same block — untouched.

## Non-goals

- Renaming, re-uniting or reinterpreting `schedule_time_mins`.
- Adding real unit support (hours/seconds) anywhere.
- Removing other unknown or legacy keys.
- Any change to `scheduler.py`, `cron_schedule.py`, or the CLI.

## Current state

- `_CONFIG_VERSION = "2.25.0"`; migrations are a sequential `MIGRATIONS` dict keyed by version, each rewriting the
  file after a single `movarr.yml.bak.<version>` backup.
- `ScheduleTaskConfig` fields: `enabled`, `schedule_time_mins`, `run_on_start`.
- `PostProcessingScheduleConfig` fields: `enabled`, `run_on_start`, `cron`.
- `schedule_time_units` appears nowhere in `src/` or the README, so the only effect of its presence is the
  unknown-key warning.

## Design

### Migration

`_migrate_v225_to_v226(raw)` is registered as `MIGRATIONS["2.25.0"]`, and `_CONFIG_VERSION` becomes `2.26.0`.

For each block name in `("acquisition", "queue_management", "post_processing")`:

1. Read `schedule.get(name)` and use it only when it is a `dict` (never `_section`, which would materialise an
   empty block in a minimal config).
2. `pop("schedule_time_units", None)`; record the block name when a key was actually removed.
3. When at least one key was removed, log one INFO line naming the affected blocks:
   `Removed obsolete schedule_time_units from: acquisition, queue_management, post_processing.`

If `raw.get("schedule")` is missing or is not a mapping, the migration removes nothing and only bumps the
version; the same holds for a block that is absent, `null` or a scalar.

The value is removed unconditionally, whatever it says: movarr never interpreted the key, so `hours` or any other
value carries no behaviour to preserve and no conversion to apply. The version bump happens regardless, so the
migration is idempotent through the existing version guard and a config without the key is a no-op apart from the
version line.

Removal runs after the 2.24.0 -> 2.25.0 cron migration in the same load, because the chain applies steps
sequentially until `_CONFIG_VERSION` is reached. A file still at 2.24.0 therefore gets both the cron conversion and
this cleanup in one pass.

### Compatibility

No model, schema or runtime change: the key was unknown before and is absent afterwards. Pydantic still ignores
unknown keys, so a hand-edited file that reintroduces it keeps working; it simply warns again until the version is
bumped or the key removed.

### Documentation

One sentence is appended to the existing config-migration paragraph at the end of the README's `schedule`
section (the paragraph that already describes the 2.24.0 -> 2.25.0 interval-to-cron conversion): upgrading removes
the obsolete `schedule_time_units` key from the three task blocks. No key table entry is added, because the key
was never a movarr setting.

## Files

| File | Change |
| --- | --- |
| `src/movarr/config.py` | `_CONFIG_VERSION`, `_migrate_v225_to_v226`, `MIGRATIONS["2.25.0"]` |
| `tests/unit/test_config.py` | migration tests (see below) |
| `README.md` | one migration sentence |

## Tests

- Key present in all three blocks at 2.25.0: after `load_config`, each block lacks the key on disk and the config
  exposes `config_version == "2.26.0"`.
- Exactly one INFO line is logged, naming the blocks that were cleaned.
- Value other than `minutes` (e.g. `hours`) is removed just the same, without conversion.
- Key absent: no INFO line, and no block is created or altered apart from the version bump.
- `schedule` missing or non-mapping: nothing is removed, the version still bumps, and no INFO line is logged.
- Neighbouring keys survive: `enabled`, `run_on_start`, `schedule_time_mins` and `cron` keep their values, and an
  unrelated section is untouched.
- Backup `movarr.yml.bak.2.25.0` is written.
- A second load is idempotent.
- `load_config` emits no unknown-key warning mentioning `schedule_time_units` after migration.

## Risks

| Risk | Mitigation |
| --- | --- |
| Migration creates empty blocks in a minimal config | Read with `.get()` + `isinstance(dict)`; never `_section` |
| Operator intent stored in a non-`minutes` value is lost | The key was never read by movarr, so there is no behaviour to lose; the INFO line records the removal |
| A newer key with the same name is added later | A future schema change adds the field back deliberately; the migration only touches the three current blocks |
| Warning-noise regression hides real typos | The removal deletes the key from disk, so `_unknown_key_paths` no longer reports it |

## Acceptance

- A 2.25.0 config with `schedule_time_units: minutes` under all three task blocks loads cleanly, is rewritten at
  `config_version: 2.26.0`, and no longer contains the key in any block.
- Exactly one INFO line names the cleaned blocks; no unknown-key warning mentions `schedule_time_units`.
- A config without the key is unchanged apart from the version bump, and a second load is a no-op.

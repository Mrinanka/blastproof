# Fast local tests, full suite in CI (D-179)

**Status: IMPLEMENTED 2026-10-26** (commit/PR: fill in when merged). Decision text: `docs/decisions.md` D-179.

## The problem
The full backend suite is 1,975 tests and took 554 s (9 min 14 s) on 2026-10-26. That blocked
development locally and made CI slow.

| Group | Tests | Time |
|---|---|---|
| Unit (`-m "not integration"`) | 1,552 | 42 s |
| Integration (need Docker Postgres) | 423 | about 510 s |

Almost all of the integration time is the shadow/preview tests (`test_shadow_*`,
`test_impact_done_when`, `test_row_hash`, `test_seed_counts`): each clones the 273k-row
`company_main` and hashes/diffs it. Then `test_demo_scripts` (reseed), `test_migrations`,
`test_cli_*`.

## The decision
- **Locally**, `uv run pytest` runs only the fast, Docker-free tests (the unit set).
- **In CI**, `uv run pytest --integration` runs everything. All 1,975 tests still run before merge.
- Running the full suite locally stays possible and is opt-in: `uv run pytest --integration`.
- CI is split into a fast `lint + unit` job and a parallel `integration` job.

Nothing was deleted or weakened. Tests only moved from "every local run" to "CI and on request".

## Why a flag, not `addopts = "-m 'not integration'"`
`backend/tests/test_ci_workflow.py` guards that CI runs plain `uv run pytest` with no `-m` filter,
so CI cannot quietly drop the integration tests. `addopts` would apply to CI too. An explicit
`--integration` flag in the CI step, which the guard test requires, keeps that protection.

## Where the change lives
- `backend/tests/conftest.py`: `--integration` option and `pytest_collection_modifyitems`.
- `.github/workflows/ci.yml`: test step becomes `uv run pytest --integration` (and the job split).
- `backend/tests/test_ci_workflow.py`: guard requires `--integration`, still forbids `-m`.
- `CLAUDE.md` Commands and Testing lines.

## How to reverse it
1. In `conftest.py`, delete the `--integration` option and the `pytest_collection_modifyitems` hook.
2. In `ci.yml`, put the test step back to `uv run pytest` (and merge the jobs if they were split).
3. In `test_ci_workflow.py`, drop the `--integration` requirement.
4. Restore the `CLAUDE.md` lines. Or simply `git revert` the commit that implemented D-179.

## Ideas not done yet (revisit if CI is still slow)
- Migrate one scratch DB per session and clone it with `TEMPLATE` instead of running Alembic per module.
- Measure each shadow scenario once per session and share the result across test files.
- `pytest-xdist` for the integration job (new dependency, ask first; tests that assert global
  state such as leftover shadow databases need a serial marker).

## Update 2026-10-27: a `slow` tier (D-190)
Integration alone was still about 9 minutes. `uv run pytest --integration --skip-slow` runs everything except 12 seed-heavy files (94 tests): 2,078 tests in 3 min 40 s. Use it while developing. The full suite (`--integration` without `--skip-slow`, all 2,173) runs only in CI and is never run on a development machine (D-191). If a change touches a slow file's area, run that one file by name. Add a file to `SLOW_FILES` in `tests/conftest.py` when its tests take over 10 s each on the seed. `--skip-slow` is never allowed in `ci.yml` (guarded by `tests/test_slow_tier.py`).


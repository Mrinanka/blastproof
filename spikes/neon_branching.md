# Spike: Neon branching (2026-10-25)

Throwaway code: `spikes/neon_branching.py` (no `backend/app` imports; dev project only, it refuses
unless the project is named `blastproof-company-dev`, and deletes only `spike-*` branches it made).
Versions: `httpx` 0.28.1, `asyncpg` 0.32.0, Python 3.13, Neon REST API v2, Postgres 18.6 on the branch.

Run from the repo root:
- `uv run --project backend python spikes/neon_branching.py probe` (about 1 min)
- `... latency --runs 20` (about 3 min)
- `... cleanup` (deletes leftover `spike-*` branches; found 0 afterwards)

Docs: https://neon.com/docs/manage/branches, https://api-docs.neon.tech/reference/createprojectbranch,
https://neon.com/docs/manage/operations, https://neon.com/docs/guides/branch-expiration,
https://neon.com/docs/connect/connection-pooling, https://neon.com/docs/reference/api/key-concepts

## Answers

| # | Question | Answer (from the output below) |
| --- | --- | --- |
| 1 | Postgres version, region | Project `pg_version` 18, `aws-us-east-2`; `server_version` on a branch is 18.6. D-057 holds for the dev project. |
| 2 | Create payload | `POST /projects/{id}/branches` with `{"branch": {"parent_id", "name"}, "endpoints": [{"type": "read_write"}]}` returns 201 in about 0.4 s (first call 1.1 s). Top-level keys: `branch, databases, endpoints, operations, roles`. The endpoint has `host` and `current_state: init`; autoscaling 0.25 to 2 CU; `suspend_timeout_seconds` 0 (the project default). |
| 3 | Waiting on operations | Four operations at 201 (`create_branch`, `timeline_update_protected_config`, `start_compute`, `epc_sync`). Polling `GET /operations/{id}` every 0.5 s, all reach `finished` in about 1.2 s once polled. Connecting without waiting also works: the first `SELECT 1` succeeded 2 s after the POST returned, at the first attempt, and every operation was already `finished` by then. Waiting on operations is still the documented signal; the connect itself needs about 2 s. |
| 4 | Non-owner connection | `connection_uris` is **absent** (the parent has several roles). Our own DSN works: the `blastproof_writer` user, password and database from `COMPANY_DB_WRITER_URL` with the branch's `endpoint.host` swapped in. `current_user` is `blastproof_writer`, TEMPORARY is granted, `CREATE TEMP TABLE` works. |
| 5 | Data and rights | `users` has 50,000 rows on the branch. `blastproof_reader` INSERT is refused (`permission denied for table feature_flags`). |
| 6 | Pooled vs direct | **Inconclusive for pooled, so use direct.** On a single client the pooled host (`-pooler`) accepted 20 parameterised fetches, kept `SET TimeZone` and kept a temp table across statements (the same backend was probably reused because nothing else was using it), and accepted `server_settings`. The endpoint reports `pooler_enabled: False`, so this did not exercise PgBouncer's transaction mode. The plan's direct endpoint stays: the shadow run needs session state and a multi-statement transaction. |
| 7 | `expires_at` | **Accepted on create** (201, read back identical, not Early Access only). A past value gives 400 `BRANCH_EXPIRATION_VALIDATION_FAILED` ("expiration time cannot be in the past"). Auto-deletion timing was not measured (needs a wait of 15+ minutes). |
| 8 | Delete and leftovers | `DELETE` returns 200 in 0.4 to 0.9 s with operations `suspend_compute`, `delete_timeline`, both `finished` about 2.5 s after the call. A second `DELETE` returns **204 with an empty body** (not 404), so a delete is idempotent. `GET` of a deleted branch is 404 `{"code": "", "message": "branch not found"}`. `list_branches` returns `name`, `created_at`, `default`, `current_state` (enough for "older than 15 minutes" cleanup). |
| 9 | Concurrency and caps | Two simultaneous creates both returned 201 with no 423, both endpoints started. The branch cap is **10** (the default branch counts): the tenth succeeded and the eleventh returned 422 `BRANCHES_LIMIT_EXCEEDED` ("branches limit exceeded"). Free plan: leftover branches can exhaust the cap and make every write fail closed. |
| 10 | Latency | 20 sequential runs: create-to-first-query (t3) **p50 4.99 s, p95 5.16 s, max 5.34 s**. Parts: POST returned p50 0.42 s; operations finished p50 2.98 s; the first connect and query add about 2 s after that; delete call p50 0.37 s. Shadow-shaped workload on 5 runs: hashing all 11 tables 3.3 s, delete plus diff plus rollback 5.7 s, so a whole preview is about **14.3 s p50 (max 14.5 s)**. All from this dev machine. |
| 11 | Failure shapes | Every Neon error is `{"request_id", "code", "message"}`: wrong key 401 `supplied credentials do not pass authentication`; bad `parent_id` 404 `parent branch not found`; unknown project 404 `project not found`; cap 422 `BRANCHES_LIMIT_EXCEEDED`. `code` is empty for most 4xx, so match on status plus message. Connecting to a deleted branch's host fails with asyncpg `InvalidPasswordError`, not a connection error. |

## What differs from the plan, and proposed edits

1. **Connection string (backend plan step 10):** "returns its direct connection string" cannot come from
   the API. Build it from `endpoints[0].host` plus the writer user, password and database of
   `COMPANY_DB_WRITER_URL`.
2. **Waiting (step 10):** poll the operations from the create response (`finished` or `skipped` is
   good; `failed`, `error`, `cancelled` is bad). The first connection still takes about 2 s, so `NeonApiClient`
   should retry the connect for a short while rather than treat the first failure as final.
3. **Cleanup (step 10, BE-58):** `expires_at` works on create, so set it (now + 15 min) on every shadow
   branch as a backstop, and keep the plan's startup cleanup (deletion delay unmeasured, and a branch
   counts against the cap until it is gone). Treat a 204 on delete as success.
4. **Cap (step 10, BE-47):** map a 422 `BRANCHES_LIMIT_EXCEEDED` on create to the `busy` or `failed`
   outcome. `MAX_CONCURRENT_SHADOWS=2` is safe on the cap if cleanup works (10 minus main leaves 9).
5. **Latency target (SPEC §10, NFR-03):** measured p50 5.0 s, p95 5.2 s to a usable branch; a whole
   preview about 14 s on a 273k-row database from the dev machine. A target of "a previewed write is
   ready for review in under 20 s" fits; "under 5 s" does not. Options if this is too slow:
   speed up the hashing (it is the largest part), or a warm pre-created branch (not in the MVP).
6. **Pooled endpoint (step 10, Known issues):** close the open item with "direct only, pooled not
   needed". The test did not prove that PgBouncer breaks the session state, so say so rather than
   claim it.
7. **`/ready` (BE-23):** a cheap Neon check is `GET /projects/{id}` (one call, 200 with the project).

## Not tested

- `expires_at` auto-deletion timing; 429 rate limits (700 requests a minute, far above this use);
  the pooled endpoint under concurrent clients (it reports `pooler_enabled: False`); the 100 CU-hour
  monthly compute budget (this spike used about 0.25 CU for under 5 minutes per run, so roughly 0.2 CU-hours in total).

## Real output

### probe
```text
Project blastproof-company-dev: 1 existing branches before the probe
  existing branch br-morning-heart-b5c59iwi name=production default=True current_state=ready
[Q1] INFO project pg_version=18 region_id=aws-us-east-2 plan keys: ['compute_time_seconds', 'compute_last_active_at']
[Q2] PASS POST create-branch -> 201 in 0.43s
  response top-level keys: ['branch', 'databases', 'endpoints', 'operations', 'roles']
  branch keys: ['active_time_seconds', 'compute_time_seconds', 'cpu_used_sec', 'created_at', 'creation_source', 'current_state', 'data_transfer_bytes', 'default', 'id', 'init_source', 'name', 'parent_id', 'parent_lsn', 'pending_state', 'primary', 'project_id', 'project_slug', 'protected', 'slug', 'state_changed_at', 'updated_at', 'written_data_bytes']
  branch.current_state=init, parent_id matches=True
  endpoint keys: ['autoscaling_limit_max_cu', 'autoscaling_limit_min_cu', 'branch_id', 'branch_slug', 'created_at', 'creation_source', 'current_state', 'disabled', 'group', 'host', 'hosts', 'id', 'passwordless_access', 'pending_state', 'pooler_enabled', 'pooler_mode', 'project_id', 'project_slug', 'provisioner', 'proxy_host', 'region_id', 'settings', 'slug', 'suspend_timeout_seconds', 'type', 'updated_at']
  endpoint type=read_write current_state=init pooler_enabled=False autoscaling min/max=0.25/2 suspend_timeout_seconds=0
  operations at 201: [('create_branch', 'running'), ('timeline_update_protected_config', 'scheduling'), ('start_compute', 'scheduling'), ('epc_sync', 'scheduling')]
[Q4] INFO connection_uris present=False (parent has several roles/databases)
[Q3] INFO first SELECT 1 as writer worked 2.01s after POST returned (attempts=1); operation statuses at that moment: ['finished', 'finished', 'finished', 'finished']
[Q3] PASS operations to terminal: {'ops': {'3af3d330-0e02-49d2-9c44-22355447092d': {'action': 'create_branch', 'transitions': ['running', 'finished'], 'finished_after_s': 0.29}, '8a7186f8-b45e-4647-9592-a6b6088fba15': {'action': 'timeline_update_protected_config', 'transitions': ['scheduling', 'finished'], 'finished_after_s': 0.58}, 'ab4fdb20-9b23-4ad1-b2bd-4f91f3e38cfc': {'action': 'start_compute', 'transitions': ['scheduling', 'finished'], 'finished_after_s': 0.87}, '50bfe1ee-dd74-4c7e-b882-f335b295da5e': {'action': 'epc_sync', 'transitions': ['scheduling', 'finished'], 'finished_after_s': 1.16}}, 'ok': True, 'total_s': 1.16}
  current_user=blastproof_writer database=neondb server_version=18.6 (fd34222) TimeZone=GMT TEMPORARY privilege=True
[Q1] INFO branch SHOW server_version=18.6 (fd34222)
[Q4] PASS writer on the branch (direct endpoint, our own DSN) can CREATE TEMP TABLE
[Q5] PASS branch has users count=50000 (main: 50,000)
[Q5] PASS blastproof_reader INSERT refused on the branch: permission denied for table feature_flags
[Q6] PASS direct: 20 parameterised fetches, 0 failed 
[Q6] PASS direct: SET TimeZone kept for 10/10 later statements
[Q6] PASS direct: temp table visible in 10/10 later statements
[Q6] PASS pooled: 20 parameterised fetches, 0 failed 
[Q6] PASS pooled: SET TimeZone kept for 10/10 later statements
[Q6] PASS pooled: temp table visible in 10/10 later statements
[Q6] INFO pooled: connecting with server_settings (TimeZone, extra_float_digits) worked
[Q8] PASS DELETE -> 200 in 0.94s, operations [('suspend_compute', 'running'), ('delete_timeline', 'scheduling')]
  delete operations: {'ops': {'40e2f5c4-24de-4321-a6cc-c181d14c2bf2': {'action': 'suspend_compute', 'transitions': ['running', 'finished'], 'finished_after_s': 1.41}, 'e8ea53ba-0159-4fe6-8f3d-991afb307000': {'action': 'delete_timeline', 'transitions': ['scheduling', 'running', 'finished'], 'finished_after_s': 2.52}}, 'ok': True, 'total_s': 2.52}
[Q8] INFO second DELETE -> 204 non-JSON body ''
[Q8] INFO GET deleted branch -> 404 keys=['code', 'message', 'request_id'] body={'request_id': '775b67dc-bfb1-4041-acf0-a34db8858e6c', 'code': '', 'message': 'branch not found'}
  list_branches fields: ['active_time_seconds', 'compute_time_seconds', 'cpu_used_sec', 'created_at', 'created_by', 'creation_source', 'current_state', 'data_transfer_bytes', 'default', 'id', 'init_source', 'logical_size', 'name', 'primary', 'project_id', 'project_slug', 'protected', 'slug', 'state_changed_at', 'updated_at', 'written_data_bytes']
[Q8] PASS deleted branch gone from the listing: True
[Q11] INFO connect to a deleted branch's host -> InvalidPasswordError: password authentication failed for user 'blastproof_writer'
[Q7] PASS expires_at accepted on create (201); sent 2026-10-25T15:23:46Z, read back '2026-10-25T15:23:46Z'
  GET branch expires_at='2026-10-25T15:23:46Z'
[Q7] INFO expires_at in the past -> 400 keys=['code', 'message', 'request_id'] body={'request_id': '7c5ddb7c-c630-41bf-a256-36ae76813fe6', 'code': 'BRANCH_EXPIRATION_VALIDATION_FAILED', 'message': 'expiration time cannot be in the past'}
[Q7] INFO auto-deletion timing not measured (needs a 15+ minute wait)
[Q9] INFO two simultaneous creates without retry -> 201, 201
  endpoints created by those two: [('read_write', 'init'), ('read_write', 'init')]
  branches before the cap test: 1
[Q9] INFO branch cap hit after creating 9 more (total 10): 422 keys=['code', 'message', 'request_id'] body={'request_id': 'f0562e6e-e20f-436b-9097-25b6771b8219', 'code': 'BRANCHES_LIMIT_EXCEEDED', 'message': 'branches limit exceeded'}
[Q11] INFO wrong API key -> 401 keys=['code', 'message', 'request_id'] body={'request_id': '1d51f486-b6da-494f-8144-7db4f714cced', 'code': '', 'message': 'supplied credentials do not pass authentication'}
[Q11] INFO bad parent_id -> 404 keys=['code', 'message', 'request_id'] body={'request_id': 'dc6b3054-62f5-4435-bf41-f432f660e7b5', 'code': '', 'message': 'parent branch not found'}
[Q11] INFO unknown project -> 404 keys=['code', 'message', 'request_id'] body={'request_id': 'ef895a8d-6d77-4cb3-bed9-3b15ec23a52b', 'code': '', 'message': 'project not found'}
Leftover spike-* branches after the probe: []
```

### latency
```text
Latency: 20 sequential runs; t0=POST sent, t1=201, t2=ops finished, t3=first SELECT 1 as writer, t4=delete returned (seconds)
run  1: t1=1.09 t2=2.25 t3=4.20 delete=0.42 ops_ok=True hash=3.31 delete+diff=5.62
run  2: t1=0.40 t2=2.97 t3=4.98 delete=0.93 ops_ok=True hash=3.36 delete+diff=5.64
run  3: t1=0.42 t2=3.18 t3=5.14 delete=0.39 ops_ok=True hash=3.29 delete+diff=5.84
run  4: t1=0.44 t2=3.17 t3=5.09 delete=0.90 ops_ok=True hash=3.26 delete+diff=6.03
run  5: t1=1.18 t2=3.31 t3=5.34 delete=0.41 ops_ok=True hash=3.43 delete+diff=5.68
run  6: t1=0.50 t2=3.15 t3=5.15 delete=0.38 ops_ok=True
run  7: t1=0.43 t2=2.47 t3=4.47 delete=0.34 ops_ok=True
run  8: t1=0.47 t2=2.23 t3=4.17 delete=0.36 ops_ok=True
run  9: t1=0.44 t2=3.07 t3=4.99 delete=0.36 ops_ok=True
run 10: t1=0.41 t2=2.45 t3=4.43 delete=0.65 ops_ok=True
run 11: t1=0.40 t2=3.07 t3=5.07 delete=0.35 ops_ok=True
run 12: t1=0.42 t2=2.46 t3=4.42 delete=0.34 ops_ok=True
run 13: t1=0.42 t2=2.48 t3=4.46 delete=0.38 ops_ok=True
run 14: t1=0.42 t2=3.12 t3=5.13 delete=0.45 ops_ok=True
run 15: t1=0.40 t2=2.99 t3=5.01 delete=0.38 ops_ok=True
run 16: t1=0.42 t2=2.15 t3=4.07 delete=0.33 ops_ok=True
run 17: t1=0.47 t2=3.05 t3=5.04 delete=0.36 ops_ok=True
run 18: t1=0.44 t2=3.03 t3=5.04 delete=0.32 ops_ok=True
run 19: t1=0.40 t2=2.39 t3=4.40 delete=0.35 ops_ok=True
run 20: t1=0.41 t2=2.43 t3=4.47 delete=0.33 ops_ok=True

t1 POST returned             n=20 p50=0.42 p95=1.09 max=1.18
t2 operations finished       n=20 p50=2.98 p95=3.19 max=3.31
t3 first query (HEADLINE)    n=20 p50=4.99 p95=5.16 max=5.34
delete call                  n=20 p50=0.37 p95=0.91 max=0.93
workload: hash 11 tables     n=5 p50=3.31 max=3.43
workload: delete+diff+rollback n=5 p50=5.68 max=6.03
preview total (t3+workload)  n=5 p50=14.26 max=14.45
(timings are from this dev machine to aws-us-east-2; Fly in the same region has shorter network legs)
Leftover spike-* branches after the latency run: []
```

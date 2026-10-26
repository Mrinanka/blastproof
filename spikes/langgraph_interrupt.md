# Spike: LangGraph interrupt and resume (SPIKE-04), 2026-10-26

Throwaway code: `spikes/langgraph_interrupt.py` (`probe`, `migrate`, `cleanup`). Local Docker Postgres only
(scratch database `bp_spike_<hex>`, dropped afterwards; `cleanup` finds none left). No `backend/app` change,
no new dependency, no network, `backend/.env` never read.

Versions: `langgraph` 1.2.17, `langgraph-checkpoint-postgres` 3.2.0, `langgraph-checkpoint` 4.3.0,
`psycopg` 3.3.6, `psycopg-pool` 3.3.3, Alembic 1.20.1, SQLAlchemy 2.1.6, Postgres 18.

Docs: LangGraph interrupts, checkpointers and persistence pages (docs.langchain.com/oss/python/langgraph/),
the `langgraph-checkpoint-postgres` PyPI README, the psycopg pool docs, Alembic `autocommit_block`, plus the
installed source (`checkpoint/postgres/{base,aio,__init__}.py`).

## Answers

| Q | Answer (from the output below) |
|---|---|
| Q1 connection | `from_conn_string(plain postgresql:// URL)` works as `blastproof_app`. **Without `autocommit=True` the graph runs without error but nothing is stored** (0 checkpoint rows): a silent loss, so a test must assert a checkpoint row exists. Missing `dict_row` and missing `prepare_threshold=0` both still worked on a direct connection (the saver passes `dict_row` to its own cursors); keep all three anyway (`prepare_threshold=0` matters behind a pooler such as Neon's pooled endpoint). 5 parallel actions work on one single connection and on a pool. After the server kills every connection: the single connection is dead for good (`the connection is closed`); a plain pool also fails on the next run too; **a pool with `check=AsyncConnectionPool.check_connection` recovers on the first run**. |
| Q2 migration | The plan's "run `setup()` from the migration" **does not hang** here (corrected from my pre-spike guess: with no snapshot held by the migration's transaction `CREATE INDEX CONCURRENTLY` on another connection does not wait), but it is **not atomic**: `setup()` on a second connection commits on its own, and a failing statement later in the revision leaves the tables behind. Approach (a) `autocommit_block()` + `PostgresSaver(raw).setup()` works but also leaves them behind (the block commits everything before it). Approach (b), the migration runs the library SQL with plain `CREATE INDEX` and fills `checkpoint_migrations`, works, is rolled back completely on failure, a later `setup()` is a no-op (10 rows before and after), downgrade drops all four tables, upgrade works again. `alembic check` lists the four tables and their indexes as drift without a filter and is clean with `include_object` skipping `checkpoint*`. |
| Q3 privileges | As the app role with only SELECT, INSERT, UPDATE, DELETE on the three data tables and SELECT on `checkpoint_migrations`, `aput`, `aput_writes`, `aget_tuple`, `alist` and `adelete_thread` all work. The app needs no CREATE and no TEMP. `setup()` as the app is refused (`permission denied for schema public`); DROP, TRUNCATE and ALTER are refused. `ALTER DEFAULT PRIVILEGES FOR ROLE blastproof_owner ... GRANT SELECT, INSERT, UPDATE, DELETE` also gave the app UPDATE and DELETE on a later `audit_like` table. |
| Q4 interrupt | On `Command(resume=...)` the interrupted node re-runs from its start; earlier nodes do not (`request_review` 1 run, `human_review` 2). With the status write in its own node the history is `awaiting_review, approved`. The one-node version re-runs the status write (`awaiting_review, awaiting_review, approved`): why D-025 split it. |
| Q5 restart | A second process with a new saver and graph object sees `next == ('human_review',)` and one pending interrupt, and resumes. After a crash in a node past the interrupt: `next == ('finish',)`, `tasks[].error` holds the exception, no pending interrupt; `ainvoke(None, config)` continues without re-running `human_review`. Unknown thread: `values == {}`, `next == ()`, no tasks. `Command(resume)` on a finished thread: no error, no node runs. `Command(resume)` on an unknown thread: starts a run and fails on the missing state (`KeyError`). `ainvoke(None)` on a finished thread: no-op returning the final state. `ainvoke(None)` on an unknown thread: `EmptyInputError`. |
| Q6 edges | Two concurrent resumes on one thread **both run**: `finish` ran twice, the status was written twice, the later decision won. LangGraph is no guard; step 15's SQL claim is. Pydantic model, UUID, datetime, Decimal round-trip with their types. **But reading a checkpoint holding a Pydantic model logs "Deserializing unregistered type ... will be blocked in a future version. Set `LANGGRAPH_STRICT_MSGPACK=true` ... or add to `allowed_msgpack_modules`"**: step 14 must register the state model modules or keep state to plain types. A node exception leaves `next` at that node, `tasks[].error` set, and the last good values. Hard kill at the start of node 2: `durability="sync"` kept node 1's checkpoint in 3 of 3 runs; the default `"async"` and `"exit"` lost it in 3 of 3 (thread has no checkpoint at all). |
| Q7 timing | Median of 30 three-node runs with no work in the nodes: 1.3 ms without a checkpointer, about 25 ms with one (single or pool, `async` or `sync`). Roughly 8 ms per checkpoint on this machine; negligible next to the 14 to 60 s shadow run. |

## Plan differences and proposed edits

1. **Migration (backend plan step 14, BE-78):** use approach (b). The migration holds the library's 10 statements
   (with `CONCURRENTLY` removed), writes `checkpoint_migrations` rows 0 to 9, and grants the app explicitly. Add a
   test that the migration's statements equal `BasePostgresSaver.MIGRATIONS` modulo `CONCURRENTLY`, so a library
   upgrade that adds a migration fails CI. Drop "run `setup()` from a migration".
2. **Grants (plan §5, BE-78):** drop `ALTER DEFAULT PRIVILEGES`; grant on the four tables in the migration; add a test
   that every `checkpoint%` table has exactly the expected app grants.
3. **`env.py`:** add `include_object` skipping tables named `checkpoint*` (indexes follow their table), or `alembic check` fails.
4. **Connection:** the pool needs `check=AsyncConnectionPool.check_connection`, kwargs `autocommit=True`,
   `prepare_threshold=0`, `row_factory=dict_row`. That imports `psycopg_pool` directly: pin it in `pyproject.toml`
   (it is in the lock already; a new direct dependency, needs your OK in step 14). A test must assert a checkpoint row exists after a run.
5. **Durability:** run with `durability="sync"` (step 15 recovery reads checkpoints).
6. **Resume shapes (step 14/15):** use `version="v2"` and `aget_state().interrupts` / `.next`; guard resumes by the SQL claim, and call
   `Command(resume)` only for a thread whose snapshot has `next == ('human_review',)`.
7. **State types:** keep the state to plain types, or register model modules via `allowed_msgpack_modules`.

## Not tested

A migration whose transaction holds a snapshot (REPEATABLE READ) before `setup()`; pgbouncer or Neon's pooled endpoint
(`prepare_threshold=0` untested against one); `LANGGRAPH_AES_KEY` encryption; the real Neon-hosted `blastproof-app`
database; `interrupt()` inside a try/except; several interrupts in one node.

## Real output

`probe`:

```text
setup INFO scratch database bp_spike_854b469bf6; versions: see spikes/langgraph_interrupt.md
--- Q1 ---
Q1.1 PASS as blastproof_app, from_conn_string(plain URL) runs a graph to its interrupt
Q1.2 INFO no autocommit: graph run -> worked; connection afterwards -> usable
Q1.2b INFO checkpoints rows persisted for that thread without autocommit: 0 (no explicit commit was issued)
Q1.3 INFO no dict_row (autocommit on): worked, interrupts=1
Q1.4 INFO default prepare_threshold (5), direct connection: worked
Q1.5-single PASS 5 parallel actions on one single saver all paused (0.32 s)
Q1.5-pool PASS 5 parallel actions on one pool saver all paused (0.40 s)
Q1.6 INFO single, all app sessions killed: 1st run -> AdminShutdown: terminating connection due to administrator command; 2nd run -> OperationalError: the connection is closed
Q1.6 INFO pool, all app sessions killed: 1st run -> AdminShutdown: terminating connection due to administrator command; 2nd run -> AdminShutdown: terminating connection due to administrator command
Q1.6 INFO pool+check, all app sessions killed: 1st run -> worked; 2nd run -> worked
--- Q3 ---
Deserializing unregistered type __main__.Payload from checkpoint. This will be blocked in a future version. Set LANGGRAPH_STRICT_MSGPACK=true to block now, or add to allowed_msgpack_modules to allow explicitly: [('__main__', 'Payload')]
Q3.1 PASS aput, aput_writes and aget_tuple (inside ainvoke) work as blastproof_app with SELECT/INSERT/UPDATE/DELETE only
Q3.2 PASS alist works (6 checkpoints for one finished action)
Q3.3 PASS adelete_thread works (ok)
Q3.3b PASS nothing left for that thread after adelete_thread
Q3.4 PASS setup() as blastproof_app is refused: InsufficientPrivilege: permission denied for schema public at character 28
Q3.5 PASS app has no TEMP right: InsufficientPrivilege: permission denied to create temporary tables in database "bp_spike_854b469bf6" LINE 1: CREATE TEMP TABLE t (x int) ^
Q3.6 PASS app refused `DROP TABLE checkpoints`: InsufficientPrivilege: must be owner of table checkpoints
Q3.6 PASS app refused `TRUNCATE checkpoints`: InsufficientPrivilege: permission denied for table checkpoints
Q3.6 PASS app refused `ALTER TABLE checkpoints ADD COLUMN x int`: InsufficientPrivilege: must be owner of table checkpoints
Q3.7 INFO app grants on checkpoint tables: {'checkpoint_blobs': 'DELETE,INSERT,SELECT,UPDATE', 'checkpoint_migrations': 'SELECT', 'checkpoint_writes': 'DELETE,INSERT,SELECT,UPDATE', 'checkpoints': 'DELETE,INSERT,SELECT,UPDATE'}
Q3.8 INFO after ALTER DEFAULT PRIVILEGES, a new table `checkpoint_future` gives app UPDATE=True DELETE=True
Q3.8 INFO after ALTER DEFAULT PRIVILEGES, a new table `audit_like` gives app UPDATE=True DELETE=True
Q3.8b PASS ALTER DEFAULT PRIVILEGES also grants UPDATE/DELETE on a later non-checkpoint table (the plan should drop it)
--- Q4 ---
Q4.1 INFO after the first ainvoke: interrupts=[{'action_id': 'e2246b3e-b5c8-4267-8b26-ddbc0c8c2f52', 'ask': 'approve?'}] runs={'classify': 1, 'request_review': 1, 'human_review': 1}
Q4.2 PASS each node ran once up to the interrupt
Q4.3 PASS aget_state().next == ('human_review',): ('human_review',)
Q4.3b PASS aget_state().interrupts has the pending interrupt: 1
Q4.4 INFO after Command(resume): value.status='approved' decision={'approve': True, 'note': 'ok'} runs={'classify': 1, 'request_review': 1, 'human_review': 2, 'finish': 1}
Q4.5 PASS classify and request_review did NOT re-run
Q4.6 PASS human_review DID re-run from its start (2 runs)
Q4.7 PASS final status approved, no pending interrupt
Q4.8 PASS status history in the database: ['awaiting_review', 'approved']
Q4.8b PASS resume value (a dict with a note) arrives intact
Q4.9 PASS one-node version: the node ran twice (runs={'classify': 1, 'review_in_one_node': 2, 'finish': 1})
Q4.10 PASS one-node version: status written twice, history ['awaiting_review', 'awaiting_review', 'approved']
--- Q5 ---
Q5.1 INFO process A: exit=0 A paused interrupts=1
Q5.2 INFO process B (new saver, new graph object): exit=0 B next=('human_review',) interrupts=1 status=awaiting_review | B resumed status=approved interrupts=0
Q5.3 PASS a second process sees the pause and resumes it
Q5.3b PASS run counts across the two processes: {'classify': 1, 'request_review': 1, 'human_review': 2, 'finish': 1}
Q5.4 INFO resume with a crashing `finish`: RuntimeError: simulated crash after the review
Q5.5 PASS after the crash, aget_state().next == ('finish',): ('finish',)
Q5.5b INFO tasks[].error after the crash: ["RuntimeError('simulated crash after the review')"]; pending interrupts=0
Q5.6 PASS ainvoke(None, config) continues from the checkpoint: approved
Q5.6b PASS human_review not re-run by the continue (before 2, after 2)
Q5.7 INFO unknown thread: values={} next=() tasks=() interrupts=() config=False
Q5.7b PASS unknown thread -> empty snapshot (values == {}, next == ())
Q5.8 INFO Command(resume) on a FINISHED thread: no error, status=approved; runs unchanged=True
Q5.9 INFO Command(resume) on an UNKNOWN thread: KeyError: 'action_id'
Q5.10 INFO ainvoke(None) on a FINISHED thread: no error, status=approved
Q5.11 INFO ainvoke(None) on an UNKNOWN thread: EmptyInputError: Received no input for __start__
--- Q6 ---
Q6.1 INFO two concurrent Command(resume): errors=[]; runs={'classify': 1, 'request_review': 1, 'human_review': 3, 'finish': 2}; status history=['awaiting_review', 'approved', 'approved']
Q6.1b INFO final decision stored: {'approve': False, 'who': 'b'}
Q6.2 PASS LangGraph does NOT stop a double resume (finish ran 2x): step 15's SQL claim is the guard
Q6.3 PASS Pydantic model / UUID / datetime / Decimal round-trip with their types: Payload, Decimal
Q6.3b INFO warnings during the round trip: none
Q6.5 INFO exception in a node: raised RuntimeError: simulated crash after the review; next=('finish',); tasks=[('finish', "RuntimeError('simulated crash after the ")]; status='awaiting_review'
Q6.6 INFO durability='async', process killed at the start of node 2, 3 trials: n1's result lost 3x; ['next=() status=None', 'next=() status=None', 'next=() status=None']; child exit=1 
Q6.6 INFO durability='sync', process killed at the start of node 2, 3 trials: n1's result lost 0x; ["next=('n2',) status='n1 done'", "next=('n2',) status='n1 done'", "next=('n2',) status='n1 done'"]; child exit=1 
Q6.6 INFO durability='exit', process killed at the start of node 2, 3 trials: n1's result lost 3x; ['next=() status=None', 'next=() status=None', 'next=() status=None']; child exit=1 
--- Q7 ---
Q7.1 INFO 3-node graph, median of 30 runs, no DB work in nodes: no checkpointer 1.3 ms; single {'async': 24.057300004642457, 'sync': 25.257300003431737}; pool {'async': 24.672600004123524, 'sync': 26.56455000396818} (ms)

all expected checks passed
```

`migrate`:

```text
Q2.a-atomic INFO revision fails after the checkpoint DDL (DataError: (psycopg.errors.DivisionByZero) division by zero [SQL: SELECT 1/0] (Background on this error at: https://sqlalche.me/e/21/9h9h)); checkpoint/after tables left: ['checkpoin
Q2.a-atomic INFO approach a is NOT atomic: left behind ['checkpoint_blobs', 'checkpoint_migrations', 'checkpoint_writes', 'checkpoints']
Q2.b-atomic INFO revision fails after the checkpoint DDL (DataError: (psycopg.errors.DivisionByZero) division by zero [SQL: SELECT 1/0] (Background on this error at: https://sqlalche.me/e/21/9h9h)); checkpoint/after tables left: []
Q2.b-atomic PASS approach b leaves nothing half-built after a failure
Q2.naive INFO setup() on a second connection inside the one-transaction migration: 0.1 s (guard 10 s): no error; tables left: ['after_checkpoint', 'alembic_version', 'checkpoint_blobs', 'checkpoint_migrations', 'checkpoint_writes', 'checkpo
Q2.naive-write INFO setup() on a second connection inside the one-transaction migration: 0.1 s (guard 10 s): no error; tables left: ['after_checkpoint', 'alembic_version', 'before_checkpoint', 'checkpoint_blobs', 'checkpoint_migrations', 'c
Q2.a PASS upgrade head with approach a: ok (0.1 s); tables=['after_checkpoint', 'alembic_version', 'checkpoint_blobs', 'checkpoint_migrations', 'checkpoint_writes', 'checkpoints']
Q2.a INFO checkpoint_migrations=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9] indexes=['checkpoint_blobs_thread_id_idx', 'checkpoint_writes_thread_id_idx', 'checkpoints_thread_id_idx'] task_path=True invalid_indexes=0
Q2.a-setup PASS setup() afterwards is a no-op (rows 10 -> 10; no error)
Q2.a-down PASS downgrade drops all four tables cleanly; left: ['alembic_version']
Q2.a-reup PASS upgrade again after downgrade: ok
No new upgrade operations detected.
Q2.b PASS upgrade head with approach b: ok (0.1 s); tables=['after_checkpoint', 'alembic_version', 'checkpoint_blobs', 'checkpoint_migrations', 'checkpoint_writes', 'checkpoints']
Q2.b INFO checkpoint_migrations=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9] indexes=['checkpoint_blobs_thread_id_idx', 'checkpoint_writes_thread_id_idx', 'checkpoints_thread_id_idx'] task_path=True invalid_indexes=0
Q2.b-setup PASS setup() afterwards is a no-op (rows 10 -> 10; no error)
Q2.b-down PASS downgrade drops all four tables cleanly; left: ['alembic_version']
Q2.b-reup PASS upgrade again after downgrade: ok
Q2.b-check INFO alembic check (empty metadata) include_object filter=False: AutogenerateDiffsDetected: New upgrade operations detected: [('remove_index', Index('checkpoint_writes_thread_id_idx', Column('thread_id', TEXT(), table=<checkpoint
No new upgrade operations detected.
Q2.b-check INFO alembic check (empty metadata) include_object filter=True: clean
Q2.b-check PASS with the include_object filter alembic check is clean
Q2.b-app INFO app role can read checkpoints after the migration's own grants: aget_tuple on empty -> None

all expected checks passed
```

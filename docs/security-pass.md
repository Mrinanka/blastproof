# Security pass: what can reach each sink

The sink-by-sink review that ARCH-16's verification asks for (backend plan §12). For each sink: what
can reach it, what protects it, and the test that proves it. The call-site rules stay the primary
protection; the redactor (`app/services/redaction.py`, D-256) is the last layer behind them.

| Sink | What can reach it | Protection | Proof |
|---|---|---|---|
| Logs (structlog) | fields chosen at each call site; exception class names | allowlist at call sites (D-110); `redact_event_dict` over every field | `test_logging_redaction.py`, `test_redaction.py` |
| Logs (stdlib, uvicorn, third-party, tracebacks) | whatever a library writes | the same processor, after the traceback is formatted; `mcp.*` details stripped; `httpx`/`httpcore` at WARNING; the reloader's handler (`log_config.json`) is the same handler | `test_logging_redaction.py`, `test_mcp_logging.py` |
| LangSmith traces | prompts (already masked), model answers, metadata, error text | one client with an anonymizer, installed always (D-257) | `test_tracing.py` |
| LangSmith dataset examples | the committed `labeled.yaml` | synthetic data; **not** covered by the anonymizer (accepted) | `test_eval_langsmith.py` |
| LLM prompts | masked SQL display, masked intent, masked sample rows, retrieved knowledge | masking at the call sites (D-110, D-149) | impact and judge tests |
| REST API | action fields; no raw `sql`; no credentials | response models; `visible_actions` (404, never 403) | authorization matrix |
| MCP replies | query results, notes, statuses | `describe_table` notes masked; owner-scoped status | `test_describe_table_notes.py`, `test_mcp_*` |
| Checkpoints | graph state | state holds no raw agent text (`workflow/state.py`) | `test_workflow_*` |
| The hook | the tool call's text | guardrail only; the boundary is credential isolation | `test_hook_*` |
| The CLI | stdout results; logs go to stderr | the same redactor on stderr; no secrets printed | `test_cli_*` |

## Accepted gaps

- A secret shorter than 12 characters (a local password such as `company`) is not registered as a
  known value, because it would blank ordinary words (D-080). Pattern rules still catch it in a URL
  or a `password=` pair.
- A 7 to 15 digit run in prompt text may be masked in a trace: the safe direction.
- LangSmith dataset examples are not masked (synthetic, committed data).
- Lines outside a request (startup, the maintenance task, recovery) carry no `request_id`.
- The middleware's `request_finished` line for an MCP call carries no `action_id`.

## Still open

- ARCH-16's deployed half: Fly secrets and `kill_timeout` (SEC-22).
- Turning on Dependabot alerts (SEC-21) is a repository setting.
- The hand check against real LangSmith (SEC-15, "trace inspection"): one write with planted values,
  then confirm in the LangSmith UI that no planted value appears in any input, output or error.

# Blastproof: Project Specification

Oct 24, 2026 · @Mrinanka Dhar

## 1. Overview

Blastproof is a safety checkpoint that sits between AI agents and the real systems they act on. Every risky action an agent attempts is previewed first, explained in plain language, and approved, blocked or sent to a human before it touches production.

**One-line pitch:** It lets AI agents take real actions safely: every risky action runs on a copy first, and humans approve what it will actually do, not cryptic commands.

**Core idea:** humans should approve outcomes, not commands. Instead of asking "approve this SQL?", Blastproof asks "approve deleting 12 paying customers?"

**What it is, technically:** a vendor-neutral MCP gateway plus a Claude Code hook integration. It intercepts agent tool calls (SQL, shell, files, cloud APIs, external apps), runs what it can on a disposable shadow copy, computes the real impact, applies policy and AI risk judgement, routes for approval, then commits, logs and supports undo.

**What it is not:** a new AI agent, a database, or a replacement for normal access control. It works alongside existing agents and permissions.

## 2. Problem statement

AI agents are being given write access to real systems, and when they make mistakes, the damage is real and fast. There is no simple, agent-neutral way to let them act while keeping a human in control of the consequences.

**Evidence it is happening:**

| When     | What happened                                                                                                                                      | Source                                                                                                                                                                                  |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Apr 2026 | A Cursor agent running Claude Opus 4.6 deleted PocketOS's production database and backups via one cloud API call, using a token it found in a file | [Live Science](https://livescience.com/technology/artificial-intelligence/i-violated-every-principle-i-was-given-ai-agent-deletes-companys-entire-database-in-9-seconds-then-confesses) |
| Mar 2026 | An agent running Terraform commands deleted a founder's production database                                                                        | [OECD AI incidents](https://oecd.ai/en/incidents/2026-03-07-79ec)                                                                                                                       |
| Jul 2025 | A Replit agent deleted SaaStr's production database during a code freeze, then produced misleading output                                          | [OECD AI incidents](https://oecd.ai/en/incidents/2025-07-19-1eb1)                                                                                                                       |

**Why current options fail:**

- No write access: the agent becomes useless for real work.
- Full access: one bad guess destroys data.
- Approve every command: humans see raw SQL or shell and cannot judge it, so they click approve anyway.
- Database branching from vendors ([Databricks Lakebase](https://www.unite.ai/?p=483300), [Xata](https://xata.io/use-cases/agent-sandboxes)) protects only that vendor's database. It would not have stopped the PocketOS deletion, which was a cloud API call, not SQL.

**Honest scope of the problem:** most incidents so far involve startups and individual developers using coding agents. Large enterprises mostly restrict agents to read-only today. The trend is toward more write access, which is why the problem grows.

**Why now:** MCP has made it easy to connect agents to any tool, coding agents run with broad permissions by default, and public incidents have made agent safety a board-level concern.

## 3. Goals and non-goals

The goal is a working, production-grade product that catches the real-world incident patterns above, with measured results to prove it.

**Product goals:**

1. Intercept every tool call an agent makes through MCP or Claude Code hooks.
2. Preview reversible actions on a disposable copy and show their real impact in plain language.
3. Auto-approve safe actions with minimal delay, block clearly dangerous ones, and route the rest to a human.
4. Commit approved actions safely, keep a full audit log, and support undo where possible.
5. Detect dangerous sequences of actions, such as reading private data then sending it externally.
6. Keep credentials away from the agent entirely.

**Portfolio goals:**

1. Demonstrate MCP, LangGraph, LangChain, RAG, vector DB, memory, guardrails, evals, security, observability, LangSmith, workflow orchestration and infrastructure, each where it genuinely fits.
2. Produce measurable results: catch rate, false block rate, added latency.
3. A live demo that recreates real incidents and stops them.

**Non-goals (for now):**

- Supporting every database engine. Postgres only at first.
- Full multi-tenant SaaS with billing.
- Perfect undo for irreversible actions like sent emails. These are held for approval instead.
- Building our own AI agent. Blastproof works with existing agents.

## 4. Target users and personas

Blastproof serves three people: the developer running an agent, the reviewer approving its actions, and the admin setting the rules.

| Persona                   | Who they are                                                                 | What they need from Blastproof                                                                       |
| ------------------------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Agent developer           | Uses Claude Code or another agent with access to databases, servers or cloud | Work fast without fear; agent keeps working on safe actions; clear feedback when blocked             |
| Reviewer / approver       | Tech lead, on-call engineer or data owner                                    | See the real consequence of an action in plain language; approve or reject in seconds; undo mistakes |
| Platform / security admin | Owns infrastructure and security policy                                      | Define rules, see every agent action in an audit log, control which agents reach which systems       |

**Primary user for the MVP:** a solo developer or small startup team using Claude Code with a Postgres database. This is exactly the profile of the public incidents. MVP access model: each user owns their agent keys and sees and decides only their own agents' actions, tracked by user ID; admins see everything and manage users. Team review, per-database scoping and separation of duties come later.

## 5. Core concepts and terminology

These terms are used throughout the spec and the codebase; agreeing on them early keeps the design consistent.

| Term                        | Meaning                                                                                        |
| --------------------------- | ---------------------------------------------------------------------------------------------- |
| Action                      | One tool call an agent attempts: a SQL query, shell command, file edit, API call or app action |
| Session                     | A sequence of actions by one agent in one task; used to detect dangerous combinations          |
| Read / write / irreversible | The three classes every action is sorted into                                                  |
| Shadow run                  | Executing a write on a disposable copy (database branch or sandbox) instead of the real system |
| Dry run                     | Using a tool's own preview mode (e.g. Terraform plan) when a copy is not possible              |
| Impact report               | The plain-language result of a shadow or dry run: what changed, how much, which sensitive data |
| Risk score                  | Combined verdict from policy rules and the AI judge: low, medium, high, critical               |
| Decision                    | Auto-approve, human review or block                                                            |
| Change request              | A pending action shown to a reviewer, like a pull request                                      |
| Commit                      | Applying an approved action to the real system after a drift check                             |
| Drift check                 | Confirming the affected data has not changed since the preview                                 |
| Before-image                | A saved copy of affected data, used for undo                                                   |
| Policy                      | A rule set by an admin, e.g. "never delete more than 1,000 rows without review"                |
| Audit log                   | An append-only record of every action, decision and who approved it                            |

## 6. How it works, end to end

Every action follows the same path; only the preview method changes by action type. Example: an agent is told "clean up inactive users."

1. **Intercept.** The agent writes a DELETE query and calls its database tool. The call reaches Blastproof, not the database.
2. **Authenticate.** Blastproof checks the agent's identity and which systems it may touch.
3. **Classify.** A SQL parser marks the query as a write affecting the users table.
4. **Session check.** Blastproof reviews the session's earlier actions for dangerous patterns.
5. **Shadow run.** Blastproof creates an instant branch of the database, runs the query there, and records before and after.
6. **Impact report.** The diff engine counts changes; RAG over schema docs and business rules adds meaning: "48,213 users deleted, 12 paying customers, 3 active yesterday."
7. **Risk decision.** Policy rules run first; an LLM judge handles gray areas. Result: human review.
8. **Review.** The dashboard shows a change request with the plain-language impact. The reviewer rejects it with a reason.
9. **Feedback.** The agent receives the reason, rewrites the query to exclude paying customers, and the flow restarts.
10. **Commit.** The safer query is approved. Blastproof runs a drift check, saves a before-image, applies it to production, and writes the audit log.
11. **Clean up.** The shadow branch is deleted.

Read-only actions skip steps 5 to 9 and pass straight through. Irreversible actions skip the shadow run and always go to review or block.

## 7. Functional requirements by module

Blastproof has ten modules; MVP marks what must exist for the first demo, Later marks what follows.

| #   | Module            | Must do                                                                                                                                                                             | Phase                                       |
| --- | ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------- |
| 1   | Gateway           | Act as an MCP server to agents; proxy calls to real tools; hold all credentials; per-agent API keys                                                                                 | MVP                                         |
| 2   | Claude Code hook  | PreToolUse hook that forwards Bash, Edit and Write calls to Blastproof and returns allow or deny with a reason                                                                      | MVP                                         |
| 3   | Classifier        | Sort actions into read, write, irreversible using SQL parsing, shell command rules and API rules                                                                                    | MVP                                         |
| 4   | Shadow executor   | Postgres branch per write; Docker sandbox for shell and files; dry-run adapters (Terraform plan)                                                                                    | MVP: Postgres. Later: shell, Terraform      |
| 5   | Impact engine     | Row and file diffs; sensitive column detection; plain-language summary via LLM with RAG over schema and business rules                                                              | MVP                                         |
| 6   | Risk engine       | Policy rules (YAML) first; pluggable judge for gray areas (Claude by default, Jev as an alternative); prompt-injection checks on tool inputs and outputs; session pattern detection | MVP: rules + judge. Later: session patterns |
| 7   | Approval workflow | LangGraph flow with human-in-the-loop pause, timeout, reject reason sent back to the agent                                                                                          | MVP                                         |
| 8   | Commit and undo   | Drift check, before-image, transactional apply, one-click undo for database changes                                                                                                 | MVP: commit. Later: undo                    |
| 9   | Memory            | Remember past decisions per action pattern; suggest auto-approval for repeatedly approved patterns; per-agent history                                                               | Later                                       |
| 10  | Dashboard         | Pending change requests, impact view, approve/reject, audit history, policy editor, metrics                                                                                         | MVP: review + history. Later: policy editor |

**Cross-cutting requirements:** every action is written to the audit log; every LLM call and workflow run is traced; every decision has a human-readable reason.

## 8. Action coverage

Blastproof covers every action type, but how deeply it protects each depends on whether the action can be tried safely first.

| Group               | Examples                                                                          | How Blastproof previews it                                                                                | Undo                                                             |
| ------------------- | --------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------- |
| Can be copied       | Postgres writes, schema changes, shell commands, file edits, git changes          | Shadow run on a database branch or Docker sandbox; exact diff                                             | Yes, from before-image                                           |
| Has a dry-run mode  | Terraform apply, Kubernetes changes, some cloud APIs, payment test modes          | Tool's own preview (e.g. Terraform plan) parsed into an impact report                                     | Partial, depends on the tool                                     |
| Cannot be previewed | Send email, post to Slack, charge a card, delete a cloud resource with no dry-run | Show exactly what will happen (content, recipients, amount); stricter rules; always human review or block | Delay window or compensating action (e.g. refund) where possible |

**Dangerous combinations:** Some actions are safe alone but dangerous together. The classic case: an agent reads private data, is exposed to untrusted content (a document or web page with a hidden prompt injection), then sends something to an external address. Blastproof tracks each session and blocks the final step when all three conditions are present.

**Expansion order:** Postgres first (clearest demo), then shell and files, then one external app (email or Slack) to show irreversible handling, then Terraform as a stretch goal.

## 9. Integrations

Blastproof connects to agents in two ways, because coding agents like Claude Code have built-in tools that never pass through MCP.

**1. MCP gateway (any MCP agent):** Blastproof registers as an MCP server. Agents call Blastproof's tools (for example `query_database`, `run_command`, `send_email`) and Blastproof proxies them to the real systems. Works with Claude Code, Claude Desktop, Cursor and any MCP client.

**2. Claude Code hook (built-in tools):** A `PreToolUse` hook script receives each Bash, Edit and Write call as JSON, forwards it to Blastproof's API, and returns a decision. A deny blocks the call and sends the reason back to Claude, so it can retry safely. Per the [Claude Code hooks docs](https://code.claude.com/docs/en/hooks), these hooks run before permission-mode checks, so they still apply when a developer skips permission prompts. Check the current docs when building; hook details have changed across versions.

**3. Credential isolation:** Database passwords and API keys live only in Blastproof. They are never placed in the project's `.env` or the agent's environment, so the agent has no way around the gateway.

**Downstream systems (what Blastproof acts on):**

| System          | MVP | Later                          |
| --------------- | --- | ------------------------------ |
| Postgres        | Yes | Other SQL engines              |
| Shell and files | No  | Docker sandbox                 |
| Email or Slack  | No  | One app, irreversible handling |
| Terraform       | No  | Stretch goal                   |

**Notifications:** the dashboard is the main review surface; a Slack message with approve/reject links is a later addition.

## 10. Non-functional requirements

Blastproof must be safe when it fails, fast on safe actions, and explainable on every decision. The numbers below are starting targets to validate during the build, not measured results.

| Area              | Requirement                                                                            | Starting target                            |
| ----------------- | -------------------------------------------------------------------------------------- | ------------------------------------------ |
| Safety on failure | If Blastproof or any check fails, writes are blocked (fail closed); reads may continue | 0 writes pass on error                     |
| Latency, reads    | Pass-through overhead stays small so agents feel no slowdown                           | under 50 ms added at p95                   |
| Latency, writes   | Shadow run plus impact report fast enough to keep the agent's flow                     | under 5 s for a demo-size database         |
| Reliability       | Pending approvals survive restarts; no action is lost or applied twice                 | Durable workflow state; idempotent commits |
| Explainability    | Every decision carries a reason a non-expert can read                                  | 100% of decisions                          |
| Auditability      | Every action, decision and approver recorded; log is append-only                       | 100% of actions                            |
| Security          | Agent never sees credentials; all traffic authenticated; least-privilege DB roles      | See section 13                             |
| Scalability       | Several agents and sessions at once; shadow runs processed asynchronously              | Tested with concurrent sessions            |
| Cost              | Run within free tiers plus LLM usage                                                   | under $100 per month at demo scale         |

## 11. Architecture overview

All agent traffic enters through one gateway and follows a single path; the impact and risk engine is the heart of the product.

&#91;embedded content: Blastproof architecture · 7 components, one path\]

Writes follow the arrows from gateway to commit. Read-only actions leave the classifier and go straight to the real systems. Irreversible actions skip the shadow executor and always reach the approval workflow.

## 12. Tech stack

Python for the backend (best LangGraph and LangSmith support), TypeScript and React for the dashboard, Postgres for almost all storage. Costs are rough estimates at demo scale; confirm current free-tier limits when signing up.

| Layer            | Tool                                                                                                                                                 | Why                                                                                                                                         | Est. cost / month   |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | ------------------- |
| Gateway API      | Python, FastAPI, official MCP Python SDK                                                                                                             | Async, fast, standard MCP support                                                                                                           | $0                  |
| Claude Code hook | Small Python or shell script calling the gateway API                                                                                                 | Covers built-in Bash, Edit, Write                                                                                                           | $0                  |
| SQL parsing      | sqlglot                                                                                                                                              | Reliable read/write detection without an LLM                                                                                                | $0                  |
| Policy rules     | YAML rules engine (Open Policy Agent as a later option)                                                                                              | Deterministic, testable, admin-editable                                                                                                     | $0                  |
| Workflow         | LangGraph with Postgres checkpointer                                                                                                                 | Human-in-the-loop pause and resume; durable state                                                                                           | $0                  |
| LLM calls        | LangChain with Claude (Sonnet for judge and summaries, Haiku for cheap checks)                                                                       | Model-agnostic wrappers; structured output. The judge is pluggable, with Jev (TypeSafe AI) via Vercel AI Gateway as a second implementation | $20 to $60          |
| RAG / vector DB  | pgvector inside Postgres                                                                                                                             | Default store behind a swappable VectorStore interface; Qdrant as the dedicated vector DB alternative                                       | $0                  |
| Shadow databases | Neon Postgres branching                                                                                                                              | Instant copy-on-write branches; free tier                                                                                                   | $0 to $19           |
| Sandboxes        | Docker containers (E2B or Firecracker as later options)                                                                                              | Isolated shell and file previews                                                                                                            | $0                  |
| Background work  | FastAPI async background tasks (no Redis in the MVP)                                                                                                 | Keeps reads fast; workflow state and results saved in Postgres. Add a Postgres jobs table or Redis queue only if load tests show the need   | $0                  |
| Dashboard        | React, TypeScript, Next.js, React Query polling every 3 to 5 s                                                                                       | Near-live review queue with no socket infrastructure; your strongest frontend stack; hosted on Vercel                                       | $0                  |
| Auth             | API keys per agent; email and password dashboard login with a backend-issued JWT, kept in an httpOnly cookie by the dashboard's own proxy; SSO later | Simple, standard                                                                                                                            | $0                  |
| Secrets          | Doppler, Infisical or cloud secrets manager                                                                                                          | Credentials never in code or agent env                                                                                                      | $0                  |
| Tracing          | LangSmith                                                                                                                                            | LLM and LangGraph traces, datasets, experiments                                                                                             | $0 (developer tier) |
| Metrics          | OpenTelemetry, Prometheus, Grafana                                                                                                                   | Latency, block rate, queue depth                                                                                                            | $0                  |
| Evals            | LangSmith datasets, pytest, GitHub Actions                                                                                                           | Automated on every change                                                                                                                   | $0                  |
| Infra            | Docker Compose, Terraform, GitHub Actions, Fly.io or AWS                                                                                             | Reproducible, deployable                                                                                                                    | $0 to $30           |

**Estimated total:** roughly $20 to $110 per month, well inside the $1,000 budget.

## 13. Security model and threat model

Blastproof is itself a high-value target, because it holds every credential; the design assumes both the agent and its inputs may be hostile.

**Security principles:**

- Credential isolation: secrets live only in Blastproof's secrets manager.
- Least privilege: each agent gets its own identity and a scoped list of allowed systems and tools.
- Fail closed: any error blocks writes.
- Defense in depth: deterministic rules first, AI judgement second, human review third.
- Untrusted content: tool outputs and documents are data, never instructions to Blastproof's own LLM calls.

**Threat model:**

| Threat                           | Example                                                                     | Mitigation                                                                                                                  |
| -------------------------------- | --------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Destructive mistake              | Agent deletes far more rows than intended                                   | Shadow run, impact report, row-count rules, review                                                                          |
| Credential theft                 | Agent reads a token from a file and calls the cloud API directly (PocketOS) | No credentials in agent environment; all access via gateway                                                                 |
| Hook bypass                      | Agent runs `psql` directly in the terminal                                  | Claude Code `PreToolUse` hook on Bash; no DB credentials available locally                                                  |
| Prompt injection                 | A document tells the agent to email customer data out                       | Injection checks on tool inputs/outputs; session pattern detection; external sends always reviewed                          |
| Attacking Blastproof's LLM       | Malicious data in a diff tries to trick the risk judge into "low risk"      | Rules run before and can override the judge; judge sees data as quoted content; judge cannot lower a rule-based verdict     |
| Drift between preview and commit | Data changes after approval                                                 | Drift check before commit; re-run if changed                                                                                |
| Unauthorized approval            | Someone approves without permission                                         | Dashboard login; every action owned by one user ID and visible only to its owner and admins; approver recorded in audit log |
| Log tampering                    | Covering up an action                                                       | Append-only audit table; restricted write role                                                                              |
| Sensitive data exposure          | Impact reports or traces show customer PII                                  | Mask sensitive columns in reports and LangSmith traces                                                                      |

Use the OWASP Top 10 for LLM Applications as a checklist when designing each module.

## 14. Evals and success metrics

Evals prove Blastproof works: they measure how many dangerous actions it catches, how many safe ones it wrongly blocks, how accurate its explanations are, and how much delay it adds.

**Eval datasets:**

1. **Incident replays:** recreate the Replit, PocketOS and Terraform incidents in a test environment. Every one must be caught.
2. **Labeled action set:** a few hundred SQL, shell and API actions, each labeled safe, needs review, or dangerous. Mix obvious and tricky cases (e.g. a DELETE with a subtle WHERE mistake).
3. **Injection set:** sessions containing hidden prompt injections that try to exfiltrate data.
4. **Explanation set:** shadow-run diffs with known correct summaries, to grade the impact reports.

**Metrics:**

| Metric               | What it measures                                           | How                                                 |
| -------------------- | ---------------------------------------------------------- | --------------------------------------------------- |
| Catch rate           | Share of dangerous actions blocked or sent to review       | Labeled set + incident replays                      |
| False block rate     | Share of safe actions wrongly blocked or held              | Labeled set                                         |
| Explanation accuracy | Do impact reports state counts and risks correctly?        | Exact checks on numbers + LLM-as-judge in LangSmith |
| Judge agreement      | How often the LLM judge matches human labels on gray cases | Labeled gray-zone subset                            |
| Added latency        | Overhead on reads and writes                               | Load tests, p50 and p95                             |
| Cost per action      | LLM spend per intercepted write                            | LangSmith token tracking                            |

**Process:** evals run in GitHub Actions on every pull request; a drop in catch rate fails the build. Compare variants as LangSmith experiments, e.g. rules only vs rules plus judge, Claude judge vs Jev judge (including calibration), pgvector vs Qdrant for retrieval, or with and without RAG context, and record the before/after numbers for your resume.

## 15. Observability

Every action gets one trace ID that follows it from interception to commit, so any decision can be explained after the fact.

- **Traces (LangSmith):** every LLM call (judge, summaries, injection checks) and every LangGraph run, with inputs, outputs, tokens and cost. Sensitive values masked.
- **Traces (OpenTelemetry):** gateway request spans, shadow-run timings, database calls, queue waits.
- **Metrics (Prometheus + Grafana):** actions per minute by class, decision mix (auto, review, block), p50/p95 latency, shadow-run duration, approvals waiting, LLM cost per day, error rate.
- **Logs:** structured JSON logs with the trace ID on every line.
- **Alerts:** spike in blocked actions, approvals waiting too long, shadow-run failures, any fail-closed event.
- **Feedback loop:** when a reviewer overrides the AI judge, store it and add it to the eval dataset.

## 16. Infrastructure, deployment and CI/CD

Everything runs locally with one command and deploys to the cloud from the same definitions.

- **Services:** two app services (the FastAPI backend, which runs the gateway, hook API and background work, and the Next.js dashboard), Postgres with pgvector, and a Grafana stack. Shadow databases on Neon; Docker sandboxes (later phase) on the backend host. No Redis in the MVP.
- **Local:** Docker Compose brings up the full stack plus a demo company database seeded with realistic fake data.
- **Cloud:** backend on Fly.io (AWS as a later option for a stronger infra story), dashboard on Vercel, databases on Neon. Infrastructure defined in Terraform.
- **CI (GitHub Actions):** lint, type checks, unit tests, integration tests against a disposable Postgres, then the eval suite with pass/fail thresholds.
- **CD:** merge to main builds container images, runs migrations, deploys to a staging environment, then promotes to production.
- **Environments:** local, staging, production (demo). Separate secrets and databases for each.
- **Resilience:** health checks, automatic restarts, durable workflow state in Postgres, database backups.

Nice touch for interviews: Blastproof can protect its own Terraform. Route your deploy pipeline's infrastructure changes through Blastproof.

## 17. Demo plan

A two-to-three-minute live demo in Claude Code against a fake company database, recreating real incidents and stopping them.

**Setup:** a demo SaaS company in Postgres (customers, plans, payments, orders), Claude Code in a project whose only database access is through Blastproof, and the dashboard open beside the terminal.

1. **Normal work flows.** "Add a note to customer 42." Auto-approved instantly. Shows Blastproof does not slow anyone down.
2. **The Replit moment.** "Clean up inactive users." Claude writes a DELETE. The dashboard shows: 48,213 users, including 12 paying customers. Reject with a reason. Claude rewrites a safer query, which is approved and committed. This is the "whoa" moment.
3. **The PocketOS moment.** Claude tries to delete a cloud storage volume via API. No preview possible, so it is blocked as an irreversible production action. Point out the agent never had the API key.
4. **The injection moment (later phase).** Claude reads a support ticket containing a hidden instruction to email customer data out. Blastproof blocks the external send based on the session pattern.
5. **Undo.** Show an approved change that turned out wrong; one click restores the data.
6. **Close on the numbers.** Show the eval dashboard: incidents replayed, catch rate, false block rate, added latency, using your real measured results.

Record a polished video of the demo for your README and LinkedIn, in case a live demo fails during an interview.

## 18. Build roadmap and step-by-step order

Build one thin, working end-to-end loop first, then harden it, then widen coverage. Do not start a phase until the previous gate passes.

&#91;embedded content: Build roadmap · 4 phases, 3 gates\]

Phase 1 is the MVP: the Replit-style demo working end to end. Each gate is a real incident scenario that must be caught.

**Step-by-step build order:** Before coding each step, write a one-page design note for it (inputs, outputs, data model, failure cases); that is the detailed design we do next.

1. Seed a fake company database and write the incident-replay scenarios first, so every later step can be tested against them.
2. Set up the repo, Docker Compose, Postgres with pgvector, and a CI pipeline with lint and tests.
3. Build the gateway as an MCP server with one read tool that passes through; connect Claude Code to it.
4. Add the Claude Code `PreToolUse` hook and remove all database credentials from the agent's environment.
5. Build the classifier: sqlglot for SQL, simple rules for shell and API calls.
6. Build the shadow executor with Neon branching and the diff engine (rows changed, sensitive columns).
7. Add YAML policy rules and the risk decision (auto, review, block).
8. Add RAG over schema docs and business rules, the plain-language summary, and the LLM judge for gray areas.
9. Build the LangGraph approval workflow with human-in-the-loop pause, Postgres checkpointer and reject feedback to the agent.
10. Build the dashboard: change requests, approve/reject, history.
11. Add commit with drift check, before-image and audit log. Gate 1: the Replit scenario is caught end to end.
12. Run evals in CI and LangSmith experiments; add OpenTelemetry metrics and Grafana dashboards.
13. Apply the threat model fixes and deploy with Terraform. Gate 2: evals green and deployed.
14. Widen coverage: shell and file sandbox, undo, memory, one external app, session pattern detection. Gate 3: PocketOS and injection scenarios caught.
15. Showcase: Terraform dry-run as a stretch, final README, video, blog post and resume bullets with real numbers.

## 19. Risks, trade-offs and open questions

The hardest problems are where the preview can differ from reality; each is a design decision to make deliberately, and each is a strong interview story.

| Risk or trade-off                       | Why it matters                                                                       | Starting approach                                                                          |
| --------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------ |
| Preview differs from real run           | Queries using NOW(), random values or sequences behave differently on replay         | Detect non-deterministic functions; pin values where possible; flag to reviewer            |
| Data changes between preview and commit | Approved impact no longer true                                                       | Drift check on affected rows; re-run shadow if changed                                     |
| Large databases                         | Branching cost and time                                                              | Use copy-on-write branching; for the demo, keep data moderate                              |
| Long transactions on production         | A simpler "run in a transaction then roll back" preview would lock production tables | Use branches, not production transactions, for previews                                    |
| Over-blocking                           | Developers abandon a tool that gets in the way                                       | Track false block rate; auto-approve safe patterns; memory of past approvals               |
| LLM judge errors                        | Wrong risk verdicts                                                                  | Rules can override the judge; judge only adds caution in gray zones; evals track agreement |
| Scope creep                             | Too many integrations, none polished                                                 | Strict phase order; Postgres demo polished before expanding                                |
| Hook behavior changes                   | Claude Code hook formats evolve                                                      | Pin a version; integration tests on the hook                                               |

**Open questions to settle during design:**

- [ ] Decided: one backend service and no Redis for the MVP. Open: what load-test result would justify a separate worker and a queue?
- [ ] Rules format: plain YAML, or Open Policy Agent from the start?
- [ ] How long can an agent wait for human review before the action times out?
- [ ] Which external app for the irreversible-action phase: email or Slack?
- [ ] Name check: is "Blastproof" free of conflicts with existing products?

## 20. Portfolio deliverables

The project is finished when a recruiter can understand it in 30 seconds and an engineer can verify it in 10 minutes.

- [ ] Public GitHub repo with a README that opens with the pitch, a 30-second GIF of the "whoa" moment, the architecture diagram and the eval results table.
- [ ] Two-to-three-minute demo video.
- [ ] One-command local setup (`docker compose up`) with seeded demo data.
- [ ] Live deployed demo (optional, with a read-only public dashboard).
- [ ] Eval report: datasets, metrics, before/after experiment results.
- [ ] Design notes: the key trade-offs from section 19 and how you solved them.
- [ ] A blog post or LinkedIn write-up: "How I stopped AI agents from deleting production databases."
- [ ] 4 to 5 resume bullets with your real measured numbers (catch rate, false block rate, latency, incidents replayed).
- [ ] Prepared answers for interview questions: why rules before LLM, how drift is handled, how prompt injection is detected, how undo works, what you would build next.

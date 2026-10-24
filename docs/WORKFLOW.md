# Building Blastproof with Claude Code: the workflow

Copy the prompts below into Claude Code. Replace anything in <ANGLE BRACKETS>.
Golden rules: one step per session, plan before code, evidence before "done",
and you personally run every "Done when" check.

---

## Part 1: One-time setup (before any code)

1. Do the outside setup in section 2 of both plans (accounts, API keys, tools).
2. Create the repo with: `CLAUDE.md`, `PROGRESS.md`, `docs/spec.md`,
   `docs/backend-plan.md`, `docs/frontend-plan.md`, empty `docs/decisions.md`,
   this file as `docs/WORKFLOW.md`. Commit.
3. Run Prompt 0A (plan audit). Fix the plans with what it finds. Commit.
4. Run Prompt 0B (requirements list). Review it. Commit.

### Prompt 0A: plan audit (once)

```text
Read CLAUDE.md and all three plans in docs/. Do not write any code.
Act as a skeptical staff engineer reviewing these plans before a build starts.
List, with section references:
1. Contradictions between the spec, backend plan and frontend plan.
2. Steps that are ambiguous enough that two engineers would build different things.
3. Anything required by a "Done when" check that no step actually builds.
4. Library or API assumptions that may be outdated (say which docs to verify).
5. Missing pieces you would expect in a production MVP of this scope.
Rank by how much each would hurt if not fixed. Be concrete; no generic advice.
```

### Prompt 0B: requirements list (once)

```text
Read all three plans in docs/. Create docs/REQUIREMENTS.md: a checklist of every
concrete requirement, with IDs (BE-01, FE-01, SEC-01, EVAL-01...). Each line:
ID, one-sentence requirement, the plan section it comes from, the build step that
implements it, and how it is verified. Include every "Done when" check, every
architecture rule in CLAUDE.md, and every item in the MVP done checklists.
Do not invent requirements; if something is implied but not stated, list it
separately at the end under "Implied, needs confirmation".
```

---

## Part 2: The loop for every step

Use this exact loop for every backend step (1-20) and frontend step (F1-F13).

1. Start a fresh session (`/clear`) so old context does not leak in.
2. Run Prompt 1 (plan). Use Claude Code's plan mode.
3. Review the plan with the checklist below. Correct it. Approve.
4. Run Prompt 2 (implement).
5. Run Prompt 3 (verify).
6. YOU run the "Done when" check yourself and read the diff.
7. Run Prompt 4 (wrap up). Commit.

### Prompt 1: plan the step

```text
Read CLAUDE.md, PROGRESS.md, docs/REQUIREMENTS.md, and <PLAN FILE> step <N>
(<STEP NAME>), plus any sections it refers to.
Before planning, check the current official docs for: <LIBRARIES THIS STEP USES>.
Tell me which versions you will use and cite the docs pages you read.

Then give me a plan, with no code yet:
1. Goal of this step in two sentences, and the requirement IDs it covers.
2. Files to create or change, and what each will contain.
3. Key functions, classes and data shapes.
4. Tests you will write first, including the "Done when" check as an automated test
   where possible.
5. Assumptions you are making that the plan does not state.
6. Anything in the step that looks wrong, risky or unclear.
7. What is explicitly NOT part of this step.
Wait for my approval.
```

**Your plan review checklist (2 minutes):**
- Does it cover everything in the step and nothing from later steps?
- Are its assumptions true? Correct any that are not.
- Did it cite real docs and pin versions for fast-moving libraries?
- Do the tests actually prove the "Done when" check?
- Does it respect every architecture rule in CLAUDE.md?

### Prompt 2: implement

```text
Approved, with these changes: <YOUR CORRECTIONS, OR "none">.
Implement the plan:
- Write the tests first for logic (classifier, rules, permissions, diffs), then the code.
- Work in small increments and run the tests after each one.
- Do not add dependencies beyond the plan without asking.
- Do not change or delete existing tests to make them pass; if a test seems wrong,
  stop and explain why.
- If anything surprises you (an API differs from the docs, a test fails for an
  unclear reason), stop and tell me instead of working around it.
```

### Prompt 3: verify with evidence

```text
Verify this step:
1. Run the full test suite and lint. Paste the actual output summary.
2. Run the "Done when" check for step <N>. Paste the actual output.
3. Review your own diff against the step's plan text and the architecture rules in
   CLAUDE.md. List any deviation, shortcut, TODO or hard-coded value.
4. List anything that is not finished or not working. Be honest; "done" only means
   the check passed.
5. Explain the two most important design choices in this step in plain language,
   as I would in an interview.
```

### Prompt 4: wrap up

```text
Wrap up step <N>:
1. Update PROGRESS.md: mark the step done, note anything partial in Known issues,
   and set the next step.
2. Tick the covered requirement IDs in docs/REQUIREMENTS.md.
3. Add any real decision, with its reason and the alternative rejected, to
   docs/decisions.md.
4. Suggest a commit message (Conventional Commits style).
```

---

## Part 3: Special prompts

### Spike: prove a risky API before building on it

Use BEFORE these steps: 6 (MCP mounting), 10 (Neon branching), 14 (LangGraph
interrupt and resume), 18 (Claude Code hook), Jev judge.

```text
Before step <N>, write a throwaway spike in spikes/<NAME>.py (not production code)
that proves <WHAT MUST WORK, e.g. "create a Neon branch, run one query on it, and
delete it">. Use the current official docs and cite them. Run it and paste the real
output. Then tell me exactly what the real API looks like and anything that differs
from what our plan assumes.
```

### Debug: when something breaks

```text
Something is broken: <WHAT YOU SEE, plus the full error>.
1. Reproduce it and show me the output.
2. List possible causes, most likely first, and how you would confirm each.
3. Confirm the cause before changing anything.
4. Make the smallest fix, add a test that would have caught it, and run all tests.
Do not change unrelated code and do not weaken tests or security rules to get green.
```

### Stage gate audit: at the end of every stage (fresh session)

```text
You are a skeptical senior engineer reviewing someone else's work. Read CLAUDE.md,
docs/REQUIREMENTS.md, PROGRESS.md and the plan sections for steps <A>-<B>.
Then inspect the code. Do not trust PROGRESS.md; verify against the code.
Report:
1. Requirements marked done that are not actually implemented or tested.
2. Requirements in these steps that are missing entirely.
3. Violations of the architecture rules in CLAUDE.md.
4. Security problems (secrets, authorization gaps, missing masking, fail-open paths).
5. Missing or weak tests, especially for failure cases.
Run the full tests and evals and paste the results. Rank findings by severity.
```

### Explain: so you can defend every line in interviews

```text
Explain <FILE OR FUNCTION> to me as if I'm preparing for an interview: what it does,
why it's designed this way, what alternatives exist, and what breaks if it fails.
Then ask me three questions an interviewer might ask about it.
```

---

## Part 4: The order to follow

Each line is one loop (Part 2). Spikes and audits are marked.

**Stage 0: setup** - outside setup, repo files, Prompt 0A, Prompt 0B.

**Stage 1: foundation (week 1)**
- Backend steps 1, 2, 3, 4, 5
- Spike: MCP mounting -> backend step 6 -> backend step 7
- GATE AUDIT (steps 1-7). Check: Claude Code reads the demo DB only via Blastproof.

**Stage 2: judging parts (week 2)**
- Backend steps 8, 9
- Spike: Neon branching -> backend step 10
- Backend steps 11, 12, 13
- GATE AUDIT (steps 8-13). Check: exact diffs and correct retrieval.

**Stage 3: the flow + dashboard start (week 3)**
- Pull forward: /v1/auth/me, GET /v1/actions, GET /v1/actions/{id},
  seed-demo-actions (from backend steps 19-20)
- Spike: LangGraph interrupt/resume -> backend steps 14, 15, 16, 17
- Frontend F1, F2, F3, F4, F5 (in between backend steps if you like)
- GATE AUDIT. Check: Gate 1, the Replit case is held and can be approved via /docs.

**Stage 4: finish backend + core dashboard (week 4)**
- Spike: Claude Code hook -> backend step 18
- Backend steps 19, 20, then evals, security pass and deployment (backend plan 12-15)
- Frontend F6, F7, F8, F9
- GATE AUDIT. Check: Gate 2, evals green and backend deployed.

**Stage 5: complete and ship (week 5)**
- Frontend F10, F11, F12, F13, deploy to Vercel
- FINAL AUDIT against both MVP done checklists, then record the demo video.

---

## Part 5: Rules that prevent most bugs and hallucinations

- Fresh session per step. Long sessions drift and forget the plan.
- Evidence, not claims: accept "done" only with real test and command output.
- Docs before code for fast-moving libraries; spike before building on them.
- Tests first for anything with logic or security.
- Never let tests or security rules be weakened to get green.
- Commit after every green step, so any bad change is one `git revert` away.
- If Claude says "should work", run it. If it says "I assumed", check it.
- Two strikes rule: if the same bug survives two fix attempts, stop, start a fresh
  session, and use the Debug prompt from scratch.
- Read every diff. If you can't explain a change, ask the Explain prompt before
  committing.

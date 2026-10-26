# Building Blastproof with Claude Code: the workflow

Every prompt below is copy-paste ready. None has blanks to fill in: Claude finds the
current step in `PROGRESS.md`. Where a prompt has an optional `<...>` line, delete it
if you have nothing to add.

Golden rules: one step per session, plan before code, evidence before "done", and you
run every "Done when" check yourself.

---

## Quick start: what do I do next?

Open `PROGRESS.md` and find the first unticked box. Then:

| The next box is...                         | Do this                                   |
| ------------------------------------------ | ----------------------------------------- |
| Something in Stage 0                       | Part 1, in order (0C, 0D, 0E)             |
| A numbered step (`1.`-`20.`, `F1.`-`F13.`) | Part 2 (the step loop)                    |
| `Pull forward: ...`                        | Part 2 (the step loop), same as a step    |
| `Evals`, security pass, deploy             | Part 2 (the step loop), same as a step    |
| `Spike: ...`                               | Prompt S (spike), then the next step loop |
| `GATE AUDIT` or `FINAL AUDIT`              | Prompt G (gate audit)                     |
| Demo video                                 | You, no prompt                            |

Lost or confused at any point? Paste **Prompt R (where am I?)** from Part 3.

---

## Part 1: One-time setup (Stage 0)

Done already: repo files, Prompt 0A (plan audit), Prompt 0B (requirements list).
They are kept at the end of this file for reference.

Remaining, in order. Use a fresh session (`/clear`) for each:

### Prompt 0C: check the requirements and settle the open questions

`REQUIREMENTS.md` was written by an AI, so spot-check it before you trust it. Then decide
every "Implied, needs confirmation" (IMP) item, because each step's plan will rely on them.

```text
Read CLAUDE.md, PROGRESS.md, docs/REQUIREMENTS.md, docs/decisions.md and the three plans.
Do not write any code.

Part 1, spot-check: pick 15 requirement rows spread across all sections. For each, quote
the source text it cites and say whether the row is accurate (correct, wrong, or
overstated). Then list any "Done when" check or MVP done checklist item that has no row.
Fix only clear mistakes in REQUIREMENTS.md and tell me what you changed.

Part 2, IMP items: go through the "Implied, needs confirmation" table 5 items at a time.
For each item give: your recommended answer, a one-line reason, and what changes if I
agree (plan edit, new requirement row, or "deferred"). Keep MVP scope small: when in
doubt, recommend the simplest safe option or deferring it. Then stop and wait for my
answers before the next 5.

After I answer a batch, apply it:
- add a decision entry to docs/decisions.md for each answer (next D-number, with the
  reason and the alternative rejected),
- make the smallest matching edit to the plan files,
- move accepted items into the right table in REQUIREMENTS.md with a proper ID, and
  mark deferred ones "Deferred (D-nnn)" in the IMP table.
```

Reply to each batch with something like `1 yes, 2 yes, 3 defer, 4 no because ..., 5 yes`.
When all 23 are done, paste:

```text
All IMP items are settled. Re-read the changed plan sections and check they don't
contradict each other or decisions.md. Then update PROGRESS.md: tick
"docs/REQUIREMENTS.md created and reviewed", set Current focus to the next item, and
suggest a commit message. Then commit everything with that message.
```

### Prompt 0D: outside setup (accounts, keys, tools)

```text
Walk me through the outside setup in backend plan section 2 and frontend plan section 2,
one item at a time. I do the clicking; you tell me exactly what to do. Check current
docs or sign-up pages if a step may have changed (e.g. Neon free-plan project limits).

Rules:
- Never ask me to paste a key, password or connection string into this chat. I put
  secret values in the .env file myself. You only tell me the variable NAME.
- For each item, give me a check command that proves it works without printing a
  secret (for example `uv --version`, `docker compose version`, or a script that prints
  only "connected OK").
- Use only the dev Neon project (blastproof-company-dev) for any connection test.
- Skip items the plan marks as needed later (Fly.io, Vercel AI Gateway); list them at
  the end so I remember.
After each item, wait for me to say "done" or describe a problem.
At the end, tick "Outside setup done" in PROGRESS.md and set the next step.
```

Tip: to run a check command yourself so Claude sees the output, type `! <command>`.

### Prompt 0E: check the repo is committed and ready

```text
Run git status and show me the result. If anything in CLAUDE.md, docs/ or PROGRESS.md is
uncommitted, show me the diff summary and suggest a commit message, then commit it when
I say yes. Then confirm Stage 0 is fully ticked in PROGRESS.md and tell me what the first
step of Stage 1 is.
```

---

## Part 2: The step loop (use it for every step)

Three prompts per step. Each step gets its own fresh session.

1. Type `/clear`.
2. Press **Shift+Tab** until the input box shows **plan mode**. Paste **Prompt 1**.
3. Read the plan with the checklist below. If something is wrong, reject the plan
   ("keep planning") and type your corrections. When it is right, approve it. Claude
   then builds it.
4. When Claude says it is finished, paste **Prompt 2** (verify).
5. **You** run the "Done when" commands it gives you (`! <command>`), and read the diff
   (`git diff`, or your editor's source control view).
6. Paste **Prompt 3** (wrap up and commit).

### Prompt 1: plan the next step (in plan mode)

```text
Find the first unticked item in PROGRESS.md. That is this session's step. Read CLAUDE.md,
PROGRESS.md (including Known issues), docs/REQUIREMENTS.md, docs/decisions.md, the plan
text for this step and any sections it refers to. If a spike for this step has already
run, read its findings in docs/decisions.md and spikes/.

Before planning, check the CURRENT official docs for every library or API this step uses
that is on CLAUDE.md's "check current documentation" list. Tell me the exact versions you
will pin and the docs pages you read.

Then give me the plan, with no code yet:
1. The step's goal in two sentences, and the requirement IDs it covers.
2. Files to create or change, and what each will contain.
3. Key functions, classes and data shapes.
4. Tests you will write first, including the "Done when" check as an automated test
   where possible. Include failure cases (errors, timeouts, wrong user).
5. Assumptions the plan does not state.
6. Anything in the step that looks wrong, risky or unclear, with your proposed fix.
7. What is explicitly NOT part of this step.

When I approve, build it with these rules:
- Tests first for logic (classifier, rules, permissions, diffs), then the code.
- Small increments; run the tests after each one.
- No new dependencies beyond the approved plan without asking me.
- Never change or delete an existing test, or weaken a security rule, to get green.
  If a test seems wrong, stop and explain.
- If something surprises you (an API differs from the docs, a test fails for an unclear
  reason), stop and tell me instead of working around it.
```

**Your plan checklist (2 minutes):**

- Does it cover everything in the step, and nothing from later steps?
- Are its assumptions true? Correct any that are not.
- Did it cite real docs and pin exact versions?
- Do the tests actually prove the "Done when" check, including failure cases?
- Does it break any architecture rule in CLAUDE.md (fail closed, credentials, masking,
  ownership, 404 not 403...)?

### Prompt 2: verify with evidence

```text
Verify this step that you build:
1. Run the test suite and lint for every part you touched (backend and/or
   frontend). Paste the actual output summary, not a description of it.
2. Run the step's "Done when" check. Paste the actual output.
3. Give me the exact commands to run the "Done when" check myself, so I can confirm it.
4. Review your own diff against the step's plan text and the architecture rules in
   CLAUDE.md. List every deviation, shortcut, TODO, skipped test or hard-coded value, and evaluate if needs fixing.
5. List anything that is not finished or not working. "Done" only means the check passed.
6. Explain the two most important design choices in this step in plain language, as I
   would in an interview, write it in the design choise guide md file (create one if necessary).
7. state clarely if any of you finding needs fixing and my input to be able to fix them
```

If anything in steps 4-5 is a real problem, ask Claude to fix it (or use Prompt D),
then paste Prompt 2 again.

### Prompt 3: wrap up and commit

```text
 if no issues to fix, create pr, but make sure you have done below steps:
1. Update PROGRESS.md: tick the step, add anything partial or deferred to Known issues,
   and set Current focus to the next unticked item.
2. Tick the requirement IDs this step fully verified in docs/REQUIREMENTS.md. Only tick
   ones whose verification actually passed.
3. Add each real decision made in this step to docs/decisions.md (next D-number, with
   the reason and the alternative rejected). Skip it if there were none.
4. Show me git status, then commit everything with a Conventional Commits message
   (e.g. "feat(backend): step 8 classifier"). create pr.
```

---

## Part 3: Special prompts

### Prompt S: spike (prove a risky API before building on it)

Spikes come before steps 6 (MCP mounting), 7 and 18 (Claude Code hook), 10 (Neon
branching) and 14 (LangGraph interrupt and resume). The questions each spike must answer
are in that step's text in the backend plan. The hook spike also answers: matcher tool
names (Read, Grep, Glob, MultiEdit, NotebookEdit), the default hook timeout and whether a
timeout fails open, `permissions.deny` path syntax, whether hooks are snapshotted at
startup, `disableAllHooks` and settings precedence, and behaviour in bypass-permissions mode.

Use a fresh session, normal mode (not plan mode):

```text
The first unticked item in PROGRESS.md is a spike. Read CLAUDE.md, the plan text for the
step it comes before, and the spike notes in docs/WORKFLOW.md Part 3. List the questions
this spike must answer and wait for my OK.

Then write throwaway code in spikes/<name>.py (not production code; no imports from
backend/app). Use the current official docs and cite them. Use only dev resources
(local Docker Postgres, the blastproof-company-dev Neon project). Run it and paste the
real output.

Finish with:
1. Each question and its answer, backed by the real output.
2. Everything that differs from what our plan assumes, and the plan edit you propose.
After I agree, record the findings in docs/decisions.md, make the plan edits, tick the
spike in PROGRESS.md and commit.
```

### Prompt G: gate audit (end of each stage, fresh session)

```text
You are a skeptical senior engineer reviewing someone else's work. The first unticked
item in PROGRESS.md is a GATE AUDIT (or the FINAL AUDIT). Read CLAUDE.md,
docs/REQUIREMENTS.md, PROGRESS.md and the plan text for every step in this stage (for the
final audit: both MVP done checklists). Then inspect the code itself. Do not trust
PROGRESS.md or the ticks in REQUIREMENTS.md; verify against the code and tests.

Report, ranked by severity:
1. Requirements ticked as done that are not actually implemented or tested.
2. Requirements for this stage that are missing entirely.
3. Violations of the architecture rules in CLAUDE.md.
4. Security problems (secrets, authorization gaps, missing masking, fail-open paths).
5. Missing or weak tests, especially for failure cases.
6. Whether the gate's check (written next to it in PROGRESS.md) passes. Show the proof.
Run the full tests, lint and evals, and paste the results. Do not fix anything yet.
```

Then fix the findings one at a time (ask "fix finding 1", using Prompt D for bugs), and tick
the gate only when its check passes. If an audit finds a lot, fix it in a new session.

### Prompt D: debug (when something breaks)

```text
Something is broken: <WHAT YOU SEE, plus the full error>.
1. Reproduce it and show me the output.
2. List possible causes, most likely first, and how you would confirm each.
3. Confirm the cause before changing anything.
4. Make the smallest fix, add a test that would have caught it, and run all tests.
Do not change unrelated code and do not weaken tests or security rules to get green.
```

### Prompt R: where am I? (after a break, a crash, or a long session)

```text
Read PROGRESS.md and run git status and git diff --stat. Tell me:
1. Which step I'm on, and whether it looks not started, half built, or built but not
   verified or committed.
2. What is uncommitted and whether it looks complete.
3. Exactly which prompt from docs/WORKFLOW.md I should paste next.
Do not change anything.
```

### Prompt E: explain (so you can defend every line in interviews)

```text
Explain <FILE OR FUNCTION> to me as if I'm preparing for an interview: what it does,
why it's designed this way, what alternatives exist, and what breaks if it fails.
Then ask me three questions an interviewer might ask about it, and check my answers.
```

---

## Part 4: Rules that prevent most bugs and hallucinations

- **Fresh session per step.** Long sessions drift and forget the plan. If a session
  gets long mid-step, `/clear` and paste Prompt R.
- **Evidence, not claims.** Accept "done" only with real test and command output.
- **Docs before code** for fast-moving libraries; spike before building on them.
- **Tests first** for anything with logic or security.
- **Never let tests or security rules be weakened** to get green.
- **Commit after every green step**, so any bad change is one `git revert` away.
- **"Should work" means run it. "I assumed" means check it.**
- **Two strikes rule:** if the same bug survives two fix attempts, stop, `/clear`, and
  start Prompt D from scratch.
- **Read every diff.** If you can't explain a change, use Prompt E before committing.
- **Plan wrong? Fix the plan, not just the code.** Any change goes into the plan file
  and `docs/decisions.md`, so later sessions see it.

---

## Reference: prompts already used

### Prompt 0A: plan audit (done)

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

### Prompt 0B: requirements list (done)

```text
Read all three plans in docs/. Create docs/REQUIREMENTS.md: a checklist of every
concrete requirement, with IDs (BE-01, FE-01, SEC-01, EVAL-01...). Each line:
ID, one-sentence requirement, the plan section it comes from, the build step that
implements it, and how it is verified. Include every "Done when" check, every
architecture rule in CLAUDE.md, and every item in the MVP done checklists.
Do not invent requirements; if something is implied but not stated, list it
separately at the end under "Implied, needs confirmation".
```

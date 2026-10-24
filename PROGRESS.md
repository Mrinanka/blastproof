# Progress

## Current focus
Stage 0: setup. Plan audit decisions applied (D-001 to D-016), then a follow-up review of the
applied plan fixed (D-017 to D-028), both on 2026-10-24. Next: owner reviews the plan diff and
commits, then outside setup (now three Neon projects), then Prompt 0B (requirements list).

## Known issues
- Accepted dev limitation (D-002): on the dev machine, the agent could reach the dev `.env` by a
  route the hook and `permissions.deny` miss. It only unlocks the dev company project. Full
  isolation is verified on the deployed demo.
- Open until spikes run: the latency target for previewed writes and whether asyncpg prepared
  statements work through Neon's pooled endpoint (Neon spike; the plan assumes the direct
  endpoint), the hook timeout behaviour (hook spike), and the Claude Code MCP tool timeout vs the
  50 s long poll (MCP spike).
- Landing page CSP allows `'unsafe-inline'` scripts (D-028); verify the real headers in F1.

## Stage 0: setup
- [ ] Outside setup done (backend plan section 2, frontend plan section 2)
- [ ] Repo files committed (CLAUDE.md, docs/, PROGRESS.md)
- [x] Plan audit done (Prompt 0A): `docs/plan-audit.md`
- [x] Plans fixed with the audit decisions: `docs/audit-response.md`, `docs/decisions.md`
- [ ] docs/REQUIREMENTS.md created and reviewed (Prompt 0B)

## Stage 1: foundation
- [ ] 1. Repo and skeleton
- [ ] 2. CI
- [ ] 3. Demo company database and eval scenarios
- [ ] 4. Blastproof data model
- [ ] 5. Auth
- [ ] Spike: MCP mounting
- [ ] 6. MCP server
- [ ] Spike: Claude Code hook
- [ ] 7. Remove direct access
- [ ] GATE AUDIT: Claude Code reads the demo DB only through Blastproof

## Stage 2: judging parts
- [ ] 8. Classifier
- [ ] 9. Policy rules
- [ ] Spike: Neon branching
- [ ] 10. Neon client and shadow executor
- [ ] 11. Knowledge and RAG, swappable vector store
- [ ] 12. Impact engine
- [ ] 13. Risk judge, pluggable
- [ ] GATE AUDIT: exact diffs and correct retrieval

## Stage 3: the flow + dashboard start
- [ ] Pull forward: /v1/auth/me, GET /v1/actions, GET /v1/actions/{id}
- [ ] Spike: LangGraph interrupt and resume
- [ ] 14. LangGraph workflow
- [ ] 15. Workflow runner
- [ ] Pull forward: seed-demo-actions
- [ ] 16. Commit
- [ ] 17. Wire up query_database
- [ ] F1. Scaffold
- [ ] F2. Typed API client
- [ ] F3. Proxy and login
- [ ] F4. Layout and navigation
- [ ] F5. Shared components
- [ ] GATE AUDIT: Gate 1, Replit case held for review and approvable

## Stage 4: finish backend + core dashboard
- [ ] 18. Claude Code hook
- [ ] 19. Dashboard API (remaining routes)
- [ ] 20. Bootstrap, dev commands and README
- [ ] Evals in CI, security pass, observability (backend plan sections 12-14)
- [ ] Backend deployed to Fly.io (backend plan section 15)
- [ ] F6. Review queue
- [ ] F7. Change request page
- [ ] F8. History
- [ ] F9. Overview
- [ ] GATE AUDIT: Gate 2, evals green and backend deployed

## Stage 5: complete and ship
- [ ] F10. Knowledge
- [ ] F11. Agents, users and rules
- [ ] F12. Polish
- [ ] F13. Landing page
- [ ] Dashboard deployed to Vercel
- [ ] FINAL AUDIT against both MVP done checklists
- [ ] Demo video recorded

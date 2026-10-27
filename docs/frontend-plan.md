# Blastproof: Frontend MVP Plan

Oct 24, 2026 · @Mrinanka Dhar

This plan builds the Blastproof dashboard, where users review and approve their own agents' actions and admins manage users and knowledge, and connects it safely to the FastAPI backend from the backend MVP plan.

## 1. MVP scope

One Next.js dashboard where reviewers see pending agent actions within seconds, read the plain-language impact, and approve or reject them; where every user creates their own agent keys; and where admins manage users and knowledge. It holds no business logic: every decision and every piece of data comes from the backend.

**In the MVP:**

- A public landing page at `/` that explains the product to visitors who are not logged in; every dashboard page requires login. Email and password login, with the session kept in a secure cookie.
- Overview page with live counts (pending reviews, decisions today, blocked actions, preview time, LLM cost).
- Review queue that refreshes every 5 seconds, with a count badge in the navigation.
- Change request page: summary, SQL, impact numbers, sample rows, judge result, rules matched, audit timeline, and approve or reject (with a required reason).
- History page with filters and pagination.
- Knowledge page: list sources, upload a document, delete one, rescan the schema.
- Agents page: create an agent (key shown once, with a ready-to-copy Claude Code setup command), deactivate an agent.
- Rules page: read-only view of the active policy rules.
- Personal view: each user sees and decides only their own agents' actions and creates their own agent keys; admins see everything and manage users and knowledge.

**Not in the MVP:** editing rules in the UI, undo button, observe mode, Slack notifications, live updates through WebSockets, charts beyond simple counts, multi-company accounts.

## 2. Outside setup

The frontend needs only one new account; everything else is reused from the backend setup.

**Accounts:**

- [ ] **Vercel** (vercel.com): you already have it for AI Gateway. The free Hobby plan is enough to host the dashboard for a personal portfolio project. Connect it to your GitHub account.
- [ ] **GitHub:** same `blastproof` repo; the dashboard lives in a new `frontend/` folder.

**Tools on your machine:**

- [ ] Node.js (current LTS, 24) and npm, which comes with it (D-061, D-062).
- [ ] A browser with React Developer Tools and the TanStack Query devtools (installed as a package, shown in development only).
- [ ] Playwright browsers, installed once with `npx playwright install` from `frontend/`, right after `@playwright/test` is added there, so the browsers match the pinned Playwright version.

## 3. Tech stack and libraries

A mainstream React stack with as few moving parts as possible: no global state library, no custom design system, no WebSockets.

| Purpose | Library / tool | Notes |
| --- | --- | --- |
| Framework | Next.js (App Router) + React + TypeScript | Pages plus a small server-side proxy in one app |
| Styling | Tailwind CSS | Utility classes; comes with `create-next-app` |
| Components | shadcn/ui | Copy-in components (table, dialog, badge, sonner for toasts, field); you own the code. shadcn is on the Radix base (`-b radix`). `toast` is Base UI-only, so we use `sonner`; `form` is legacy, so we use `field` (D-215) |
| Server data | TanStack Query | Fetching, caching, 5-second polling, mutations |
| API types | openapi-typescript | Generates TypeScript types from the backend's OpenAPI schema |
| API client | openapi-fetch | Tiny typed `fetch` wrapper that uses those types |
| Forms | react-hook-form + zod | Login, reject reason, create agent, upload |
| Dates | date-fns | "3 minutes ago" and local time display |
| Icons | lucide-react | Default icon set for shadcn/ui |
| Unit tests | Vitest + React Testing Library | Components and helpers |
| End-to-end tests | Playwright | Real browser against the real local backend |
| Quality | ESLint + Prettier + `tsc --noEmit` | Run in CI |
| Hosting | Vercel | Built by the makers of Next.js; free Hobby plan |

**State rule:** server data lives only in TanStack Query; filters live in the URL query string; everything else is local component state. That covers every page in this plan.

## 4. How the frontend and backend connect

The dashboard uses the "backend for frontend" pattern: the browser only ever talks to the Next.js app on its own domain, and a small server-side proxy inside Next.js calls the FastAPI backend. The backend needs no changes for this; it keeps checking a Bearer JWT on every request.

&#91;embedded content: How the dashboard reaches the backend · browser, Next.js proxy, FastAPI\]

**A request, step by step:**

1. **Login.** The login form posts to `/api/auth/login` on the dashboard. That route calls the backend's `/v1/auth/login`, receives the JWT, and stores it in an httpOnly cookie. The browser gets back only the user's name and role.
2. **Navigation.** `src/proxy.ts` (Next.js 16 renamed `middleware.ts` to `proxy.ts`; verify in F1) runs before every page under `/dashboard` (the landing page and /login stay public); with no session cookie, it redirects to `/login`. To avoid a name clash, this plan calls `src/proxy.ts` the **auth proxy** and the catch-all route handler below the **BFF route**.
3. **Data.** A page calls, say, `api.GET("/v1/actions")`, which the typed client sends to `/api/blastproof/v1/actions`. The browser attaches the cookie automatically.
4. **BFF route.** The route handler reads the cookie, calls `BLASTPROOF_API_URL + /v1/actions` with `Authorization: Bearer <jwt>`, and returns the backend's status and JSON unchanged.
5. **Backend.** FastAPI checks the JWT and the role, as it would for any client, and answers.
6. **Expired or invalid session.** The backend answers 401; the proxy passes it through; the dashboard's global error handler sends the user to `/login`.

**Why this pattern:** the token is never readable by JavaScript in the page, the browser never calls another domain (so no CORS), and the backend's address stays a server-side secret. The cost is one extra short hop, which is negligible when Vercel's region matches Fly.io's.

## 5. The API contract

The backend's OpenAPI schema is the single contract between the two apps. TypeScript types are generated from it, so a renamed field breaks the frontend build instead of breaking in a reviewer's browser.

**Endpoints the dashboard uses:** (all under the backend's `/v1`)

| Area | Method and path | Who |
| --- | --- | --- |
| Auth | POST `/auth/login`, GET `/auth/me` | anyone / signed in |
| Actions | GET `/actions` (filters: status, agent\_id, table, from, to, limit, offset), GET `/actions/{id}` | your own only (admin: all) |
| Decisions | POST `/actions/{id}/approve`, POST `/actions/{id}/reject` (body: reason) | the action's owner or an admin |
| Stats | GET `/stats` | your own only (admin: all) |
| Rules | GET `/policies` (read-only) | signed in |
| My agents | GET `/agents`, POST `/agents`, POST `/agents/{id}/deactivate` | your own (admin: all) |
| Knowledge | GET `/knowledge`, POST `/knowledge` (multipart file upload), DELETE `/knowledge/{id}` | read: signed in; write: admin |
| Systems | GET `/systems`, POST `/systems/{id}/scan` | admin |
| Users | GET `/users`, POST `/users` | admin |

**Conventions both sides follow:**

- JSON everywhere except the upload. IDs are UUID strings. Timestamps are ISO 8601 in UTC; the dashboard converts to local time.
- List endpoints return `{"items": [...], "total": n, "limit": n, "offset": n}`.
- Every error, including validation errors, uses `{"error": {"code": "...", "message": "..."}}`.
- Status codes: 401 not signed in (dashboard sends you to login), 403 wrong role, 404 not found, 409 already decided (message names who decided), 422 invalid input.
- Every backend route declares a Pydantic response model, so the generated types are exact.

**Typed client:**

`npm run gen:api` reads the backend's `/openapi.json` and writes `src/lib/api/schema.d.ts`. The client is two lines:

```ts
import createClient from "openapi-fetch";
import type { paths } from "./schema";

export const api = createClient<paths>({ baseUrl: "/api/blastproof" });

// usage: fully typed path, params and response
const { data, error } = await api.GET("/v1/actions/{action_id}", { params: { path: { action_id } } });
```

The browser always calls `/api/blastproof/...` on the dashboard's own domain; section 4 explains how that reaches the backend.

## 6. Project structure

The dashboard lives in `frontend/`, next to `backend/` in the same repo. Pages are thin; data fetching lives in hooks; the proxy is the only code that knows the backend's address.

```text
blastproof/
  backend/                   # FastAPI (backend plan)
  frontend/
    src/
      app/
        page.tsx             # public landing page at /
        login/page.tsx       # public
        dashboard/           # everything here requires a session
          layout.tsx         # sidebar, user menu, review badge
          page.tsx           # Overview
          review/page.tsx
          actions/[id]/page.tsx
          history/page.tsx
          knowledge/page.tsx
          settings/agents/page.tsx
          settings/rules/page.tsx
          settings/users/page.tsx   # admin only
        api/
          auth/login/route.ts   # sets the session cookie
          auth/logout/route.ts  # clears it
          blastproof/[...path]/route.ts  # BFF route: forwards to the backend
      proxy.ts               # auth proxy (Next 16): /dashboard without a cookie -> /login
      components/
        ui/                  # shadcn/ui components
        landing/             # hero, how-it-works, results, FAQ sections
        actions/             # status badge, impact card, SQL block, timeline
      hooks/                 # useActions, useAction, useStats, useMe, ...
      lib/
        api/schema.d.ts      # generated, never edited by hand
        api/client.ts        # openapi-fetch client
        format.ts            # dates, numbers, money
    public/                  # demo video, Open Graph image, screenshots
    tests/                   # Vitest unit tests
    e2e/                     # Playwright tests
    .env.example
    package.json
```

## 7. Pages and what each shows

Ten pages: two public, two admin-only, and six that show each user only their own data. The change request page is the heart of the product and the one to polish first; every other page is a list or a form.

| Page | Route | Shows | Data (refresh) |
| --- | --- | --- | --- |
| Landing (public) | `/` | Product story for visitors: pitch, problem, how it works, demo, results, Log in button | None; static page |
| Login (public) | `/login` | Email and password form; error message on failure | POST `/api/auth/login` |
| Overview | `/dashboard` | Your count cards: pending reviews, decisions today, blocked today, average preview time, LLM cost today ("today" is the UTC day, labelled on the cards); your 10 latest actions | `/stats`, `/actions?limit=10` (every 15 s) |
| Review queue | `/dashboard/review` | Your agents' pending actions, oldest first: agent, intent, headline, risk badge, waiting time | `/actions?status=awaiting_review` (every 5 s) |
| Change request | `/dashboard/actions/[id]` | See the layout below; "not found" if it is not yours | `/actions/{id}` (every 5 s while pending) |
| History | `/dashboard/history` | Your actions, with filters for status, agent, table and dates; 25 per page | `/actions` with filters from the URL |
| My agents | `/dashboard/settings/agents` | Your agents, key prefix, active state; create and deactivate | `/agents` |
| Rules | `/dashboard/settings/rules` | Active rules: name, conditions, outcome, reason (read-only) | `/policies` |
| Knowledge (admin) | `/dashboard/knowledge` | Sources with chunk counts; upload, delete, rescan schema | `/knowledge`, `/systems` |
| Users (admin) | `/dashboard/settings/users` | All users and roles; create a user with an initial password | `/users` |

**Change request layout, top to bottom:**

1. Header: status and risk badges, agent name, how long ago, and who decided (if decided).
2. Plain-language impact: the headline in large text, key points and concerns underneath.
3. Decision bar (only while pending, shown when the backend's can\_decide flag is true, meaning you own the action or are an admin): Approve, and Reject, which opens a dialog requiring a reason. After a decision, the page refreshes; a 409 shows "already decided by ...".
4. Agent's intent, then the SQL in a monospace block (`sql_display`). When values were pinned, show the original next to it and list the pinned values, e.g. `now()` → `2026-10-24 12:00 UTC`. If this action retries a rejected one (`retry_of`), link to it.
5. Impact numbers: rows changed per table (cascades marked), the breakdown by important columns, e.g. plan: free 48,201, pro 9, enterprise 3, and the derived groups, e.g. paying: 12.
6. Sample affected rows (sensitive columns arrive already masked from the backend).
7. Judge result: recommendation, confidence, reason, which judge ran.
8. Rules that matched, with their reasons.
9. Knowledge used: the chunks that informed the summary (source, table, first line).
10. Audit timeline: every event with its time.

All of these fields come from the backend's `ActionDetail` schema (backend plan section 6).

**Agents page detail:** after creating an agent, show the key once with a copy button, plus the exact `claude mcp add` command and the `BLASTPROOF_AGENT_KEY` line for the hook, exactly as returned by `POST /v1/agents` (the backend builds them from its `PUBLIC_BASE_URL`; the dashboard never knows or exposes `BLASTPROOF_API_URL`). Also link to the README's hook install steps. This turns setup into a copy-paste step and makes a strong demo moment.

## 8. Build steps A: setup, contract, auth, layout

Steps F1 to F5 produce an empty dashboard you can log in to, talking to the real backend through the proxy. Each step ends with a test you can run.

1. **F1. Scaffold.** In the repo root, `npx create-next-app@latest frontend` with TypeScript, Tailwind, ESLint, App Router and the `src/` folder. Run `npx shadcn@latest init`, then add the components you need (button, card, table, badge, dialog, input, textarea, field, sonner, dropdown-menu, sidebar, skeleton), with `npx shadcn@4.22.1 init -b radix`. Add Prettier and a `typecheck` script. Check the current Next.js docs for the `proxy.ts` file convention and the CSP guide before F3.
   *Done when:* `npm run dev` shows the starter page at `http://localhost:3000`.
2. **F2. Typed API client.** Add `openapi-typescript` and `openapi-fetch`. A `gen:api` script reads `http://localhost:8000/openapi.json` into `src/lib/api/schema.d.ts` (committed). `src/lib/api/client.ts` creates the client with base URL `/api/blastproof`. Set up TanStack Query's provider in the root layout, with `refetchOnWindowFocus` on and polling paused while the tab is hidden.
   *Done when:* hovering `api.GET("/v1/actions/{action_id}")` in your editor shows the backend's real response type.
3. **F3. Proxy and login.** Build the three route handlers and the auth proxy (`src/proxy.ts`) from section 4. The login route posts to the backend, puts the JWT in an httpOnly cookie, and returns only the user. The proxy forwards method, path, query string, body and content type, adds the `Authorization` header from the cookie, drops incoming cookies, and returns the backend's status and body unchanged. It rejects any POST or DELETE whose `Origin` is not the dashboard's own address (section 11). A global TanStack Query error handler sends any 401 to `/login`. Add a `useMe()` hook on `/v1/auth/me` for the user's name and role.
   *Done when:* you can log in, the token never appears in browser JavaScript or local storage, and deleting the cookie sends you back to `/login`.
4. **F4. Layout and navigation.** The `app/dashboard/layout.tsx` layout (a real URL segment, not a route group) has a sidebar (Overview, Review with a count badge, History, Knowledge, and Settings with Agents, Rules and, for admins, Users) and a user menu with logout. The badge reads `total` from `/v1/actions?status=awaiting_review&limit=1` every 5 seconds. Admin-only links (Knowledge, Users) are hidden from members (the backend still enforces roles).
   *Done when:* creating a pending action with `seed-demo-actions --add-pending` makes the badge change within 5 seconds.
5. **F5. Shared components.** `StatusBadge`, `RiskBadge`, `TimeAgo`, `SQLBlock`, `NumberBreakdown`, `AuditTimeline`, `DataTable` with pagination, and standard loading (skeleton), empty and error states. Number and date formatting live in `lib/format.ts`.
   *Done when:* Vitest tests pass for the badges and formatting helpers.

## 9. Build steps B: the pages

Steps F6 to F12 build the pages in order of importance, so the demo-critical flow works first.

6. **F6. Review queue.** `useActions({status: "awaiting_review"})` polling every 5 seconds; rows link to the change request page.
   *Done when:* a pending action added with `seed-demo-actions --add-pending` appears without a page refresh.
7. **F7. Change request page.** The layout from section 7. Approve and reject are TanStack Query mutations; on success, refresh this action, the queue and the badge. Reject opens a dialog with a required reason (zod: at least 10 characters). A 409 shows a toast naming who already decided. The decision bar shows only while the action is pending and the backend's can\_decide flag is true.
   *Done when:* approving and rejecting work end to end against the local backend, and two browser windows approving the same action show the 409 message in the second.
8. **F8. History.** Filters (status, agent, table, date range) are stored in the URL, so a filtered view can be bookmarked or shared. Server-side pagination, 25 rows per page.
   *Done when:* changing filters updates the URL and the back button restores the previous view.
9. **F9. Overview.** Count cards from `/v1/stats` and the latest 10 actions, refreshing every 15 seconds.
   *Done when:* numbers match a manual SQL count on Blastproof's database.
10. **F10. Knowledge.** Source list; upload a `.md` or `.txt` file (multipart through the BFF route) with a size limit of 1 MB and an optional list of tables it applies to; delete with a confirm dialog; "Rescan schema" button. The page is admin-only; members never see it.
    *Done when:* a document uploaded with the table `users` appears in the "Knowledge used" list of the next action that touches `users` (checked with `seed-demo-actions --add-pending`, so no Neon or real LLM is needed).
11. **F11. Agents and rules.** Create agent: show the key once with a copy button and the pre-filled Claude Code setup lines; deactivate with confirmation. Users page (admin only) lists users and creates new ones with an initial password. Rules page renders `/v1/policies` as a read-only table.
    *Done when:* a newly created key works in Claude Code by copy-pasting the shown commands.
12. **F12. Polish.** Keyboard focus and labels on every control, readable color contrast, dark mode (shadcn supports it), page titles, and a friendly 404 page. Check every page at a narrow width so it works on a laptop split screen.
    *Done when:* a Lighthouse accessibility check on the change request page scores 90 or above.
13. **F13. Landing page** (`/`, public). A static, server-rendered page with no login and no API calls, built like a real product's marketing page. Sections, top to bottom: hero (name, one-line pitch, "Watch the demo", "View on GitHub", "Log in"); the problem (the real incidents, one line each, linked to their sources); how it works in four steps (intercept, preview on a copy, explain the impact, approve or block); a screenshot of the change request page; what makes it different (outcomes, not commands; real shadow execution, not predictions; any MCP agent plus a Claude Code hook); results from your actual evals; the architecture diagram; security; FAQ; footer. Add page metadata, an Open Graph image for link previews, and a sitemap. Only publish numbers you have measured.
    *Done when:* a logged-out visitor sees the landing page, "Log in" leads to `/login`, Lighthouse scores 90 or above for performance, accessibility and SEO, and the link preview looks right when shared on LinkedIn.

## 10. Configuration

The dashboard needs only three variables, all server-side. None start with `NEXT_PUBLIC_`, so none are ever sent to the browser. Locally they live in `frontend/.env.local` (git-ignored, with a committed `.env.example`); in production they are Vercel environment variables.

| Variable | Example | Used for |
| --- | --- | --- |
| `BLASTPROOF_API_URL` | `http://localhost:8000` locally, `https://<your-app>.fly.dev` in production | Where the proxy and login route send requests |
| `SESSION_COOKIE_NAME` | `blastproof_session` | Name of the httpOnly cookie |
| `SESSION_MAX_AGE_SECONDS` | `28800` | Cookie lifetime; matches the backend's 8-hour JWT expiry |

The backend URL appears in exactly one file, the proxy, plus the login route that uses the same helper.

## 11. Security for the dashboard

The dashboard can approve changes to production data, so a stolen session is as dangerous as a stolen database password. These rules keep it safe without extra infrastructure.

- **Token never in the browser's JavaScript:** the JWT lives only in an httpOnly cookie (`Secure` in production, `SameSite=Lax`, `Path=/`), so a script injected into the page cannot read it. Never use local storage for tokens.
- **Cross-site request protection:** `SameSite=Lax` stops other websites from sending your cookie with form posts. In addition, the proxy rejects any state-changing request (POST, DELETE) whose `Origin` header is not the dashboard's own address.
- **No CORS needed:** the browser only talks to the dashboard's own domain; the proxy talks to the backend server to server. Leave the backend's `CORS_ORIGINS` empty in production.
- **The backend is the real gatekeeper:** hiding buttons by role is for convenience only; every role check is enforced again by the backend.
- **Untrusted text is shown as text:** SQL, intents, uploaded documents and sample rows are rendered as plain text (React escapes it by default). Never use `dangerouslySetInnerHTML` and never render Markdown from uploads as HTML.
- **Masked data:** sensitive columns are masked by the backend before they reach the dashboard.
- **Security headers:** `X-Frame-Options: DENY` and `Referrer-Policy` on every page in `next.config`. A nonce-based Content Security Policy forces dynamic rendering, so apply it to `/dashboard/*`, `/login` and `/api/*` only; the landing page stays static. The App Router emits inline scripts even on static pages (`self.__next_f.push`), so the landing page's CSP allows `script-src 'self' 'unsafe-inline'`. That is acceptable there because it renders no user or agent content and loads no third-party scripts. Verify the exact headers in F1, and try a hash-based CSP if the build makes it practical.
- **Logout:** clears the cookie; tokens also expire after 8 hours.

## 12. Testing

A few unit tests for the pieces with logic, and one end-to-end test for the flow that matters most. No snapshot tests and no mocking the whole backend.

- **Unit (Vitest + React Testing Library):** formatting helpers, status and risk badges, the reject dialog's validation, the proxy's header handling (cookie in, Authorization out, incoming cookies dropped, bad Origin rejected).
- **Type safety:** `npm run typecheck` catches any mismatch with the generated API types.
- **End-to-end (Playwright):** against the real local backend with demo data from the backend's `seed-demo-actions` command:
  1. Log in as the first demo user.
  2. Open the review queue and click the seeded pending action.
  3. Check that the headline and impact numbers are shown.
  4. Reject it with a reason.
  5. Find it in History with status rejected and your name as the decider.
  6. Log out, log in as the second demo user, and confirm the action is not in their queue and its URL shows "not found".
- **Manual check each week:** run the full demo with Claude Code creating a real pending action, and review it in the dashboard.

## 13. Deployment and CI

The dashboard deploys to Vercel straight from GitHub; CI makes sure only green code reaches `main`, and a contract check makes sure the two apps agree.

**Deploy to Vercel (once the backend is on Fly.io):**

1. In Vercel, import the `blastproof` repo and set the **root directory** to `frontend/`. Vercel detects Next.js automatically.
2. Add the three environment variables from section 10, with `BLASTPROOF_API_URL` pointing at the Fly.io backend.
3. Set the functions region to match your Fly.io and Neon region, so the proxy's hop to the backend is short.
4. Deploy. Every merge to `main` then deploys automatically, and every pull request gets a preview URL.
5. Create your first admin with the backend's bootstrap command, log in, and run the demo end to end.

Preview deployments use the same backend as production. That is acceptable for a one-person portfolio project because login is still required; add a staging backend in phase 2.

**CI (GitHub Actions, `frontend` job on every pull request):**

- Install with `npm ci` (cached), then `lint`, `typecheck`, `test` (Vitest) and `build`.
- **Contract check:** the backend writes its OpenAPI schema to a file with `export-openapi` (no server or database needed); the frontend regenerates `schema.d.ts` from it; the job fails if the file changed. This catches any backend change the frontend hasn't caught up with.
- **End-to-end:** a separate workflow, run nightly or by hand, starts Postgres and the backend, runs `seed-demo-actions`, starts the dashboard and runs Playwright.
- Protect `main` in GitHub so merging requires both the backend and frontend jobs to pass.

## 14. Schedule

Adding the dashboard moves the full MVP from four weeks to about five. The dashboard starts in week 3, as soon as login, the actions list and demo data exist on the backend.

&#91;embedded content: Combined schedule · backend and dashboard lanes over 5 weeks\]

To make that start possible, pull small backend pieces forward in week 3: the auth routes (`/v1/auth/login`, `/v1/auth/me`) and GET `/v1/actions` and `/v1/actions/{id}` at the start of the week, then the approve and reject routes and the `seed-demo-actions` command right after backend step 15, because they need its `resume()` and the real graph (step 17's check and Gate 1 also use approve and reject). F1 to F3 and F5 don't need seeded data; F4's check does, so it finishes after the seed command exists. The rest of step 19 can follow later in the week. Week 5 doubles as buffer: if the backend slips, it absorbs it. The landing page (F13, about two days) also lands in week 5, once real eval numbers and a demo recording exist.

## 15. Done checklist and what is deferred

The dashboard MVP is done when every box is ticked on the deployed Vercel site, connected to the deployed backend.

- [ ] Logged-out visitors see the landing page at /, and every /dashboard page redirects them to login. Login works; the token never appears in browser JavaScript or local storage; logout and expiry send you to login.
- [ ] A pending action created by Claude Code appears in the review queue and the nav badge within 5 seconds.
- [ ] The change request page shows the headline, impact numbers, masked sample rows, judge result, matched rules and audit timeline.
- [ ] Approve and reject work; reject requires a reason that reaches the agent; a second approval shows the 409 message.
- [ ] History filters live in the URL and survive back and forward navigation.
- [ ] Each user sees only their own agents' actions and manages their own agent keys; admins see everything and manage users and knowledge; members cannot see admin controls, and the backend refuses them anyway.
- [ ] A new agent can be connected to Claude Code by copy-pasting the setup lines shown once.
- [ ] Lint, typecheck, unit tests, build and the contract check pass in CI; the Playwright flow passes.
- [ ] Lighthouse accessibility score of 90 or above on the change request page.

**Deferred:** rule editing in the UI, undo button, observe-mode toggle, Slack notifications, WebSockets or server-sent events, charts and trends, a
staging backend for preview deployments, multi-company accounts, single sign-on, server-side logout (token revocation; logout clears the cookie and the JWT expires after 8 hours, D-041), "today" counts in the viewer's local day (D-051).

## 16. Backend and spec changes this plan depends on

Writing this plan surfaced gaps between the two apps; all of them are now fixed in the backend plan and the spec.

| Gap found | Fix made |
| --- | --- |
| The dashboard had no way to learn the signed-in user's role | Added GET `/v1/auth/me` |
| Pages needed endpoints the backend plan did not list | Added GET `/v1/stats` to the API table, plus GET `/v1/policies`, GET `/v1/systems`, GET `/v1/agents`, POST `/v1/agents/{id}/deactivate` and DELETE `/v1/knowledge/{id}` |
| Roles only covered approve and reject | Systems, knowledge changes and users are admin-only; every signed-in user creates and deactivates their own agents |
| Upload format was unspecified | POST `/v1/knowledge` is a multipart upload of a .md or .txt file up to 1 MB |
| No shared shapes for lists, dates or validation errors | Conventions added: `{"items", total, limit, offset}` lists, ISO 8601 UTC timestamps, one error shape including validation errors, a 409 that names who decided |
| Types could drift between the apps | Every route declares a response model; new `export-openapi` command feeds the frontend's contract check in CI |
| No data to build or test the dashboard before the full pipeline works | New `seed-demo-actions` command creates actions in every status without real Neon or a real LLM (it runs the real graph with fakes, so pending actions can be approved) |
| The dashboard guessed the action detail shape (plan audit) | `ActionDetail` schema written into backend plan section 6 |
| The setup lines needed the backend's public URL (plan audit) | `POST /v1/agents` returns `mcp_url` and the setup lines |
| The backend plan configured CORS for the browser | Not needed: the browser only talks to the dashboard, whose proxy calls the backend server to server; `CORS_ORIGINS` stays empty in production |
| The spec assumed OAuth (Auth.js) for dashboard login | Spec now says email and password with a backend-issued JWT in an httpOnly cookie; single sign-on later |
| The repo layout and deployment steps had no place for the dashboard | `frontend/` added to the repo structure; backend deployment now points to this plan; spec lists the dashboard on Vercel |
| Every logged-in user saw every action, and agents had no owner | Ownership model: agents belong to a user, actions store the owner's user\_id, every query goes through one visibility rule, and other users' actions return 404; roles simplified to admin and member; admin-only Users endpoints and page added |

One knock-on effect for scheduling: because `seed-demo-actions` exists, dashboard work does not have to wait for the backend's full pipeline (section 14).

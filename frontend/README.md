# Blastproof dashboard

Next.js 16 (App Router), TypeScript strict, Tailwind 4 and shadcn/ui on Radix. The plan is
`docs/frontend-plan.md`; the dashboard holds no business logic.

## Commands

Run from `frontend/` with Node 24 (`.nvmrc`).

- `npm ci` - install the exact locked versions
- `npm run dev` - dashboard on http://localhost:3000
- `npm run lint && npm run format:check && npm run typecheck && npm test` - checks
- `npm run build` - production build (needs network for the Geist font)
- `npm run format` - format everything with Prettier

## Environment

Copy `.env.example` to `.env.local`. Only server-side code reads these; never use a
`NEXT_PUBLIC_` name.

- `BLASTPROOF_API_URL` - the backend, e.g. `http://localhost:8000`
- `SESSION_COOKIE_NAME`, `SESSION_MAX_AGE_SECONDS` - the login cookie (F3)

# Deploying the dashboard to Vercel

The dashboard is the Next.js app in `frontend/`. Vercel builds it from the `release` branch, in the
same region as the Fly backend (`iad`), and serves `blastproof.in`. The browser only ever talks to the
dashboard; the dashboard's server talks to `https://blastproof.fly.dev`. Vercel is outward-facing, so
you click these steps yourself. The backend's own checklist is `docs/deploy.md`.

## 1. Merge into `release`

Open a pull request `develop` -> `release`. **This also redeploys the backend**: `release` is behind
`develop` by the backend changes D-276, D-277, D-282 (the embedding batch of 8, the fix for the out of
memory crash on Fly) and the cause logging. There is no migration. When the Deploy workflow is green,
run `uv run python -m app.cli deploy-check --url https://blastproof.fly.dev`.

## 2. Import the project

Vercel -> New Project -> import `Mrinanka/blastproof`.

- Root Directory: `frontend`. The framework is detected as Next.js.
- Add these environment variables for Production and Preview. Mark `BLASTPROOF_API_URL` as Sensitive.

| Variable | Value |
|---|---|
| `BLASTPROOF_API_URL` | `https://blastproof.fly.dev` |
| `SESSION_COOKIE_NAME` | `blastproof_session` |
| `SESSION_MAX_AGE_SECONDS` | `28800` |

None starts with `NEXT_PUBLIC_`: nothing here is meant for the browser.

## 3. Production branch

Settings -> Environments -> Production -> Branch Tracking -> `release`, then redeploy from `release`.
Until this is set, Vercel treats `develop` (the repo's default branch) as production. Every other
branch gets a preview deployment.

## 4. Region

`frontend/vercel.json` sets `"regions": ["iad1"]` (Washington, D.C.), next to Fly's `iad`. Hobby allows
one region. Check that the deployment summary shows `iad1`.

## 5. Deployment Protection

Standard Protection with Vercel Authentication must be **on**. Previews talk to the production backend,
so this is what keeps preview URLs private. (It does not cover the production domains.)

## 6. Vercel Toolbar

Turn it **off** for Preview and Production. The nonce CSP would block it and log CSP errors, and we do
not loosen the CSP for it.

## 7. Domain

Add `blastproof.in` and `www.blastproof.in` (www redirects to the apex) and set the DNS records Vercel
shows. `SITE_URL` in `frontend/src/lib/landing/site.ts` already says `https://blastproof.in`.

## 8. Probe it

Read-only, needs no key:

```text
uv run python -m app.cli dashboard-check --url https://blastproof.in
uv run python -m app.cli dashboard-check --url https://<project>.vercel.app
```

The second skips the `og:image` host check. Every line must say `ok`. A failed line names what is
wrong, never the value the server sent.

## 9. Log in by hand

Log in, open the Overview, log out. Then upload one large knowledge file and check it does not 502
(Fly's proxy may cut idle requests near 60 s; D-268 measured 48 s).

## 10. LinkedIn

Run the LinkedIn Post Inspector on `https://blastproof.in` (DW-F13). LinkedIn, X and Slack cache
previews, so re-scrape after any later change.

## Known limits

- The login rate limit sees Fly's proxy address, not the user's, so all dashboard logins share one
  bucket. Stricter, so safe. Forwarding the real address safely needs a shared secret (a fourth
  environment variable, against SEC-31) or Vercel Static IPs (a paid plan).
- The BFF route sets `maxDuration = 150` (above the 120 s upload and scan timeout; Hobby allows 300 s).
  It does not fix a cut by Fly's proxy.
- The `*.vercel.app` production URL stays public next to `blastproof.in`, so search engines can see
  duplicate content. A redirect or canonical tag is later work.
- There is no `ignoreCommand`: `git diff HEAD^` misses multi-commit pushes and could leave production
  stale.
- Preview deployments share the production backend; a staging backend is phase 2.

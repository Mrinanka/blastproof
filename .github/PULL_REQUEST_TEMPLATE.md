## Summary

<!-- Provide a concise summary of the purpose and changes introduced by this pull request. -->

## Changes

- 
- 

## Test Plan

<!-- How did you test your changes? Include relevant commands and results. -->

- [ ] Backend unit tests pass (`cd backend && uv run pytest`)
- [ ] Backend linter passes (`uv run ruff check . ../demo_company ../claude_hook && uv run ruff format --check . ../demo_company ../claude_hook`)
- [ ] Frontend checks pass if applicable (`cd frontend && npm run lint && npm run format:check && npm run typecheck && npm test`)
- [ ] OpenAPI schema & TypeScript types synced if routes were changed (`export-openapi` + `npm run gen:api`)

## Checklist

- [ ] Code follows project architecture rules (credential isolation, fail-closed, etc.)
- [ ] No secrets, credentials, or `.env` entries are committed
- [ ] Relevant documentation or decision log updated if necessary

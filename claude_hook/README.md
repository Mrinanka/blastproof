# Claude Code demo workspace

A folder outside the repo where Claude Code runs. It holds no company database credential: its only
secret is `BLASTPROOF_AGENT_KEY`, and it reaches company data through the `blastproof` MCP server.

```text
# from the repo root; the key goes through stdin so it never reaches argv or shell history
uv run --project backend python claude_hook/workspace.py create "D:\My Workplace\blastproof-demo" --key-stdin
uv run --project backend python claude_hook/workspace.py check "D:\My Workplace\blastproof-demo"
uv run --project backend python claude_hook/workspace.py verify "D:\My Workplace\blastproof-demo"
```

- `create` without `--key-stdin` leaves the key empty; fill it in `.claude/settings.local.json`.
- Run `check` in the shell that launches `claude`: it scans the folder and that shell's environment.
- Start `claude` in the folder once by hand, trust it and approve the `blastproof` server
  (interactive sessions hold hooks back until the folder is trusted).
- `verify` needs a Claude login. It reads a canary file first and the real `backend/.env` only if
  the canary was denied; it never prints tool output.

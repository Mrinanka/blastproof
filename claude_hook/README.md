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

## The PreToolUse hook (step 18)

`create` also writes a `hooks.PreToolUse` entry into `.claude/settings.json` (see
`settings.example.json`). Before every Bash, PowerShell, Monitor, Edit, Write, NotebookEdit, Read,
Grep and Glob call, Claude Code runs `pre_tool_use.py`, which asks `POST /v1/hook/pre-tool-use`.
The backend checks the agent key, applies the rules (database clients, credential files, the hook's
own settings, hook bypasses, `terraform apply`, force pushes, `rm -rf` outside the workspace),
stores a masked `hook_events` row and answers allow or deny. A deny exits 2 and the reason reaches
the agent.

- The entry runs `sys.executable` from the shell that ran `create`; pass `--python <path>` to pick
  another interpreter. Only the standard library is used.
- The URL is an argument; the key is read from `BLASTPROOF_AGENT_KEY` only, so the workspace still
  holds one secret. The block message shows the hook's command line (Python path, script path, URL),
  never the key.
- **If Blastproof is down:** shell and edit tools (and any tool the script does not know) are blocked;
  Read, Grep and Glob still work except on credential files, the hook's settings and this repo.
  A rejected or missing key blocks every tool.
- Re-run `create --force --key-stdin` to add the hook to a workspace made before step 18 (it also
  rewrites `settings.local.json`, so give the key again).

By hand (DW-B18), from the demo workspace with uvicorn running: ask `claude` to run `psql`, to read
`backend/.env` and to edit `.claude/settings.json`, and check that each block reason appears. Stop
uvicorn and repeat: Bash and Edit are blocked, a normal file still reads, `.env` does not.

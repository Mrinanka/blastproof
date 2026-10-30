// Text and numbers for the public landing page (F13, FE-28). Every claim comes from docs/spec.md,
// the README or docs/decisions.md; every number from the seed (D-078) or the eval snapshot.

// The Replit-style mass delete from backend/evals/scenarios/incidents.yaml, scenario
// `replit-clean-up-inactive-users`. The counts are exact by construction (D-078);
// tests/landing-content.test.ts checks them against the YAML.
export const REPLIT = {
  intent: "Clean up inactive users",
  sql: "DELETE FROM users WHERE last_login_at < now() - interval '6 months'",
  users: 48213,
  paying: 12,
  enterprise: 3,
  cascades: [
    { table: "orders", deleted: 48500 },
    { table: "order_items", deleted: 121364 },
    { table: "support_tickets", deleted: 11647 },
    { table: "payments", deleted: 4382 },
  ],
} as const;

export type Incident = { when: string; date: string; text: string; source: string; url: string };

// docs/spec.md section 2. Text in `backticks` renders in monospace (components/landing/mono-text.tsx).
export const INCIDENTS: Incident[] = [
  {
    when: "Apr 2026",
    date: "2026-04",
    text: "A Cursor agent deleted PocketOS's production database and its backups with one cloud API call, using a token it found in a file.",
    source: "Live Science",
    url: "https://livescience.com/technology/artificial-intelligence/i-violated-every-principle-i-was-given-ai-agent-deletes-companys-entire-database-in-9-seconds-then-confesses",
  },
  {
    when: "Mar 2026",
    date: "2026-03",
    text: "An agent running Terraform commands deleted a founder's production database.",
    source: "OECD AI incidents",
    url: "https://oecd.ai/en/incidents/2026-03-07-79ec",
  },
  {
    when: "Jul 2025",
    date: "2025-07",
    text: "A Replit agent deleted SaaStr's production database during a code freeze, then produced misleading output.",
    source: "OECD AI incidents",
    url: "https://oecd.ai/en/incidents/2025-07-19-1eb1",
  },
];

export const CURRENT_OPTIONS = [
  { option: "No write access", result: "the agent is useless for real work." },
  { option: "Full access", result: "one bad guess destroys data." },
  {
    option: "Approve every command",
    result: "people see raw SQL, cannot judge it, and click approve anyway.",
  },
];

export const STEPS = [
  {
    title: "Intercept",
    text: "Every tool call goes through Blastproof, over `MCP` or the `Claude Code` `PreToolUse` hook. Reads run directly.",
  },
  {
    title: "Preview on a copy",
    text: "A write runs first on a disposable database branch, so its effect is measured, not guessed.",
  },
  {
    title: "Explain the impact",
    text: "Row counts come from the database. The AI only writes the sentence, and every number in it is checked against the computed facts.",
  },
  {
    title: "Approve or block",
    text: "Rules run first, then an AI judge that can only make a decision stricter. Safe writes go through, dangerous ones are blocked, the rest wait for the owner.",
  },
];

export const DIFFERENCES = [
  {
    title: "Outcomes, not commands",
    text: "The owner approves “delete 12 paying customers”, not a line of SQL.",
  },
  {
    title: "Real shadow execution, not predictions",
    text: "The write actually runs on a branch. The commit must reproduce exactly the diff that was approved, or it rolls back.",
  },
  {
    title: "Any `MCP` agent, plus a `Claude Code` hook",
    text: "Vendor-neutral: any agent that speaks `MCP` connects, and `Claude Code`'s shell and file tools are checked by the hook too.",
  },
];

export const SECURITY = [
  {
    title: "Credential isolation is the boundary",
    text: "The agent never holds the company database's credentials. They live only in the backend's environment.",
  },
  {
    title: "Fail closed",
    text: "Any error, timeout or unparseable SQL blocks the write. If only the AI judge fails, the write goes to a human.",
  },
  {
    title: "Commit exactly what was previewed",
    text: "The approved statement is pinned. If the commit would change anything else, it rolls back.",
  },
  {
    title: "Sensitive columns are masked",
    text: "Emails, phone numbers and similar columns are masked before they reach logs, prompts or the dashboard.",
  },
  {
    title: "Hooks are guardrails, with an audit trail",
    text: "The Claude Code hook catches honest mistakes early; it is not the boundary. Every action keeps a full audit trail.",
  },
];

export const FAQ = [
  {
    q: "Does the agent ever see my database password?",
    a: "No. The company database's credentials live only in Blastproof's backend. The agent holds a Blastproof key, which can only ask for actions, never connect to the database.",
  },
  {
    q: "What if the AI judge is down or wrong?",
    a: "Rules run first and the judge can only make a decision stricter, never looser. If the judge fails or times out, the write goes to a human for review instead of going through.",
  },
  {
    q: "Which agents work with it?",
    a: "Any agent that speaks MCP. Claude Code also connects through its PreToolUse hook, which checks shell, file and search tools.",
  },
  {
    q: "What does it support today?",
    a: "Postgres. Reads run directly; inserts, updates and deletes are previewed on a database branch; schema changes from agents are blocked. Shell sandboxes, an external app such as email or Slack, and Terraform come later.",
  },
  {
    q: "Can I sign up?",
    a: "Not yet. Accounts are created by an admin, because every account acts on one shared demo database. Watch the demo, or run Blastproof locally by following the README.",
  },
  {
    q: "Does it replace access control?",
    a: "No. It works alongside the permissions you already have. It adds a preview, an explanation and an approval step in front of an agent's writes.",
  },
];

// The README's Mermaid diagram, in words: the text alternative for the architecture section.
export const FLOW = [
  "The agent (Claude Code) calls Blastproof's backend by an MCP tool call or through the PreToolUse hook.",
  "A read goes to the company database directly.",
  "A write is previewed on a database branch.",
  "Rules, then an LLM judge, decide: allow, block or review.",
  "Allow commits exactly what was previewed. Block stops the write.",
  "Review waits for the owner to approve in the dashboard, then commits.",
  "Blastproof's own database holds the actions, the audit trail and the knowledge.",
];

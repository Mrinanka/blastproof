// Pure checks on the F1 scaffold config. Each returns a list of problems (empty when fine), in
// the same style as backend/tests/test_ci_workflow.py, so a bad fixture can prove each one fails.

export type HeaderRule = { source: string; headers: { key: string; value: string }[] };

const EXACT_VERSION = /^\d+\.\d+\.\d+$/;
const ENV_NAMES = ["BLASTPROOF_API_URL", "SESSION_COOKIE_NAME", "SESSION_MAX_AGE_SECONDS"];
const REQUIRED_HEADERS: Record<string, string> = {
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "X-Content-Type-Options": "nosniff",
};
const FORBIDDEN_SOURCE = ["dangerouslySetInnerHTML", "NEXT_PUBLIC_", "localStorage"];

export function inexactPins(pkg: {
  dependencies?: Record<string, string>;
  devDependencies?: Record<string, string>;
}): string[] {
  const all = { ...pkg.dependencies, ...pkg.devDependencies };
  return Object.entries(all)
    .filter(([, version]) => !EXACT_VERSION.test(version))
    .map(([name, version]) => `${name} is not an exact pin: ${version}`);
}

export function headerProblems(rules: HeaderRule[]): string[] {
  const rule = rules.find((r) => r.source === "/(.*)");
  if (!rule) return ["no rule for /(.*)"];
  const problems: string[] = [];
  for (const [key, value] of Object.entries(REQUIRED_HEADERS)) {
    const found = rule.headers.find((h) => h.key === key);
    if (!found) problems.push(`missing header: ${key}`);
    else if (found.value !== value) problems.push(`${key} should be ${value}, is ${found.value}`);
  }
  return problems;
}

export function envExampleProblems(text: string): string[] {
  const lines = text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#"));
  const entries = lines.map((line) => {
    const at = line.indexOf("=");
    return at < 0 ? [line, ""] : [line.slice(0, at), line.slice(at + 1)];
  });
  const problems: string[] = [];
  for (const [name] of entries) {
    if (name.startsWith("NEXT_PUBLIC_")) problems.push(`public variable: ${name}`);
  }
  const names = entries.map(([name]) => name);
  if (JSON.stringify([...names].sort()) !== JSON.stringify([...ENV_NAMES].sort())) {
    problems.push(`names must be exactly ${ENV_NAMES.join(", ")}; got ${names.join(", ")}`);
  }
  const maxAge = entries.find(([name]) => name === "SESSION_MAX_AGE_SECONDS");
  if (maxAge && maxAge[1] !== "28800") problems.push("SESSION_MAX_AGE_SECONDS must be 28800");
  return problems;
}

export function forbiddenSource(files: Record<string, string>): string[] {
  const problems: string[] = [];
  for (const [path, text] of Object.entries(files)) {
    for (const pattern of FORBIDDEN_SOURCE) {
      if (text.includes(pattern)) problems.push(`${path} contains ${pattern}`);
    }
  }
  return problems;
}

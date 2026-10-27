// Where to go after login. Only dashboard paths on this origin: anything else (another site, a
// protocol-relative or backslash path, a scheme) falls back to /dashboard.
export function safeNext(value: string | null | undefined): string {
  if (!value) return "/dashboard";
  const underDashboard = value === "/dashboard" || value.startsWith("/dashboard/");
  const startsQuery = value.startsWith("/dashboard?");
  if (!underDashboard && !startsQuery) return "/dashboard";
  if (value.includes("//") || value.includes("\\") || /[\u0000-\u001f]/.test(value)) {
    return "/dashboard";
  }
  return value;
}

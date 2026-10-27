// True only when the route answered 2xx, so the UI never claims a logout that did not happen.
export async function logout(): Promise<boolean> {
  try {
    const response = await fetch("/api/auth/logout", { method: "POST" });
    return response.ok;
  } catch {
    return false;
  }
}

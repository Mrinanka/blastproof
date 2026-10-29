export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

// A body without the envelope (a proxy's or platform's error page) is never shown; the status is,
// so "HTTP 502" points at the right place instead of a bare "Something went wrong."
function errorFields(error: unknown, status: number): { code: string; message: string } {
  const detail = (error as { error?: { code?: unknown; message?: unknown } } | null)?.error;
  return {
    code: typeof detail?.code === "string" ? detail.code : "unknown_error",
    message:
      typeof detail?.message === "string"
        ? detail.message
        : `The server returned an error (HTTP ${status}).`,
  };
}

// Turns openapi-fetch's { data, error, response } into the data, or throws an ApiError that
// carries the HTTP status (0 when the request never got an answer).
export async function unwrap<T>(request: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await request;
  } catch {
    throw new ApiError(0, "network_error", "Could not reach the server.");
  }
  if (result.error !== undefined || !result.response.ok) {
    const { code, message } = errorFields(result.error, result.response.status);
    throw new ApiError(result.response.status, code, message);
  }
  return result.data as T;
}

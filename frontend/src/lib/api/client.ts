import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

// The browser only ever calls the same-origin proxy; it alone knows the backend address (CONV-14).
export const API_BASE_URL = "/api/blastproof";

export const api = createClient<paths>({ baseUrl: API_BASE_URL });

export type Schemas = components["schemas"];
export type ApiErrorBody = Schemas["ErrorResponse"];

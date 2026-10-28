// History's filters live in the URL (CONV-12). Invalid values are dropped, never sent: a bad
// bookmark shows the unfiltered view instead of a 422.
import { STATUS_META, type ActionStatus } from "@/components/actions/status-badge";
import type { paths } from "@/lib/api/schema";

type ActionsQuery = NonNullable<paths["/v1/actions"]["get"]["parameters"]["query"]>;

export const HISTORY_PAGE_SIZE = 25;

export interface HistoryFilters {
  statuses: ActionStatus[];
  agentId: string | null;
  table: string | null;
  from: string | null; // YYYY-MM-DD, the viewer's local day
  to: string | null; // YYYY-MM-DD, inclusive here; the backend's `to` is exclusive
  page: number; // 1-based
}

export type FilterPatch = Partial<Omit<HistoryFilters, "page">>;

// STATUS_META's key order is the canonical order, so one view has one URL.
export const STATUS_ORDER = Object.keys(STATUS_META) as ActionStatus[];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const TABLE_MAX = 63; // the backend's limit (a Postgres identifier)
const MAX_PAGE = Math.floor(Number.MAX_SAFE_INTEGER / HISTORY_PAGE_SIZE);

function dayParts(value: string): [number, number, number] | null {
  const m = DAY.exec(value);
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  // Years below 1000 are refused: `new Date(y, ...)` reads 0-99 as 1900-1999.
  if (y < 1000) return null;
  const date = new Date(y, mo - 1, d);
  const real = date.getFullYear() === y && date.getMonth() === mo - 1 && date.getDate() === d;
  return real ? [y, mo, d] : null;
}

export function isDay(value: string): boolean {
  return dayParts(value) !== null;
}

function readDay(value: string | null): string | null {
  return value !== null && isDay(value) ? value : null;
}

function readPage(value: string | null): number {
  if (value === null || !/^\d+$/.test(value)) return 1;
  const n = Number(value);
  return n >= 1 && n <= MAX_PAGE ? n : 1;
}

export function parseHistoryParams(params: URLSearchParams): HistoryFilters {
  const wanted = new Set(params.getAll("status"));
  const agent = params.get("agent");
  const table = params.get("table")?.trim() ?? "";
  return {
    statuses: STATUS_ORDER.filter((s) => wanted.has(s)),
    agentId: agent !== null && UUID.test(agent) ? agent.toLowerCase() : null,
    table: table.length >= 1 && table.length <= TABLE_MAX ? table : null,
    from: readDay(params.get("from")),
    to: readDay(params.get("to")),
    page: readPage(params.get("page")),
  };
}

export function toSearchString(f: HistoryFilters): string {
  const params = new URLSearchParams();
  for (const s of f.statuses) params.append("status", s);
  if (f.agentId) params.set("agent", f.agentId);
  if (f.table) params.set("table", f.table);
  if (f.from) params.set("from", f.from);
  if (f.to) params.set("to", f.to);
  if (f.page > 1) params.set("page", String(f.page));
  return params.toString();
}

// A new filter means a new result set, so it starts on page 1.
export function applyPatch(f: HistoryFilters, patch: FilterPatch): HistoryFilters {
  return { ...f, ...patch, page: 1 };
}

export function toggleStatus(
  statuses: ActionStatus[],
  status: ActionStatus,
  on: boolean,
): ActionStatus[] {
  const next = new Set(statuses);
  if (on) next.add(status);
  else next.delete(status);
  return STATUS_ORDER.filter((s) => next.has(s));
}

export function withPage(f: HistoryFilters, page: number): HistoryFilters {
  return { ...f, page };
}

export function hasFilters(f: HistoryFilters): boolean {
  return f.statuses.length > 0 || !!f.agentId || !!f.table || !!f.from || !!f.to;
}

function localMidnight(day: string, addDays = 0): string {
  const [y, m, d] = dayParts(day)!;
  // Date's own day arithmetic keeps a DST day at 23 or 25 hours.
  return new Date(y, m - 1, d + addDays).toISOString();
}

// null when the range runs backwards: the page shows a message and sends nothing.
export function toActionsQuery(f: HistoryFilters): ActionsQuery | null {
  if (f.from && f.to && f.from > f.to) return null;
  const query: ActionsQuery = {
    order: "newest",
    limit: HISTORY_PAGE_SIZE,
    offset: (f.page - 1) * HISTORY_PAGE_SIZE,
  };
  if (f.statuses.length > 0) query.status = f.statuses;
  if (f.agentId) query.agent_id = f.agentId;
  if (f.table) query.table = f.table;
  if (f.from) query.from = localMidnight(f.from);
  if (f.to) query.to = localMidnight(f.to, 1);
  return query;
}

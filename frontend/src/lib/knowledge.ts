import type { Schemas } from "@/lib/api/client";
import { formatNumber } from "@/lib/format";

export type KnowledgeSource = Schemas["KnowledgeSource"];

export const MAX_UPLOAD_BYTES = 1024 * 1024;

// Copied from backend/app/services/uploads.py; the backend stays the authority.
export const SOURCE_NAME = /^[A-Za-z0-9][A-Za-z0-9._ -]{0,99}\.(md|txt)$/;

const NAME_CHAR = /[A-Za-z0-9._ -]/;

// Why a name fails SOURCE_NAME, cause by cause, so the message points at the real problem
// (owner's hand test: a comma got "has no slashes"). Null when the name passes.
export function nameProblem(name: string): string | null {
  if (SOURCE_NAME.test(name)) return null;
  const match = /\.(md|txt)$/.exec(name);
  if (!match) return "Only .md and .txt files can be uploaded.";
  const stem = name.slice(0, match.index);
  if (!/^[A-Za-z0-9]/.test(stem)) return "The file name must start with a letter or digit.";
  const bad = [...stem].find((c) => !NAME_CHAR.test(c));
  if (bad !== undefined) {
    return `The file name can't contain "${bad}". Use letters, digits, spaces, dots, dashes or underscores.`;
  }
  return "The file name is too long (100 characters at most before the extension).";
}

// The message to show, or null when the file may be sent.
export function checkUploadFile(file: File | undefined | null): string | null {
  if (!file) return "Choose a .md or .txt file.";
  const problem = nameProblem(file.name);
  if (problem) return problem;
  if (file.size === 0) return "The file is empty.";
  if (file.size > MAX_UPLOAD_BYTES) {
    return `The file is larger than ${formatNumber(MAX_UPLOAD_BYTES / 1024)} KB.`;
  }
  return null;
}

// One `tables` entry per tag, as the backend's multipart route reads them.
export function uploadForm(file: File, tables: string[]): FormData {
  const form = new FormData();
  form.append("file", file);
  for (const table of tables) form.append("tables", table);
  return form;
}

// Only tables the schema scan knows can be tagged: a typo would never match an action's tables.
export function knownTables(sources: KnowledgeSource[]): string[] {
  const scan = sources.find((s) => s.kind === "schema_scan");
  return scan ? [...new Set(scan.table_names)].sort() : [];
}

const KIND_LABELS: Record<KnowledgeSource["kind"], string> = {
  schema_scan: "Schema scan",
  schema_docs: "Schema docs",
  upload: "Upload",
};

export function kindLabel(kind: KnowledgeSource["kind"]): string {
  return KIND_LABELS[kind];
}

// An upload with an existing name replaces it (D-241).
export function replaces(sources: KnowledgeSource[], name: string): KnowledgeSource | undefined {
  return sources.find((s) => s.source === name);
}

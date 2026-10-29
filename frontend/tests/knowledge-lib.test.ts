// F10 guards: FE-23 (client-side checks), DW-F10 (table tags reach the form).
import { describe, expect, it } from "vitest";

import {
  MAX_UPLOAD_BYTES,
  checkUploadFile,
  kindLabel,
  knownTables,
  nameProblem,
  replaces,
  uploadForm,
  type KnowledgeSource,
} from "../src/lib/knowledge";

const file = (name: string, size = 10) => new File([new Uint8Array(size)], name);
const source = (over: Partial<KnowledgeSource>): KnowledgeSource => ({
  source: "s.md",
  kind: "upload",
  chunks: 1,
  table_names: [],
  updated_at: "2026-10-29T10:00:00Z",
  ...over,
});

describe("checkUploadFile names", () => {
  it.each(["notes.md", "notes.txt", "my notes.md", "a.b-c_d.md", "1.txt"])("accepts %s", (n) => {
    expect(checkUploadFile(file(n))).toBeNull();
  });
  it.each([
    "NOTES.MD",
    "x.pdf",
    "noext",
    ".hidden.md",
    "-dash.md",
    "a/b.md",
    "a\b.md",
    `${"a".repeat(101)}.md`,
  ])("refuses %s", (n) => {
    expect(checkUploadFile(file(n))).not.toBeNull();
  });
  it("accepts 100 characters before the extension", () => {
    expect(checkUploadFile(file(`${"a".repeat(100)}.md`))).toBeNull();
  });
});

describe("nameProblem names the real cause", () => {
  it.each([
    ["Act as a ruthless, elite technical.txt", `can't contain ","`],
    ["a/b.md", `can't contain "/"`],
    ["a\\b.md", `can't contain "\\"`],
    ["x.pdf", "Only .md and .txt"],
    ["NOTES.MD", "Only .md and .txt"],
    ["noext", "Only .md and .txt"],
    [".hidden.md", "start with a letter or digit"],
    ["-dash.md", "start with a letter or digit"],
    [`${"a".repeat(101)}.md`, "too long"],
  ])("%s", (name, expected) => {
    expect(nameProblem(name)).toContain(expected);
  });

  it("passes a good name", () => {
    expect(nameProblem("my notes_v2.1-final.md")).toBeNull();
  });
});

describe("checkUploadFile sizes", () => {
  it("takes exactly 1 MB", () =>
    expect(checkUploadFile(file("a.md", MAX_UPLOAD_BYTES))).toBeNull());
  it("refuses 1 byte more", () =>
    expect(checkUploadFile(file("a.md", MAX_UPLOAD_BYTES + 1))).toMatch(/larger/));
  it("refuses an empty file", () => expect(checkUploadFile(file("a.md", 0))).toMatch(/empty/));
  it("refuses no file", () => {
    expect(checkUploadFile(null)).not.toBeNull();
    expect(checkUploadFile(undefined)).not.toBeNull();
  });
});

describe("uploadForm", () => {
  it("has one file and the tables in order", () => {
    const form = uploadForm(file("a.md"), ["users", "orders"]);
    expect(form.getAll("file")).toHaveLength(1);
    expect(form.getAll("tables")).toEqual(["users", "orders"]);
  });
  it("has no tables entry when none are given", () => {
    expect(uploadForm(file("a.md"), []).has("tables")).toBe(false);
  });
});

describe("knownTables", () => {
  it("is the scan's tables, sorted and deduplicated, ignoring uploads' tags", () => {
    const rows = [
      source({ source: "u.md", table_names: ["zzz"] }),
      source({
        source: "schema_scan",
        kind: "schema_scan",
        table_names: ["users", "orders", "users"],
      }),
    ];
    expect(knownTables(rows)).toEqual(["orders", "users"]);
  });
  it("is empty without a scan", () => {
    expect(knownTables([source({ table_names: ["users"] })])).toEqual([]);
  });
});

describe("labels", () => {
  it("names each kind", () => {
    expect(kindLabel("schema_scan")).toBe("Schema scan");
    expect(kindLabel("schema_docs")).toBe("Schema docs");
    expect(kindLabel("upload")).toBe("Upload");
  });
  it("finds the source an upload would replace", () => {
    const rows = [source({ source: "a.md" })];
    expect(replaces(rows, "a.md")).toBe(rows[0]);
    expect(replaces(rows, "b.md")).toBeUndefined();
  });
});

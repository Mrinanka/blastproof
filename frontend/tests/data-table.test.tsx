// @vitest-environment jsdom
// F5 guards: FE-12 (paginated table and its states).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DataTable, pageInfo } from "../src/components/common/data-table";
import { ApiError } from "../src/lib/api/errors";

afterEach(cleanup);

describe("pageInfo", () => {
  it.each([
    [
      { total: 0, limit: 25, offset: 0 },
      { from: 0, to: 0, hasPrev: false, hasNext: false },
    ],
    [
      { total: 312, limit: 25, offset: 0 },
      { from: 1, to: 25, hasPrev: false, hasNext: true },
    ],
    [
      { total: 312, limit: 25, offset: 25 },
      { from: 26, to: 50, hasPrev: true, hasNext: true },
    ],
    [
      { total: 312, limit: 25, offset: 300 },
      { from: 301, to: 312, hasPrev: true, hasNext: false },
    ],
    [
      { total: 50, limit: 25, offset: 25 },
      { from: 26, to: 50, hasPrev: true, hasNext: false },
    ],
    [
      { total: 10, limit: 25, offset: 50 },
      { from: 0, to: 0, hasPrev: true, hasNext: false },
    ],
    [
      { total: 10, limit: 0, offset: 0 },
      { from: 1, to: 1, hasPrev: false, hasNext: true },
    ],
  ])("%j", (page, expected) => {
    expect(pageInfo(page)).toMatchObject(expected);
  });

  it("computes the offsets", () => {
    const info = pageInfo({ total: 312, limit: 25, offset: 10 });
    expect(info.prevOffset).toBe(0);
    expect(info.nextOffset).toBe(35);
  });
});

interface Row {
  id: string;
  name: string;
}
const ROWS: Row[] = [
  { id: "1", name: "alpha" },
  { id: "2", name: "beta" },
];

function table(props: Partial<React.ComponentProps<typeof DataTable<Row>>> = {}) {
  return (
    <DataTable<Row>
      label="Things"
      columns={[{ header: "Name", cell: (r) => r.name }]}
      rows={ROWS}
      rowKey={(r) => r.id}
      isLoading={false}
      empty={{ title: "No things" }}
      {...props}
    />
  );
}

describe("DataTable", () => {
  it("renders rows", () => {
    render(table());
    expect(screen.getByRole("table", { name: "Things" })).toBeTruthy();
    expect(screen.getByText("alpha")).toBeTruthy();
    expect(screen.getByText("beta")).toBeTruthy();
  });

  it("shows skeletons while loading", () => {
    render(table({ isLoading: true, rows: undefined }));
    expect(screen.getAllByTestId("skeleton-line").length).toBeGreaterThan(0);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows the empty state", () => {
    render(table({ rows: [] }));
    expect(screen.getByText("No things")).toBeTruthy();
  });

  it("shows the error with Retry when there are no rows", () => {
    const onRetry = vi.fn();
    render(table({ rows: undefined, error: new ApiError(500, "boom", "Server broke."), onRetry }));
    expect(screen.getByText("Server broke.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("keeps rows and adds an alert when a refresh fails", () => {
    render(table({ error: new ApiError(500, "boom", "Server broke.") }));
    expect(screen.getByText("alpha")).toBeTruthy();
    expect(screen.getByText(/Couldn't refresh/)).toBeTruthy();
  });

  it("disables Previous on the first page and Next on the last", () => {
    const { rerender } = render(table({ page: { total: 60, limit: 25, offset: 0 } }));
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Previous" }).disabled).toBe(true);
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Next" }).disabled).toBe(false);
    rerender(table({ page: { total: 60, limit: 25, offset: 50 } }));
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Previous" }).disabled).toBe(
      false,
    );
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Next" }).disabled).toBe(true);
  });

  it("calls onPageChange with the right offsets and shows the range", () => {
    const onPageChange = vi.fn();
    render(table({ page: { total: 312, limit: 25, offset: 25 }, onPageChange }));
    expect(screen.getByText("Showing 26–50 of 312")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Next" }));
    fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(onPageChange).toHaveBeenNthCalledWith(1, 50);
    expect(onPageChange).toHaveBeenNthCalledWith(2, 0);
  });

  it("handles an offset past the end", () => {
    render(table({ rows: [], page: { total: 10, limit: 25, offset: 50 } }));
    expect(screen.getByText("No rows on this page")).toBeTruthy();
    expect(screen.getByRole<HTMLButtonElement>("button", { name: "Previous" }).disabled).toBe(
      false,
    );
  });
});

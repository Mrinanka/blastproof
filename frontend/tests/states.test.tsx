// @vitest-environment jsdom
// F5 guards: FE-12 (loading, empty and error states).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { EmptyState, ErrorState, LoadingState } from "../src/components/common/states";
import { ApiError } from "../src/lib/api/errors";

afterEach(cleanup);

describe("ErrorState", () => {
  it("shows an ApiError's message", () => {
    render(<ErrorState error={new ApiError(404, "not_found", "Action not found.")} />);
    expect(screen.getByText("Action not found.")).toBeTruthy();
  });

  it("never shows a plain Error's message", () => {
    render(<ErrorState error={new Error("secret detail")} />);
    expect(screen.getByText("Something went wrong.")).toBeTruthy();
    expect(screen.queryByText(/secret detail/)).toBeNull();
  });

  it("takes a title", () => {
    render(<ErrorState error={null} title="Not found" />);
    expect(screen.getByText("Not found")).toBeTruthy();
  });

  it("has Retry only with onRetry, and calls it", () => {
    const { rerender } = render(<ErrorState error={null} />);
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    const onRetry = vi.fn();
    rerender(<ErrorState error={null} onRetry={onRetry} />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("EmptyState", () => {
  it("shows title and description", () => {
    render(<EmptyState title="Nothing here" description="Try later" />);
    expect(screen.getByText("Nothing here")).toBeTruthy();
    expect(screen.getByText("Try later")).toBeTruthy();
  });
});

describe("LoadingState", () => {
  it("renders its number of lines and is busy", () => {
    render(<LoadingState lines={4} />);
    expect(screen.getAllByTestId("skeleton-line")).toHaveLength(4);
    expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
  });
});

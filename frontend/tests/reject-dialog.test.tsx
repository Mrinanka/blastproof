// @vitest-environment jsdom
// F7 guards: FE-19, TEST-10 (reject reason rules).
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RejectDialog, rejectSchema } from "../src/components/change-request/reject-dialog";
import { ApiError } from "../src/lib/api/errors";
import { installDomStubs } from "./dom-stubs";

beforeEach(installDomStubs);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function open(onReject = vi.fn(async () => undefined)) {
  render(<RejectDialog onReject={onReject} />);
  fireEvent.click(screen.getByRole("button", { name: "Reject…" }));
  return onReject;
}
const submit = () => fireEvent.click(screen.getByRole("button", { name: "Reject" }));
const type = (value: string) =>
  fireEvent.change(screen.getByLabelText("Reason"), { target: { value } });

describe("rejectSchema", () => {
  it.each([
    ["123456789", false],
    ["   short   ", false],
    ["1234567890", true],
    ["  1234567890  ", true],
    ["x".repeat(500), true],
    ["x".repeat(501), false],
  ])("%j -> valid %s", (reason, ok) => {
    expect(rejectSchema.safeParse({ reason }).success).toBe(ok);
  });
});

describe("RejectDialog", () => {
  it("shows a field error and sends nothing under 10 characters", async () => {
    const onReject = open();
    type("123456789");
    submit();
    await screen.findByText(/at least 10 characters/);
    expect(onReject).not.toHaveBeenCalled();
  });

  it("sends the trimmed reason and closes on success", async () => {
    const onReject = open();
    type("  too risky for prod  ");
    submit();
    await waitFor(() => expect(onReject).toHaveBeenCalledWith("too risky for prod"));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("rejects 501 characters", async () => {
    const onReject = open();
    type("x".repeat(501));
    submit();
    await screen.findByText(/500 characters or fewer/);
    expect(onReject).not.toHaveBeenCalled();
  });

  it("Cancel and Escape send nothing", async () => {
    const onReject = open();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    fireEvent.click(screen.getByRole("button", { name: "Reject…" }));
    fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(onReject).not.toHaveBeenCalled();
  });

  it("shows the backend message and stays open on failure", async () => {
    const onReject = open(
      vi.fn(async () => {
        throw new ApiError(422, "validation_error", "Reason is too vague");
      }),
    );
    type("long enough reason");
    submit();
    await screen.findByText("Reason is too vague");
    expect(onReject).toHaveBeenCalledOnce();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });
});

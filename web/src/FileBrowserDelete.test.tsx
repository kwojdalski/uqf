import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { FileBrowser } from "./FileBrowser";

const listing = {
  target: "box",
  root: "/srv/uqf",
  path: "",
  parent: null,
  entries: [
    {
      name: "config.csv",
      kind: "file",
      size: 10,
      mtime: null,
      blocked: false,
      reason: null,
    },
  ],
  total: 1,
  truncated: false,
  release: { current: null, release: null, report: null, report_error: null },
};
const ok = (body: unknown) => ({ ok: true, json: async () => body });

it("locks selection during deletion and preserves outcomes when refresh fails", async () => {
  const user = userEvent.setup();
  let finish!: (value: unknown) => void;
  let reads = 0;
  const fetcher = vi.fn((_url: string, options: RequestInit) => {
    if (options.method === "POST")
      return new Promise((resolve) => {
        finish = resolve;
      });
    if (++reads === 1) return Promise.resolve(ok(listing));
    return Promise.reject(new Error("refresh lost"));
  });
  vi.stubGlobal("fetch", fetcher);
  vi.stubGlobal("confirm", () => true);
  render(<FileBrowser target="box" onClose={vi.fn()} />);
  await user.click(await screen.findByLabelText("Select config.csv"));
  await user.click(screen.getByRole("button", { name: "Delete selected (1)" }));
  expect(screen.getByLabelText("Select config.csv")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Refresh" })).toBeDisabled();
  finish(
    ok({
      target: "box",
      root: "/srv/uqf",
      results: [{ path: "config.csv", deleted: true, error: null }],
    }),
  );
  await waitFor(() =>
    expect(
      screen.getByText(/refreshing the listing failed/),
    ).toBeInTheDocument(),
  );
  expect(
    screen.getByRole("list", { name: "Deletion results" }),
  ).toHaveTextContent('"config.csv": Deleted');
  expect(screen.getByLabelText("Select config.csv")).toBeDisabled();
  expect(
    fetcher.mock.calls.filter(([, options]) => options.method === "POST"),
  ).toHaveLength(1);
});

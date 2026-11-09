import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { FileDelete } from "./FileDelete";

function mount(refresh = vi.fn().mockResolvedValue(undefined)) {
  render(
    <FileDelete
      target="box"
      dest="/srv/uqf"
      paths={["shared/a", "shared/b"]}
      disabled={false}
      onBusy={vi.fn()}
      onRefresh={refresh}
    />,
  );
  return refresh;
}

it("cancellation sends no deletion request and confirmation names every path", async () => {
  const confirm = vi.fn().mockReturnValue(false);
  const fetcher = vi.fn();
  vi.stubGlobal("confirm", confirm);
  vi.stubGlobal("fetch", fetcher);
  mount();
  await userEvent.click(
    screen.getByRole("button", { name: "Delete selected (2)" }),
  );
  expect(confirm).toHaveBeenCalledWith(
    expect.stringContaining("Target: box\nDeployment root: /srv/uqf"),
  );
  expect(confirm.mock.calls[0][0]).toContain('"shared/a"\n"shared/b"');
  expect(fetcher).not.toHaveBeenCalled();
});

it("reports partial success independently from a subsequent refresh failure", async () => {
  vi.stubGlobal("confirm", () => true);
  const fetcher = vi.fn().mockResolvedValue({
    ok: true,
    json: async () => ({
      target: "box",
      dest: "/srv/uqf",
      results: [
        { path: "shared/a", outcome: "succeeded", error: null },
        { path: "shared/b", outcome: "failed", error: "Directory not empty" },
      ],
    }),
  });
  vi.stubGlobal("fetch", fetcher);
  mount(vi.fn().mockRejectedValue(new Error("offline")));
  await userEvent.click(
    screen.getByRole("button", { name: "Delete selected (2)" }),
  );
  expect(
    await screen.findByText(/Not deleted: Directory not empty/),
  ).toBeInTheDocument();
  expect(screen.getByText(/refreshing the listing failed/)).toBeInTheDocument();
  const [path, options] = fetcher.mock.calls[0];
  expect(path).toBe("/services/box/files");
  expect(options.method).toBe("DELETE");
  expect(JSON.parse(options.body)).toEqual({
    paths: ["shared/a", "shared/b"],
    confirmed: true,
  });
  expect(fetcher).toHaveBeenCalledTimes(1);
});

it("does not retry or refresh when the deletion outcome is unknown", async () => {
  vi.stubGlobal("confirm", () => true);
  const fetcher = vi.fn().mockRejectedValue(new Error("connection lost"));
  vi.stubGlobal("fetch", fetcher);
  const refresh = mount();
  await userEvent.click(
    screen.getByRole("button", { name: "Delete selected (2)" }),
  );
  expect(
    await screen.findByText(/Deletion outcome may be unknown/),
  ).toBeInTheDocument();
  expect(refresh).not.toHaveBeenCalled();
  expect(fetcher).toHaveBeenCalledTimes(1);
});

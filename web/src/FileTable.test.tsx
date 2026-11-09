import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { FileTable, size } from "./FileTable";
import type { RemoteEntry } from "./api/services";

const entries: RemoteEntry[] = [
  {
    name: "alpha",
    kind: "file",
    size: 100,
    mtime: null,
    blocked: false,
    reason: null,
  },
  {
    name: "zeta",
    kind: "dir",
    size: 2,
    mtime: null,
    blocked: false,
    reason: null,
  },
  {
    name: "link",
    kind: "symlink",
    size: null,
    mtime: null,
    blocked: true,
    reason: "symlink",
  },
];

it("preserves selected identities through numeric sorting and excludes blocked entries", async () => {
  const user = userEvent.setup();
  render(<FileTable entries={entries} disabled={false} onOpen={vi.fn()} />);
  await user.click(screen.getByLabelText("Select alpha"));
  expect(screen.getByLabelText("Select all files")).toBePartiallyChecked();
  await user.click(screen.getByRole("button", { name: /^size/ }));
  expect(screen.getByLabelText("Select alpha")).toBeChecked();
  expect(screen.getByText("1 of 2 selected")).toBeInTheDocument();
  expect(screen.getByLabelText("Select link")).toBeDisabled();
  await user.click(screen.getByLabelText("Select all files"));
  expect(screen.getByLabelText("Select all files")).toBeChecked();
  expect(screen.getByLabelText("Select all files")).not.toBePartiallyChecked();
  expect(screen.getByLabelText("Select link")).not.toBeChecked();
  expect(screen.getByText("2 of 2 selected")).toBeInTheDocument();
  const rows = screen.getAllByRole("row").slice(1);
  expect(within(rows[0]).getByText("alpha")).toBeInTheDocument();
  expect(within(rows[1]).getByText("zeta/")).toBeInTheDocument();
});

it("clears selection on a fresh listing and supports keyboard selection and sorting", async () => {
  const user = userEvent.setup();
  const { rerender } = render(
    <FileTable entries={entries} disabled={false} onOpen={vi.fn()} />,
  );
  screen.getByLabelText("Select alpha").focus();
  await user.keyboard(" ");
  expect(screen.getByLabelText("Select alpha")).toBeChecked();
  const sort = screen.getByRole("button", { name: /^name/ });
  sort.focus();
  await user.keyboard("{Enter}");
  expect(sort.closest("th")).toHaveAttribute("aria-sort", "descending");
  rerender(
    <FileTable entries={[...entries]} disabled={false} onOpen={vi.fn()} />,
  );
  expect(screen.getByText("0 of 2 selected")).toBeInTheDocument();
});

it("opens directories and disables every selection when a listing is unavailable", async () => {
  const user = userEvent.setup();
  const open = vi.fn();
  const { rerender } = render(
    <FileTable entries={entries} disabled={false} onOpen={open} />,
  );
  await user.click(screen.getByRole("button", { name: "zeta/" }));
  expect(open).toHaveBeenCalledWith("zeta");
  rerender(<FileTable entries={entries} disabled={true} onOpen={open} />);
  for (const checkbox of screen.getAllByRole("checkbox"))
    expect(checkbox).toBeDisabled();
});

it("clears selection when navigating targets or directories, or disconnecting", async () => {
  const user = userEvent.setup();
  const view = (scope: string, disabled = false) => (
    <FileTable
      key={scope}
      entries={entries}
      disabled={disabled}
      onOpen={vi.fn()}
    />
  );
  const { rerender } = render(view("target-a/root"));
  await user.click(screen.getByLabelText("Select alpha"));
  rerender(view("target-a/subdir"));
  expect(screen.getByLabelText("Select alpha")).not.toBeChecked();
  await user.click(screen.getByLabelText("Select alpha"));
  rerender(view("target-b/root"));
  expect(screen.getByLabelText("Select alpha")).not.toBeChecked();
  await user.click(screen.getByLabelText("Select alpha"));
  rerender(view("target-b/root", true));
  expect(screen.getByLabelText("Select alpha")).not.toBeChecked();
});

it("shows a size just under a unit boundary in the next unit (#2588)", () => {
  expect(size(1048575)).toBe("1.0 MiB");
  expect(size(1023.99 * 2 ** 20)).toBe("1.0 GiB");
  expect(size(1023)).toBe("1023 B");
  expect(size(1024)).toBe("1.0 KiB");
  expect(size(1536)).toBe("1.5 KiB");
});

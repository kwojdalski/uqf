import { useEffect, useId, useRef } from "react";
import {
  useTable,
  tableFeatures,
  rowSortingFeature,
  rowSelectionFeature,
  createSortedRowModel,
  sortFns,
} from "@tanstack/react-table";
import type { RemoteEntry } from "./api";

const features = tableFeatures({
  rowSortingFeature,
  rowSelectionFeature,
  sortedRowModel: createSortedRowModel(),
  sortFns,
});

export function size(bytes: number | null): string {
  if (bytes === null) return "";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return unit === 0 ? `${bytes} B` : `${value.toFixed(1)} ${units[unit]}`;
}

const KIND: Record<RemoteEntry["kind"], string> = {
  dir: "Directory",
  file: "File",
  symlink: "Link",
  special: "Special",
  unknown: "Unknown",
};

function Checkbox({
  label,
  checked,
  mixed = false,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  mixed?: boolean;
  disabled: boolean;
  onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = mixed;
  }, [mixed]);
  return (
    <input
      ref={ref}
      type="checkbox"
      aria-label={label}
      checked={checked}
      aria-checked={mixed ? "mixed" : checked}
      disabled={disabled}
      onChange={onChange}
    />
  );
}

export function FileTable({
  entries,
  disabled,
  onOpen,
}: {
  entries: RemoteEntry[];
  disabled: boolean;
  onOpen: (name: string) => void;
}) {
  const table = useTable({
    key: useId(),
    features,
    data: entries,
    columns: [
      { accessorKey: "name" },
      { accessorKey: "kind" },
      { accessorKey: "size" },
    ],
    getRowId: (entry) => entry.name,
    enableRowSelection: (row) => !row.original.blocked,
    initialState: { sorting: [{ id: "name", desc: false }] },
  });
  useEffect(() => table.resetRowSelection(), [entries]);
  useEffect(() => {
    if (disabled) table.resetRowSelection();
  }, [disabled]);
  const selectable = entries.filter((entry) => !entry.blocked).length;
  return (
    <>
      <p role="status">
        {table.getSelectedRowModel().rows.length} of {selectable} selected
      </p>
      <div className="table-scroll" tabIndex={0} aria-label="Files">
        <table>
          <thead>
            <tr>
              <th scope="col">
                <Checkbox
                  label="Select all files"
                  checked={table.getIsAllRowsSelected()}
                  mixed={
                    !table.getIsAllRowsSelected() &&
                    table.getIsSomeRowsSelected()
                  }
                  disabled={disabled || selectable === 0}
                  onChange={() => table.toggleAllRowsSelected()}
                />
              </th>
              {(
                [
                  ["name", "name"],
                  ["kind", "type"],
                  ["size", "size"],
                ] as const
              ).map(([id, label]) => {
                const column = table.getColumn(id)!;
                const direction = column.getIsSorted();
                return (
                  <th
                    key={id}
                    scope="col"
                    aria-sort={
                      direction === "asc"
                        ? "ascending"
                        : direction === "desc"
                          ? "descending"
                          : "none"
                    }
                  >
                    <button
                      type="button"
                      onClick={column.getToggleSortingHandler()}
                    >
                      {label}
                      {direction === "asc"
                        ? " ↑"
                        : direction === "desc"
                          ? " ↓"
                          : ""}
                    </button>
                  </th>
                );
              })}
              <th scope="col">note</th>
            </tr>
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => {
              const entry = row.original;
              return (
                <tr
                  key={row.id}
                  className={entry.blocked ? "blocked" : undefined}
                >
                  <td>
                    <Checkbox
                      label={`Select ${entry.name}`}
                      checked={row.getIsSelected()}
                      disabled={disabled || !row.getCanSelect()}
                      onChange={() => row.toggleSelected()}
                    />
                  </td>
                  <td className="file-name">
                    {entry.kind === "dir" && !entry.blocked ? (
                      <button
                        type="button"
                        className="link"
                        disabled={disabled}
                        onClick={() => onOpen(entry.name)}
                      >
                        {entry.name}/
                      </button>
                    ) : (
                      entry.name
                    )}
                  </td>
                  <td>{KIND[entry.kind]}</td>
                  <td>{size(entry.size)}</td>
                  <td>{entry.blocked ? `Blocked: ${entry.reason}` : ""}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

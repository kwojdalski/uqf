import { useEffect, useId, useRef } from "react";
import {
  useTable,
  tableFeatures,
  rowSortingFeature,
  rowSelectionFeature,
  createSortedRowModel,
  sortFns,
} from "@tanstack/react-table";
import type { RemoteEntry } from "./api/services";

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
  // Compared as it will be shown: 1048575 B is 1023.999 KiB, which rounds
  // to "1024.0 KiB" - shown as 1.0 MiB instead (#2588).
  while (Number(value.toFixed(1)) >= 1024 && unit < units.length - 1) {
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
  name,
  label,
  checked,
  mixed = false,
  disabled,
  onChange,
}: {
  /** The control's activity name (activity.ts). */
  name: string;
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
      data-activity={name}
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
  onSelectionChange,
}: {
  entries: RemoteEntry[];
  disabled: boolean;
  onOpen: (name: string) => void;
  onSelectionChange?: (names: string[]) => void;
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
  const selectedRows = table.getSelectedRowModel().rows;
  useEffect(() => {
    onSelectionChange?.(selectedRows.map((row) => row.original.name));
  }, [selectedRows, onSelectionChange]);
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
                  name="files.select-all"
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
                      data-activity={`files.sort.${column.id}`}
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
                      name="files.select"
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
                        data-activity="files.open"
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

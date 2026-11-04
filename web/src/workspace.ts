/**
 * The workspace this tab reads (#2029): `local`, or a target the server's uqs
 * registry declares.
 *
 * The choice is this tab's alone. It is never sent to the server as a setting
 * - only named on each request, in the X-UQF-Target header `request` adds -
 * so another browser session cannot be moved by it, and none here can move
 * theirs. It is kept in sessionStorage so a reload keeps it, and nothing else
 * about a target is held here: the list comes from GET /workspaces, the
 * connection and its credentials stay on the server.
 *
 * Switching aborts every request still in flight for the old workspace, and
 * the views key their results by workspace, so nothing read from one target
 * is ever shown under another's name.
 */
import { useSyncExternalStore } from "react";

export const LOCAL = "local";
export const TARGET_HEADER = "X-UQF-Target";
const STORAGE_KEY = "uqf.workspace";

function stored(): string {
  try {
    return sessionStorage.getItem(STORAGE_KEY) || LOCAL;
  } catch {
    return LOCAL;
  }
}

let current = stored();
let controller = new AbortController();
const listeners = new Set<() => void>();

/** The workspace requests are made for now. */
export function currentWorkspace(): string {
  return current;
}

/** Aborted the moment the workspace changes: every request joins it. */
export function workspaceSignal(): AbortSignal {
  return controller.signal;
}

/** Make `name` this tab's workspace: abort what is in flight, then tell the
 *  views, which drop what they showed and ask again. */
export function selectWorkspace(name: string): void {
  const next = name || LOCAL;
  if (next === current) return;
  const previous = controller;
  controller = new AbortController();
  current = next;
  try {
    if (next === LOCAL) sessionStorage.removeItem(STORAGE_KEY);
    else sessionStorage.setItem(STORAGE_KEY, next);
  } catch {
    // storage refused: the choice still holds for this page
  }
  previous.abort(
    new DOMException(`workspace changed to ${next}`, "AbortError"),
  );
  for (const listener of listeners) listener();
}

export function subscribeWorkspace(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The workspace, re-rendering on a switch. */
export function useWorkspace(): string {
  return useSyncExternalStore(subscribeWorkspace, currentWorkspace);
}

/** The selected target, or null for the local stack: what a view that
 *  names its target itself (Services, Fleet's connection details) starts on. */
export function useWorkspaceTarget(): string | null {
  const workspace = useWorkspace();
  return workspace === LOCAL ? null : workspace;
}

/** For tests: back to local, with nothing stored. */
export function resetWorkspace(): void {
  selectWorkspace(LOCAL);
}

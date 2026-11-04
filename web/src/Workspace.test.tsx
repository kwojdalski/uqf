/**
 * Target-aware workspaces (#2029): each request names this tab's workspace,
 * a switch aborts what is in flight and clears what was shown, every result
 * says where it came from, and an unavailable target is an error - never the
 * local stack's answer.
 */
import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";
import { ApiError, request } from "./api";
import { mockWholeApi } from "./testing/activityFixtures";
import { useResource } from "./useResource";
import {
  LOCAL,
  TARGET_HEADER,
  currentWorkspace,
  resetWorkspace,
  selectWorkspace,
} from "./workspace";
import { WorkspaceSelector } from "./WorkspaceSelector";

afterEach(() => resetWorkspace());

function answer(body: unknown, status = 200, label?: string) {
  return {
    ok: status < 400,
    status,
    headers: new Headers(label ? { [TARGET_HEADER]: label } : {}),
    json: async () => body,
  };
}

/** The workspace header a fetch call carried. */
function named(call: unknown[]): string | undefined {
  const init = call[1] as RequestInit;
  return (init.headers as Record<string, string>)[TARGET_HEADER];
}

describe("the target travels with each request", () => {
  it("names local by default and the selected target after a switch", async () => {
    const fetcher = vi.fn(async () => answer({ ok: 1 }));
    vi.stubGlobal("fetch", fetcher);
    await request("/ops/queue", new AbortController().signal);
    selectWorkspace("prod-a");
    await request("/ops/queue", new AbortController().signal);
    expect(fetcher.mock.calls.map(named)).toEqual([LOCAL, "prod-a"]);
  });

  it("keeps the choice for this tab only, in sessionStorage", () => {
    selectWorkspace("prod-a");
    expect(sessionStorage.getItem("uqf.workspace")).toBe("prod-a");
    selectWorkspace(LOCAL);
    expect(sessionStorage.getItem("uqf.workspace")).toBeNull();
  });

  it("aborts a request still in flight when the workspace changes", async () => {
    let seen: AbortSignal | undefined;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_path: string, init: RequestInit) =>
          new Promise((_resolve, reject) => {
            seen = init.signal!;
            init.signal!.addEventListener("abort", () =>
              reject(init.signal!.reason),
            );
          }),
      ),
    );
    const pending = request("/query", new AbortController().signal, "{}");
    selectWorkspace("prod-a");
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(seen?.aborted).toBe(true);
  });

  it("refuses an answer labelled for another workspace", async () => {
    selectWorkspace("prod-a");
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => answer({ rows: [] }, 200, LOCAL)),
    );
    const failure = (await request(
      "/query",
      new AbortController().signal,
      "{}",
    ).catch((error: unknown) => error)) as ApiError;
    expect(failure).toBeInstanceOf(ApiError);
    expect(failure.message).toMatch(/came from local, not prod-a/);
  });
});

describe("views follow the workspace", () => {
  it("clears what was shown and labels the new result with its target", async () => {
    const fetcher = vi.fn(async (_path: string, init: RequestInit) => {
      const target = (init.headers as Record<string, string>)[TARGET_HEADER];
      return answer({ rows: [{ from: target }] }, 200, target);
    });
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() =>
      useResource<{ rows: { from: string }[] }>("/ops/queue", undefined, false),
    );
    await waitFor(() => expect(result.current.data?.rows[0].from).toBe(LOCAL));
    expect(result.current.target).toBe(LOCAL);
    act(() => selectWorkspace("prod-a"));
    // the local rows are gone at once, not kept as a "last result"
    expect(result.current.data).toBeUndefined();
    await waitFor(() =>
      expect(result.current.data?.rows[0].from).toBe("prod-a"),
    );
    expect(result.current.target).toBe("prod-a");
  });

  it("shows an unavailable target as an error naming it, never local data", async () => {
    const fetcher = vi.fn(async (_path: string, init: RequestInit) => {
      const target = (init.headers as Record<string, string>)[TARGET_HEADER];
      if (target === "prod-a")
        return answer(
          {
            error: "TargetUnavailable",
            detail: "target prod-a is unavailable: ssh exit 255",
            transient: false,
          },
          502,
          "prod-a",
        );
      return answer({ rows: [{ from: "local" }] }, 200, target);
    });
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() =>
      useResource<{ rows: { from: string }[] }>("/ops/queue", undefined, false),
    );
    await waitFor(() => expect(result.current.data).toBeDefined());
    act(() => selectWorkspace("prod-a"));
    await waitFor(() => expect(result.current.error).toBeDefined());
    expect(result.current.error?.message).toMatch(/prod-a is unavailable/);
    expect(result.current.data).toBeUndefined();
    expect(result.current.target).toBe("prod-a");
  });
});

describe("the selector", () => {
  it("lists local and the registry's targets, and switches this tab", async () => {
    mockWholeApi();
    render(<WorkspaceSelector />);
    const select = await screen.findByRole("combobox");
    expect(select).toHaveAttribute("data-activity", "workspace.select");
    await screen.findByRole("option", { name: "prod-a" });
    expect(screen.getByText("direct")).toBeInTheDocument();
    expect(screen.getByText("read-write")).toBeInTheDocument();
    fireEvent.change(select, { target: { value: "prod-a" } });
    expect(currentWorkspace()).toBe("prod-a");
    expect(await screen.findByText("ssh tunnel")).toBeInTheDocument();
    expect(screen.getByText("read-only")).toBeInTheDocument();
  });

  it("says a target is unavailable, in its own words", async () => {
    selectWorkspace("prod-a");
    const whole = mockWholeApi();
    const fallback = whole.getMockImplementation()!;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (path: string) =>
        path === "/workspace"
          ? answer(
              {
                error: "TargetUnavailable",
                detail:
                  "target prod-a is unavailable: could not open an ssh tunnel",
                transient: false,
              },
              502,
              "prod-a",
            )
          : fallback(path),
      ),
    );
    render(<WorkspaceSelector />);
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "target prod-a is unavailable: could not open an ssh tunnel",
    );
    expect(screen.getByRole("status")).toHaveTextContent("Unavailable");
  });

  it("labels each view's result with the workspace it came from", async () => {
    mockWholeApi();
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Queue/ }));
    expect(await screen.findAllByText("Local stack")).not.toHaveLength(0);
    act(() => selectWorkspace("prod-a"));
    expect(await screen.findAllByText("Target prod-a")).not.toHaveLength(0);
    expect(screen.queryByText("Local stack")).toBeNull();
  });

  it("points the Services reads at the selected target", async () => {
    const fetcher = mockWholeApi();
    selectWorkspace("prod-a");
    render(<App />);
    fireEvent.click(await screen.findByRole("button", { name: /Services/ }));
    // the target's files are browsed without picking it again
    await screen.findByRole("button", { name: "releases/" });
    expect(
      fetcher.mock.calls.some(([path]) =>
        String(path).startsWith("/services/prod-a/files"),
      ),
    ).toBe(true);
  });
});

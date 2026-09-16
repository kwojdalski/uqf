import { act, renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useResource } from "./useResource";

function response(body: unknown, status = 200) {
  return { ok: status < 400, status, json: async () => body };
}
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

describe("server-directed polling", () => {
  it("waits until completion, follows the returned cadence, and pauses without refetching", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockResolvedValue(response({ poll_seconds: 7, count: 1 }));
    vi.stubGlobal("fetch", fetcher);
    const { result, unmount } = renderHook(() => useResource("/ops/queue"));
    await settle();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6999);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    act(() => result.current.setPaused(true));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(21000);
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(result.current.data).toEqual({ poll_seconds: 7, count: 1 });
    act(() => result.current.refresh());
    await settle();
    expect(fetcher).toHaveBeenCalledTimes(3);
    unmount();
    await vi.advanceTimersByTimeAsync(21000);
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
  it("retains the last result and cadence when manual refresh hits a transient reload", async () => {
    vi.useFakeTimers();
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response({ poll_seconds: 2, value: "old" }))
      .mockResolvedValueOnce(
        response({ detail: "EOD reload", transient: true }, 503),
      )
      .mockResolvedValue(response({ poll_seconds: 2, value: "new" }));
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() =>
      useResource<{ value: string }>("/ops/queue"),
    );
    await settle();
    act(() => result.current.refresh());
    await settle();
    expect(result.current.data?.value).toBe("old");
    expect(result.current.error?.transient).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.data?.value).toBe("new");
    expect(result.current.error).toBeUndefined();
  });
  it("aborts superseded requests and ignores late responses for old filters", async () => {
    let resolveOld!: (value: unknown) => void;
    const fetcher = vi
      .fn()
      .mockImplementationOnce(
        () =>
          new Promise((resolve) => {
            resolveOld = resolve;
          }),
      )
      .mockResolvedValue(response({ poll_seconds: 20, value: "new" }));
    vi.stubGlobal("fetch", fetcher);
    const { result, rerender } = renderHook(
      ({ path }) => useResource<{ value: string }>(path),
      { initialProps: { path: "/coverage?dataset=old" } },
    );
    const oldSignal = fetcher.mock.calls[0][1].signal;
    rerender({ path: "/coverage?dataset=new" });
    await waitFor(() => expect(result.current.data?.value).toBe("new"));
    expect(oldSignal.aborted).toBe(true);
    await act(async () =>
      resolveOld(response({ poll_seconds: 1, value: "old" })),
    );
    expect(result.current.data?.value).toBe("new");
  });
  it("does not overlap slow requests or retry permanent coverage failures", async () => {
    vi.useFakeTimers();
    let resolve!: (value: unknown) => void;
    const fetcher = vi.fn().mockImplementation(
      () =>
        new Promise((done) => {
          resolve = done;
        }),
    );
    vi.stubGlobal("fetch", fetcher);
    const { result } = renderHook(() => useResource("/query", "{}"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () =>
      resolve(response({ detail: "missing [a,b)", transient: false }, 409)),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(result.current.error?.status).toBe(409);
  });
});

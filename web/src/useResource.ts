import { useEffect, useRef, useState } from "react";
import { ApiError, request } from "./api";

export function useResource<T>(
  path: string | null,
  body?: string,
  poll = true,
) {
  const [revision, setRevision] = useState(0);
  const [paused, updatePaused] = useState(false);
  const pausedRef = useRef(false);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const lastCadence = useRef({ key: "", seconds: 5 });
  const key = `${path}\n${body ?? ""}`;
  const [state, setState] = useState<{
    key?: string;
    data?: T;
    error?: ApiError;
    loading: boolean;
    updated?: Date;
    cadence?: number;
  }>({ loading: false });
  useEffect(() => {
    if (!path) {
      setState({ loading: false });
      return;
    }
    let active = true;
    const controller = new AbortController();
    if (lastCadence.current.key !== key)
      lastCadence.current = { key, seconds: 5 };
    let cadence = lastCadence.current.seconds;
    setState((previous) =>
      previous.key === key
        ? { ...previous, loading: true }
        : { key, loading: true },
    );
    async function load() {
      setState((previous) => ({ ...previous, loading: true }));
      try {
        const data = await request<T>(path!, controller.signal, body);
        if (!active) return;
        const seconds = (data as { poll_seconds?: number }).poll_seconds;
        const canPoll =
          typeof seconds === "number" &&
          Number.isFinite(seconds) &&
          seconds > 0;
        if (canPoll) {
          cadence = seconds;
          lastCadence.current = { key, seconds };
        }
        setState({
          key,
          data,
          loading: false,
          updated: new Date(),
          cadence: canPoll ? cadence : undefined,
        });
        if (poll && canPoll && !pausedRef.current)
          timer.current = setTimeout(load, cadence * 1000);
      } catch (error) {
        if (!active) return;
        const failure =
          error instanceof ApiError ? error : new ApiError(String(error));
        setState((previous) => ({
          ...previous,
          error: failure,
          loading: false,
        }));
        if (poll && failure.transient && !pausedRef.current)
          timer.current = setTimeout(load, cadence * 1000);
      }
    }
    void load();
    return () => {
      active = false;
      clearTimeout(timer.current);
      controller.abort();
    };
  }, [path, body, key, poll, revision]);
  function setPaused(value: boolean) {
    pausedRef.current = value;
    updatePaused(value);
    clearTimeout(timer.current);
    if (!value) setRevision((value) => value + 1);
  }
  return {
    ...state,
    paused,
    setPaused,
    refresh: () => setRevision((value) => value + 1),
  };
}

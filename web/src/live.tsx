import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { getStatus, subscribe, tool, type Envelope, type Status } from "./api";

interface Live {
  status: Status | null;
  version: number;        // bumps on every warehouse refresh; data hooks depend on it
  connected: boolean;
  week: string | null;
  setWeek: (w: string) => void;
}

const LiveContext = createContext<Live>({ status: null, version: 0, connected: false, week: null, setWeek: () => {} });

export function LiveProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [version, setVersion] = useState(0);
  const [connected, setConnected] = useState(false);
  // A link can open on a given week (?week=2026-W41); otherwise follow the latest week.
  const linked = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("week") : null;
  const [week, setWeekState] = useState<string | null>(linked);
  const [pinned, setPinned] = useState(linked !== null);   // user picked a week; don't follow the latest

  useEffect(() => {
    let alive = true;
    getStatus().then((s) => alive && setStatus(s)).catch(() => {});
    const off = subscribe(() => {
      setVersion((v) => v + 1);
      getStatus().then((s) => alive && setStatus(s)).catch(() => {});
    }, setConnected);
    return () => {
      alive = false;
      off();
    };
  }, []);

  useEffect(() => {
    if (!status) return;                       // keep a linked week until the week list is known
    const latest = status.weeks.at(-1) ?? null;
    if (!pinned || !status.weeks.includes(week ?? "")) setWeekState(latest);
  }, [status, pinned, week]);

  const setWeek = (w: string) => {
    setPinned(w !== status?.weeks.at(-1));
    setWeekState(w);
  };

  return <LiveContext.Provider value={{ status, version, connected, week, setWeek }}>{children}</LiveContext.Provider>;
}

export const useLive = () => useContext(LiveContext);

/** Fetch a governed tool; re-fetches when its parameters change or the warehouse refreshes. */
export function useTool(name: string, params: Record<string, any> | null) {
  const { version } = useLive();
  const [state, setState] = useState<{ data: Envelope | null; error: string | null; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  });
  const key = params ? JSON.stringify(params) : null;
  useEffect(() => {
    if (key === null) return;
    let alive = true;
    setState((s) => ({ ...s, loading: true }));
    tool(name, JSON.parse(key))
      .then((data) => alive && setState({ data, error: null, loading: false }))
      .catch((e: Error) => alive && setState({ data: null, error: e.message, loading: false }));
    return () => {
      alive = false;
    };
  }, [name, key, version]);
  return state;
}

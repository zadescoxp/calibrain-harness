"use client";
// Tiny external store. High-frequency data (ticks, book) lives in its own slices so a tick
// re-renders only the components that read that slice, not the whole terminal.
import { useSyncExternalStore } from "react";

export type Tick = { last?: string; mark?: string; bid?: string; ask?: string; testnet_last?: string; pct24h?: number | null; ts: number };
export type Book = { symbol: string; bids: [number, number][]; asks: [number, number][]; ts: number };
export type Bar = { symbol: string; tf: string; time: number; open: number; high: number; low: number; close: number; volume: number };

export type State = {
  ov: any | null;
  mon: any | null;
  mkt: any | null;
  decisions: any[];
  latest: Record<string, any>;
  news: { forecasts: any[]; headlines: any[] };
  perf: Record<string, [number, number][]>;
  symbol: string | null;
  tf: string;
  view: "mon" | "eval" | "valid" | "trades" | "news" | "log";
  ticks: Record<string, Tick>;
  book: Book | null;
  socket: { connected: boolean; lastMsg: number; latencyMs: number | null; msgs: number };
  modal: { kind: "decision" | "state" | "keys" | "help" | "tf" | "universe" | "strategy"; data?: any } | null;
  cmdHint: string | null;
  rev: { trades: number; events: number; eval: number };
};

let state: State = {
  ov: null, mon: null, mkt: null, decisions: [], latest: {}, news: { forecasts: [], headlines: [] }, perf: {},
  symbol: null, tf: "5", view: "mon", ticks: {}, book: null,
  socket: { connected: false, lastMsg: 0, latencyMs: null, msgs: 0 }, modal: null, cmdHint: null,
  rev: { trades: 0, events: 0, eval: 0 },
};
const listeners = new Set<() => void>();

export const getState = () => state;
export function setState(patch: Partial<State> | ((s: State) => Partial<State>)) {
  const p = typeof patch === "function" ? patch(state) : patch;
  state = { ...state, ...p };
  listeners.forEach((l) => l());
}
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

/** Subscribe to one slice. The selector must return a stable reference (a store field), not a new object. */
export function useStore<T>(sel: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => sel(state), () => sel(state));
}

/** Label of the active classification model ("Kairos", "Jev", ...). */
export function useModelLabel(): string {
  return useStore((s) => s.ov?.kairos?.label) || "Model";
}

// Bars go straight to the chart (no React render): the chart registers a sink.
type BarSink = (b: Bar) => void;
let barSink: BarSink | null = null;
export const setBarSink = (fn: BarSink | null) => (barSink = fn);
export const pushBar = (b: Bar) => barSink?.(b);

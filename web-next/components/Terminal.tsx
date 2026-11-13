"use client";
import { useEffect } from "react";
import { connectSocket, loadDecisions, loadMarket, loadMonitor, loadNews, loadOverview, loadPerf } from "@/lib/data";
import { setState, useStore } from "@/lib/store";
import { Alerts, Clocks, CommandBar, FKeys, StatusLine } from "./TopBar";
import { MonitorView } from "./Monitor";
import { EvalView, LogView, NewsView, TradesView } from "./Views";
import { Modals } from "./Modals";
import { ValidationView } from "./Validation";
import { Tip } from "./Tip";

/** Polls only what has no push channel; everything that moves arrives over /ws. */
function every(ms: number, fn: () => Promise<unknown>) {
  const id = setInterval(() => {
    if (!document.hidden) fn().catch(() => {});
  }, ms);
  return () => clearInterval(id);
}

export default function Terminal() {
  const view = useStore((s) => s.view);
  const ready = useStore((s) => !!s.ov);

  useEffect(() => {
    let stop: (() => void)[] = [];
    (async () => {
      await loadOverview().catch(() => {});
      await Promise.all([loadMonitor(), loadMarket(), loadDecisions(), loadPerf(), loadNews()].map((p) => p.catch(() => {})));
      stop = [
        connectSocket(),
        every(10_000, loadOverview), // settings, budgets, labelled count (account itself is pushed)
        every(5_000, loadMonitor), // universe map, macro, tape, headlines, sparklines (ticks are pushed)
        every(10_000, loadMarket), // state preview + derivatives block (candles/book are pushed)
        every(30_000, loadPerf),
        every(60_000, loadNews),
        every(60_000, loadDecisions), // labels land in the background
      ];
    })();
    const onKey = (e: KeyboardEvent) => {
      const fk = ({ F1: "mon", F2: "eval", F3: "trades", F4: "news", F5: "log", F6: "valid" } as const)[e.key as "F1"];
      if (fk) {
        e.preventDefault();
        setState({ view: fk });
        return;
      }
      const tag = (document.activeElement as HTMLElement | null)?.tagName || "";
      if ((e.key === "/" || e.key === "`") && !/INPUT|SELECT|TEXTAREA/.test(tag) && !document.querySelector("dialog[open]")) {
        e.preventDefault();
        (document.getElementById("cmd") as HTMLInputElement | null)?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      stop.forEach((f) => f());
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <>
      <Clocks />
      <CommandBar />
      <FKeys />
      <Alerts />
      {ready ? (
        <>
          <MonitorView hidden={view !== "mon"} />
          {view === "eval" && <EvalView />}
          {view === "valid" && <ValidationView />}
          {view === "trades" && <TradesView />}
          {view === "news" && <NewsView />}
          {view === "log" && <LogView />}
        </>
      ) : (
        <div className="empty">Connecting to the local terminal service on 127.0.0.1:8800…</div>
      )}
      <StatusLine />
      <Modals />
      <Tip />
    </>
  );
}

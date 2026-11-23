"use client";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  CandlestickSeries, createChart, createSeriesMarkers, HistogramSeries, LineSeries,
  type IChartApi, type ISeriesApi, type ISeriesMarkersPluginApi, type SeriesMarker, type SeriesType, type Time, type UTCTimestamp,
} from "lightweight-charts";
import { ChartSpline } from "lucide-react";
import { setBarSink, useStore, type Bar } from "@/lib/store";
import { cssVar, decimalsOf, fx, SERIES_C, sg, short, ud, usd } from "@/lib/format";
import {
  bollinger, ema, INDICATORS, macd, rsi, setIndicator, sma, useIndicators, vwap, type Candle, type IndicatorId, type Pt,
} from "@/lib/indicators";
import { Switch } from "./ui";

const UP = "#22c55e", DN = "#ef4444";
const C: Record<string, string> = {
  ema20: "#ff8a1f", ema50: "#7aa7ff", ema200: "#e8c547", sma50: "#c48bff", bb: "#8a93a3", vwap: "#3fd0e0",
  rsi: "#c48bff", macd: "#7aa7ff", macdSig: "#ff8a1f",
};

const baseOpts = () => ({
  autoSize: true,
  layout: { background: { color: "transparent" }, textColor: cssVar("--muted"), fontFamily: cssVar("--mono"), fontSize: 11,
    panes: { separatorColor: "rgba(255,255,255,.07)", separatorHoverColor: "rgba(255,255,255,.12)", enableResize: true } },
  grid: { vertLines: { color: "rgba(255,255,255,.035)" }, horzLines: { color: "rgba(255,255,255,.035)" } },
  timeScale: { timeVisible: true, secondsVisible: false, borderColor: "rgba(255,255,255,.07)", rightOffset: 4 },
  rightPriceScale: { borderColor: "rgba(255,255,255,.07)" },
  crosshair: { mode: 0, vertLine: { color: "rgba(255,255,255,.25)", style: 2, labelBackgroundColor: "#2a2c31" }, horzLine: { color: "rgba(255,255,255,.25)", style: 2, labelBackgroundColor: "#2a2c31" } },
  localization: { timeFormatter: (t: number) => new Date(t * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }) },
});

type Line = { key: string; series: ISeriesApi<SeriesType>; values: (b: Candle[]) => Pt[]; color?: (v: number, i: number) => string };
const line = { lineWidth: 1 as const, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false };

/** Price chart: green / red candles, the indicators chosen in the Indicators menu, and the model's decisions at their price. */
export function PriceChart() {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const candles = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const marks = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const lines = useRef<Line[]>([]);
  const bars = useRef<Candle[]>([]);
  const byTime = useRef(new Map<number, Candle>());
  const key = useRef("");
  const [ohlc, setOhlc] = useState<Candle | null>(null);
  const [pd, setPd] = useState(2);
  const mkt = useStore((s) => s.mkt);
  const decisions = useStore((s) => s.decisions);
  const ind = useIndicators();

  useEffect(() => {
    const ch = createChart(host.current!, baseOpts() as any);
    const cs = ch.addSeries(CandlestickSeries, { upColor: UP, downColor: DN, borderVisible: false, wickUpColor: UP, wickDownColor: DN,
      priceLineColor: cssVar("--accent"), priceLineStyle: 2 });
    marks.current = createSeriesMarkers(cs, []);
    ch.subscribeCrosshairMove((p) => {
      const b = p?.time ? byTime.current.get(p.time as number) : null;
      setOhlc(b || bars.current[bars.current.length - 1] || null);
    });
    chart.current = ch;
    candles.current = cs;
    return () => {
      setBarSink(null);
      ch.remove();
      chart.current = candles.current = marks.current = null;
      lines.current = [];
    };
  }, []);

  const point = (b: Candle, v: Pt, l: Line, i: number) =>
    v === null || !Number.isFinite(v) ? { time: b.time as UTCTimestamp } : { time: b.time as UTCTimestamp, value: v, ...(l.color ? { color: l.color(v, i) } : {}) };
  const drawAll = () => {
    const b = bars.current;
    for (const l of lines.current) {
      const vals = l.values(b);
      l.series.setData(b.map((x, i) => point(x, vals[i], l, i)) as any);
    }
  };
  const drawLast = () => {
    const b = bars.current, i = b.length - 1;
    if (i < 0) return;
    for (const l of lines.current) l.series.update(point(b[i], l.values(b)[i], l, i) as any);
  };

  // (re)build the indicator series whenever the selection changes
  useEffect(() => {
    const ch = chart.current, cs = candles.current;
    if (!ch || !cs) return;
    for (const l of lines.current) ch.removeSeries(l.series);
    lines.current = [];
    for (let i = ch.panes().length - 1; i > 0; i--) ch.removePane(i);
    const add = (k: string, def: any, opts: any, values: Line["values"], pane = 0, color?: Line["color"]) => {
      const s = ch.addSeries(def, opts, pane) as ISeriesApi<SeriesType>;
      lines.current.push({ key: k, series: s, values, color });
      return s;
    };
    const closes = (b: Candle[]) => b.map((x) => x.close);
    if (ind.includes("vol")) {
      add("vol", HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false },
        (b) => b.map((x) => x.volume), 0, (_v, i) => (bars.current[i] && bars.current[i].close >= bars.current[i].open ? "rgba(34,197,94,.28)" : "rgba(239,68,68,.28)"));
      ch.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    }
    cs.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: ind.includes("vol") ? 0.2 : 0.06 } });
    for (const p of [20, 50, 200] as const) if (ind.includes(`ema${p}` as IndicatorId)) add(`ema${p}`, LineSeries, { color: C[`ema${p}`], ...line }, (b) => ema(closes(b), p));
    if (ind.includes("sma50")) add("sma50", LineSeries, { color: C.sma50, ...line }, (b) => sma(closes(b), 50));
    if (ind.includes("bb")) {
      add("bbU", LineSeries, { color: C.bb, ...line, lineStyle: 2 }, (b) => bollinger(closes(b)).up);
      add("bbM", LineSeries, { color: C.bb, ...line }, (b) => bollinger(closes(b)).mid);
      add("bbL", LineSeries, { color: C.bb, ...line, lineStyle: 2 }, (b) => bollinger(closes(b)).lo);
    }
    if (ind.includes("vwap")) add("vwap", LineSeries, { color: C.vwap, ...line, lineWidth: 2 }, (b) => vwap(b));
    let pane = 1;
    if (ind.includes("rsi")) {
      const s = add("rsi", LineSeries, { color: C.rsi, lineWidth: 1, priceLineVisible: false, lastValueVisible: true }, (b) => rsi(closes(b)), pane);
      for (const lvl of [70, 30]) s.createPriceLine({ price: lvl, color: "rgba(255,255,255,.18)", lineWidth: 1, lineStyle: 2, axisLabelVisible: false, title: "" });
      pane++;
    }
    if (ind.includes("macd")) {
      add("macdH", HistogramSeries, { priceLineVisible: false, lastValueVisible: false }, (b) => macd(closes(b)).hist, pane,
        (v) => (v >= 0 ? "rgba(34,197,94,.45)" : "rgba(239,68,68,.45)"));
      add("macd", LineSeries, { color: C.macd, ...line, lastValueVisible: true }, (b) => macd(closes(b)).line, pane);
      add("macdS", LineSeries, { color: C.macdSig, ...line }, (b) => macd(closes(b)).signal, pane);
      pane++;
    }
    const panes = ch.panes();
    if (panes.length > 1) { panes[0].setStretchFactor(3); for (let i = 1; i < panes.length; i++) panes[i].setStretchFactor(1); }
    drawAll();
  }, [ind]);

  // full reload when the symbol/timeframe (or its REST snapshot) changes
  useEffect(() => {
    const ch = chart.current, cs = candles.current;
    if (!ch || !cs || !mkt?.bars?.length) return;
    const k = `${mkt.symbol}:${mkt.tf}`;
    const reset = k !== key.current;
    key.current = k;
    const d = Math.max(decimalsOf(mkt.ticker.lastPrice), 0);
    setPd(d);
    cs.applyOptions({ priceFormat: { type: "price", precision: d, minMove: Math.pow(10, -d) } });
    const live = !reset ? bars.current[bars.current.length - 1] : undefined; // keep a newer bar the socket already delivered
    let b: Candle[] = mkt.bars;
    if (live && b.length && live.time >= b[b.length - 1].time) b = [...b.filter((x) => x.time < live.time), live];
    bars.current = b;
    byTime.current = new Map(b.map((x) => [x.time, x]));
    cs.setData(b.map((x) => ({ time: x.time as UTCTimestamp, open: x.open, high: x.high, low: x.low, close: x.close })));
    drawAll();
    if (reset) ch.timeScale().setVisibleLogicalRange({ from: Math.max(0, b.length - 140), to: b.length + 4 });
    setOhlc(b[b.length - 1]);

    setBarSink((bar: Bar) => {
      if (bar.symbol !== mkt.symbol || bar.tf !== mkt.tf || !candles.current) return;
      const arr = bars.current, last = arr[arr.length - 1];
      if (last && bar.time < last.time) return;
      const c: Candle = { time: bar.time, open: bar.open, high: bar.high, low: bar.low, close: bar.close, volume: bar.volume };
      const isNew = !last || bar.time > last.time;
      if (isNew) arr.push(c);
      else arr[arr.length - 1] = c;
      byTime.current.set(c.time, c);
      candles.current.update({ time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close });
      drawLast();
      setOhlc((o) => (!o || o.time === c.time || o.time === last?.time ? c : o));
    });
  }, [mkt]);

  // model decisions, pinned to the price they were made at (a decision just after a bar close describes that bar)
  useEffect(() => {
    const m = marks.current;
    if (!m) return;
    if (!mkt?.bars?.length || !ind.includes("signals")) { m.setMarkers([]); return; }
    const tfs = Number(mkt.tf) * 60, first = mkt.bars[0].time, withFlat = ind.includes("flat");
    const out = new Map<number, SeriesMarker<Time>>();
    for (const d of decisions) {
      if (d.symbol !== mkt.symbol || d.error_kind || d.probe || d.ts < first) continue;
      if (d.action === "flat" && !withFlat) continue;
      const t = Math.floor((d.ts - 10) / tfs) * tfs;
      const bar = byTime.current.get(t);
      if (!bar) continue;
      const price = Number(d.price) > 0 ? Number(d.price) : bar.close;
      if (out.has(t) && d.action === "flat") continue; // a trade signal on the same bar wins
      out.set(t, d.action === "long"
        ? { time: t as UTCTimestamp, position: "atPriceBottom", price, shape: "arrowUp", color: UP, size: 1.2 }
        : d.action === "short"
          ? { time: t as UTCTimestamp, position: "atPriceTop", price, shape: "arrowDown", color: DN, size: 1.2 }
          : { time: t as UTCTimestamp, position: "atPriceMiddle", price, shape: "circle", color: "rgba(160,166,176,.7)", size: 0.45 });
    }
    m.setMarkers([...out.values()].sort((a, b) => (a.time as number) - (b.time as number)));
  }, [decisions, mkt, ind]);

  const chg = ohlc ? ohlc.close - ohlc.open : 0;
  const legend = INDICATORS.filter((i) => ind.includes(i.id) && i.group !== "Model" && i.id !== "vol");
  return (
    <div className="pb chart-wrap">
      <div ref={host} className="chart-host" />
      {ohlc && (
        <div className="ohlc">
          <div>
            <b>O</b> {fx(ohlc.open, pd)} <b>H</b> {fx(ohlc.high, pd)} <b>L</b> {fx(ohlc.low, pd)} <b>C</b> <span className={ud(chg)}>{fx(ohlc.close, pd)}</span> <b>V</b> {usd(ohlc.volume)}{" "}
            <span className="muted">{new Date(ohlc.time * 1000).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
          </div>
          {legend.length > 0 && (
            <div className="ind-legend">
              {legend.map((i) => <span key={i.id}><i style={{ background: C[i.id] || "#8a93a3" }} />{i.label}</span>)}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** "Indicators" button in the chart header: a menu of switches. The choice is remembered in this browser. */
export function IndicatorsMenu() {
  const ind = useIndicators();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState({ top: 0, left: 0 });
  const ref = useRef<HTMLDivElement>(null);
  const pop = useRef<HTMLDivElement>(null);
  const toggle = () => {
    const r = ref.current!.getBoundingClientRect();
    setPos({ top: r.bottom + 6, left: Math.max(8, Math.min(r.left, window.innerWidth - 300)) });
    setOpen(!open);
  };
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node) && !pop.current?.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopPropagation(); setOpen(false); } };
    document.addEventListener("mousedown", off);
    document.addEventListener("keydown", esc, true);
    return () => { document.removeEventListener("mousedown", off); document.removeEventListener("keydown", esc, true); };
  }, [open]);
  const count = ind.filter((x) => x !== "flat").length;
  const groups = [...new Set(INDICATORS.map((i) => i.group))];
  return (
    <div className="ind-menu" ref={ref}>
      <button type="button" className={`btn sm${open ? " on" : ""}`} aria-expanded={open} onClick={toggle}>
        <ChartSpline size={14} strokeWidth={1.7} aria-hidden="true" />Indicators{count > 0 && <span className="ind-count">{count}</span>}
      </button>
      {open && createPortal(
        <div className="ind-pop" ref={pop} role="dialog" aria-label="Chart indicators" style={{ top: pos.top, left: pos.left }}>
          {groups.map((g) => (
            <div key={g} className="ind-group">
              <div className="ind-gh">{g}</div>
              {INDICATORS.filter((i) => i.group === g).map((i) => {
                const disabled = i.id === "flat" && !ind.includes("signals");
                return (
                  <label key={i.id} className={`ind-row${disabled ? " dis" : ""}`} data-tip={i.help}>
                    {C[i.id] && i.group !== "Model" ? <i className="sw" style={{ background: C[i.id] }} /> : <i className="sw none" />}
                    <span>{i.label}</span>
                    <Switch size="sm" tone="neutral" checked={ind.includes(i.id) && !disabled} disabled={disabled} label={i.label}
                      onChange={(v) => { setIndicator(i.id, v); if (i.id === "signals" && !v) setIndicator("flat", false); }} />
                  </label>
                );
              })}
            </div>
          ))}
          <button type="button" className="btn sm ghost ind-clear" onClick={() => INDICATORS.forEach((i) => setIndicator(i.id, false))}>Remove all</button>
        </div>, document.body,
      )}
    </div>
  );
}

export function PerfChart() {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<Record<string, ISeriesApi<"Line">>>({});
  const perf = useStore((s) => s.perf);
  const order = usePerfOrder();

  useEffect(() => {
    chart.current = createChart(host.current!, { ...(baseOpts() as any), handleScroll: false, handleScale: false });
    return () => {
      chart.current?.remove();
      chart.current = null;
      series.current = {};
    };
  }, []);
  useEffect(() => {
    if (!chart.current) return;
    for (const [sym, ser] of Object.entries(series.current)) {
      if (!order.includes(sym)) { chart.current.removeSeries(ser); delete series.current[sym]; }
    }
    order.forEach((sym, i) => {
      if (!perf[sym]) return;
      if (!series.current[sym])
        series.current[sym] = chart.current!.addSeries(LineSeries, {
          color: cssVar(SERIES_C[i % 5]), lineWidth: 2, title: short(sym), priceLineVisible: false, crosshairMarkerRadius: 3,
          priceFormat: { type: "custom", minMove: 0.01, formatter: (v: number) => sg(v, 2, "%") },
        });
      series.current[sym].setData(perf[sym].map(([t, v]) => ({ time: t as UTCTimestamp, value: v })));
    });
    chart.current.timeScale().fitContent();
  }, [perf, order]);
  return <div className="pb chart-wrap"><div ref={host} className="chart-host" /></div>;
}

/** At most 5 series (the palette has 5 hues; colours are never cycled): the loaded symbol plus the first others in the universe. */
function usePerfOrder(): string[] {
  const all: string[] = useStore((s) => s.ov?.settings.symbols) || [];
  const sel = useStore((s) => s.symbol);
  const key = `${sel}|${all.join(",")}`;
  const ref = useRef<{ key: string; v: string[] }>({ key: "", v: [] });
  if (ref.current.key !== key) {
    const rest = all.filter((x) => x !== sel);
    ref.current = { key, v: (sel && all.includes(sel) ? [sel, ...rest] : rest).slice(0, 5).sort((a, b) => all.indexOf(a) - all.indexOf(b)) };
  }
  return ref.current.v;
}

export function PerfLegend() {
  const perf = useStore((s) => s.perf);
  const order = usePerfOrder();
  return (
    <span className="legend">
      {order.map((s, i) => {
        const v = perf[s]?.[perf[s].length - 1]?.[1];
        return (
          <span key={s}>
            <span style={{ color: `var(${SERIES_C[i % 5]})` }}>●</span> <span className="w">{short(s)}</span> <span className={ud(v)}>{sg(v, 2, "%")}</span>&nbsp;&nbsp;
          </span>
        );
      })}
    </span>
  );
}

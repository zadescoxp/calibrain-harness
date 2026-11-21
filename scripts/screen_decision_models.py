"""Screen OpenRouter decision models on this harness's own question set, with live market states.

No labels are needed: it measures what can be checked right now.
- contract: requests that failed or answers the strict validator rejected
- mirror error: the mirrored market should get swapped answers (long<->short brackets, P(up) -> 1-P(up),
  return bins reversed, big_move unchanged). Mean absolute probability error; lower = reads the state
  consistently instead of leaning on noise or on option order.
- repeat error: the identical request asked twice; lower = stable.
- latency and cost per request (from OpenRouter's usage.cost).

Forecasting skill (Brier vs outcomes) still needs a forward test: this only removes models that are
broken, noisy or slow before spending hours on them.

    .venv/bin/python scripts/screen_decision_models.py                 # default candidate list
    .venv/bin/python scripts/screen_decision_models.py liquid/d1.1 typesafe/jev-1.14
"""
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiohttp  # noqa: E402

from ctb.config import load_settings  # noqa: E402
from ctb.features import build_state, mirror_state  # noqa: E402
from ctb.kairos.client import KairosError, make_client  # noqa: E402
from ctb.kairos.parse import validate  # noqa: E402
from ctb.kairos.questions import build_questions  # noqa: E402
from ctb.market.bybit_public import BybitPublicFeed  # noqa: E402
from ctb.market.store import MarketStore  # noqa: E402
from ctb.models import MODELS  # noqa: E402

# Every decision model on OpenRouter on 2026-11-14 that takes the standard state format, newest
# version of each family only (aliases, superseded versions, and Respan/ScaleDown with their own
# state formats left out).
CANDIDATES = [
    "typesafe/jev-1.14", "liquid/d1.1", "liquid/d1-ultra", "perplexity/pplx-decider-v1.3:free",
    "perplexity/pplx-decider-flash-v1.3:free", "perplexity/pplx-decider-v1.2", "google/gemini-decision-1",
    "openai/gpt-6-luna-decisions", "microsoft/microsoft-decision-1", "nace-ai/drex-max-finance", "nace-ai/drex-max",
    "nace-ai/drex-v2.0", "heygen/heygen-decision-1", "heygen/heygen-decision-1-turbo", "cloudflare/clairic",
    "cloudflare/clef", "cloudflare/clef-omni", "cloudflare/clef-flash", "baseten/mammoth-1", "baseten/elephant-1",
    "baseten/joey-1", "morph/reflex", "parallel/decisions-fast", "upstage/solar-decide",
    "togethercomputer/tev1-4b-experimental", "jaredpalmer/kev-4b",
]
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "HYPEUSDT"]


def mirror_error(o: dict, m: dict) -> float | None:
    errs = []
    for a, b in (("long_bracket", "short_bracket"), ("short_bracket", "long_bracket")):
        if a in o and b in m:
            errs += [abs(o[a]["probs"][k] - m[b]["probs"][k]) for k in o[a]["probs"]]
    if "direction" in o and "direction" in m:
        errs.append(abs(o["direction"]["p"] - (1 - m["direction"]["p"])))
    if "big_move" in o and "big_move" in m:
        errs.append(abs(o["big_move"]["p"] - m["big_move"]["p"]))
    if "return_distribution" in o and "return_distribution" in m:
        op, mp = o["return_distribution"]["probs"], m["return_distribution"]["probs"]
        errs += [abs(op[f"bin_{i}"] - mp[f"bin_{6 - i}"]) for i in range(7)]
    return statistics.fmean(errs) if errs else None


def repeat_error(a: dict, b: dict) -> float | None:
    errs = []
    for q in a:
        if q not in b:
            continue
        if "probs" in a[q] and isinstance(a[q]["probs"], dict):
            errs += [abs(a[q]["probs"][k] - b[q]["probs"].get(k, 0)) for k in a[q]["probs"]]
        elif "p" in a[q]:
            errs.append(abs(a[q]["p"] - b[q]["p"]))
    return statistics.fmean(errs) if errs else None


async def ask(client, state, questions, sem):
    async with sem:
        t = time.perf_counter()
        try:
            res = await client.ask(state, questions)
        except KairosError as exc:
            return {"error": f"{exc.kind}: {exc.message[:120]}", "latency": time.perf_counter() - t}
        parsed, issues = validate(res.answers, questions)
        return {"parsed": parsed, "issues": issues, "latency": res.latency_ms / 1000,
                "cost": (res.usage or {}).get("cost"), "served": res.metadata.get("model")}


async def screen(model: str, session, cases, sem) -> dict:
    client = make_client(MODELS["ordecide"], session, model=model, timeout_s=90, max_retries=1)
    jobs = []
    for sym, state, mstate, qs in cases:
        jobs.append(ask(client, state, qs, sem))
        jobs.append(ask(client, mstate, qs, sem))
    sym0, state0, _, qs0 = cases[0]
    jobs.append(ask(client, state0, qs0, sem))  # repeat of the first real request
    out = await asyncio.gather(*jobs)
    reals, mirrors, rep = out[0:-1:2], out[1:-1:2], out[-1]
    calls = len(out)
    failed = [r for r in out if "error" in r]
    issues = sum(len(r.get("issues", [])) for r in out if "error" not in r)
    m_err = [mirror_error(a["parsed"], b["parsed"]) for a, b in zip(reals, mirrors) if "parsed" in a and "parsed" in b]
    m_err = [x for x in m_err if x is not None]
    r_err = repeat_error(reals[0]["parsed"], rep["parsed"]) if "parsed" in reals[0] and "parsed" in rep else None
    ok = [r for r in out if "error" not in r]
    costs = [r["cost"] for r in ok if isinstance(r.get("cost"), (int, float))]
    dirs = [r["parsed"]["direction"]["p"] for r in reals if "parsed" in r and "direction" in r["parsed"]]
    return {
        "model": model, "served": next((r.get("served") for r in ok if r.get("served")), None),
        "ok": f"{len(ok)}/{calls}", "contract_issues": issues,
        "mirror_err": round(statistics.fmean(m_err), 4) if m_err else None,
        "repeat_err": round(r_err, 4) if r_err is not None else None,
        "p50_s": round(statistics.median(r["latency"] for r in ok), 2) if ok else None,
        "cost_per_1k": round(1000 * statistics.fmean(costs), 4) if costs else None,
        "p_up_spread": round(max(dirs) - min(dirs), 3) if len(dirs) > 1 else None,
        "first_error": failed[0]["error"] if failed else None,
    }


async def main(models: list[str]) -> None:
    s = load_settings()
    s.symbols = SYMBOLS
    async with aiohttp.ClientSession() as session:
        store = MarketStore(SYMBOLS)
        await BybitPublicFeed(store, session).warmup()
        cases = []
        for sym in SYMBOLS:
            state, meta = build_state(store, sym, s)
            cases.append((sym, state, mirror_state(state), build_questions(meta["sl_bp"], meta["tp_bp"], meta["horizon_min"])))
        print(f"{len(models)} models x {2 * len(cases) + 1} requests, states from {', '.join(SYMBOLS)}", file=sys.stderr)
        sem = asyncio.Semaphore(12)
        rows = await asyncio.gather(*(screen(m, session, cases, sem) for m in models))
    rows.sort(key=lambda r: (r["mirror_err"] is None, r["contract_issues"] > 0, r["mirror_err"] or 9))
    out = Path(__file__).resolve().parents[1] / "data" / "model_screen.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"ts": time.time(), "symbols": SYMBOLS, "rows": rows}, indent=1))
    hdr = f"{'model':42} {'ok':>6} {'issues':>6} {'mirror':>7} {'repeat':>7} {'p50 s':>6} {'$/1k':>7} {'P(up) spread':>12}"
    print(hdr)
    for r in rows:
        print(f"{r['model']:42} {r['ok']:>6} {r['contract_issues']:>6} {str(r['mirror_err']):>7} {str(r['repeat_err']):>7} "
              f"{str(r['p50_s']):>6} {str(r['cost_per_1k']):>7} {str(r['p_up_spread']):>12}")
    for r in rows:
        if r["first_error"]:
            print(f"  {r['model']}: {r['first_error']}")
    print(f"saved {out}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:] or CANDIDATES))

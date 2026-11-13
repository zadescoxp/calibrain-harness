<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/calibrain-harness-logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/brand/calibrain-harness-logo-light.svg">
    <img alt="Calibrain Harness" src="docs/brand/calibrain-harness-logo-light.svg" width="420">
  </picture>
</p>

<p align="center">
  <b>A forward-testing harness for trading with fast classification models: plug a model in, run it on live perpetual markets, and find out honestly whether it has an edge.</b>
</p>

<p align="center">
  <img alt="status: under development" src="https://img.shields.io/badge/status-under%20development-ff9a2e?style=flat-square">
  <img alt="venue: Bybit testnet only" src="https://img.shields.io/badge/venue-Bybit%20testnet%20only-3a3a3a?style=flat-square">
  <img alt="models: Kairos | Jev" src="https://img.shields.io/badge/models-Kairos%20%7C%20Jev-3a3a3a?style=flat-square">
  <img alt="python 3.12" src="https://img.shields.io/badge/python-3.12-3a3a3a?style=flat-square">
  <img alt="next.js 16" src="https://img.shields.io/badge/next.js-16-3a3a3a?style=flat-square">
  <a href="LICENSE"><img alt="license: MIT" src="https://img.shields.io/badge/license-MIT-3a3a3a?style=flat-square"></a>
</p>

> [!WARNING]
> **Calibrain Harness is under active development.** It trades on the **Bybit testnet only** (the host is
> hard-coded; there is no mainnet switch). Interfaces, question sets and defaults change between
> commits. Nothing here is financial advice.

<p align="center">
  <a href="docs/media/calibrain-harness-demo.mp4"><img alt="Calibrain Harness terminal demo (click for the full video)" src="docs/media/calibrain-harness-demo.gif" width="880"></a>
  <br><sub>Live terminal: monitor, Kairos signals, evaluation and news. <a href="docs/media/calibrain-harness-demo.mp4">Watch the full 1-minute video</a>.</sub>
</p>

## What it is

Calibrain Harness connects a **high-speed classification model** to perpetual futures and treats every answer
as a forecast that will be checked. On each closed bar it asks the model a small set of
**labellable questions** ("which is touched first in the next 2 h: +1.0% or −0.6%?"), turns the
probabilities into expected value in code, trades the ones that clear the gates on Bybit testnet
(long and short, with protected stops and targets), and then scores **every** prediction against
what the market actually did, traded or not.

You get two things: a trading bot, and an honest evaluation of the model behind it.

### Supported models

Calibrain Harness is built for any fast classifier that returns calibrated probabilities over a fixed set
of options. **Kairos** and **Jev** are supported today; switch between them live with **MODEL** in the
terminal's function-key bar.

| Model | Provider | Model id | Key | Status |
|---|---|---|---|---|
| **Kairos** | DMind via [Minara Cloud](https://minara.cloud) | `DMIND/Kairos` | `KAIROS_API_KEY` | Supported (also powers news_return forecasts) |
| **Jev** | [TypeSafe](https://docs.typesafe.ai) | `jev-1.14.0` (pinned; override with `CTB_JEV_MODEL`) | `TYPESAFE_API_KEY` | Supported |
| Other System One style models | | | | Add a `ModelSpec` in `ctb/models.py` |

Both are called the same way (`POST /v1/systemone` with a state and typed questions), so the question
set, validation, calibration, policy and evaluation are shared. What is kept **per model**: every
decision is stamped with the model that made it, calibration weights are fitted per model (and per
trading timeframe), EVAL and reports filter by model, and switching models starts a new run (only
while the bot is flat), so results never mix two models.

## Features

- **Bloomberg-style terminal.** Next.js front end fed by one WebSocket: candles, order book with
  depth chart and wall detection, market map, liquidation and block tape, derivatives and flow, the
  model signal with calibrated vs raw expected value, and a decision blotter with the full request
  and response for every call.
- **Multiple models.** Kairos and Jev behind one interface, switchable live, evaluated separately.
- **Labellable question set** with the instrument's own **base rates** in the state, so the model
  starts from how often each outcome really happens and is measured against that.
- **Calibration layer.** Decisions blend the model with the base rates; the weight is fitted on
  labelled outcomes, so a model that adds nothing stops driving trades.
- **Exact execution accounting on thin testnet books.** IOC-capped entries, resting take-profits,
  mainnet-confirmed managed stops with capped IOC exits, and P&L, fees and R summed from every
  Bybit closed-P&L row.
- **Evaluation built in.** Brier/RPS skill vs climatology and vs the base rates the model was shown,
  AUC, reliability diagrams, PIT, EV ranking, repeat and mirror consistency probes, execution
  quality, and per-run results (Run 1, Run 2, …, Overall).
- **Safety.** Testnet-only host, keys only in `.env`, kill switch, daily loss limit, per-trade,
  portfolio and same-direction risk caps, venue-divergence gate.

## Quick start

Requirements: Python 3.12, Node.js 20+ with npm, a Kairos or Jev API key, and Bybit **testnet** API keys.

```bash
git clone https://github.com/zadescoxp/calibrain-harness.git
cd calibrain-harness

# 1. Python service (data, model calls, execution, API)
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # add keys here, or later in the terminal (KEYS)

# 2. Terminal UI (Next.js): build once into web-next/out
cd web-next && npm ci && npm run build && cd ..

# 3. Start everything
.venv/bin/python -m ctb.server
```

Open http://127.0.0.1:8800. The Python service serves the built UI, the API and the live `/ws`
socket from that one address; there is no separate Node process to run.

- **Keys.** Kairos keys come from [minara.cloud](https://minara.cloud) and must belong to a
  Kairos-enabled group. Jev keys come from the [TypeSafe console](https://console.typesafe.ai/keys).
  Choose the model with `CTB_MODEL_PROVIDER=kairos|jev` or MODEL in the terminal. Bybit testnet keys come from
  [testnet.bybit.com → API management](https://testnet.bybit.com/app/user/api-management) with
  Contract → Orders + Positions and **no withdrawal permission**.
- **Observe-only first.** The bot starts without placing orders. Decisions are made and scored;
  switch on **AUTO-TRADE** in the command bar to trade on testnet.
- **Kill switch.** The KILL button, or `touch KILL` in the project folder: closes bot positions,
  cancels orders and halts.

### Working on the UI

| Task | Command | Notes |
|---|---|---|
| Rebuild after pulling or editing UI code | `cd web-next && npm run build` | Reload the page; no server restart needed. |
| Live-reload development | `cd web-next && npm run dev` | Open http://localhost:3000 with the Python server still running; the dev UI talks to its API and `/ws` on 127.0.0.1:8800. |
| Type-check | `cd web-next && npx tsc --noEmit` | |

If the UI has not been built yet, http://127.0.0.1:8800 shows a short page with the build command;
the API and the live socket work either way.

### Tests and smoke checks

```bash
.venv/bin/python -m pytest -q tests                       # unit tests (no network)
.venv/bin/python scripts/kairos_smoke.py SOLUSDT          # one real request to the active model + validation
.venv/bin/python scripts/kairos_smoke.py SOLUSDT --model jev   # same, against a specific model
.venv/bin/python scripts/testnet_smoke.py SOLUSDT long    # min-size testnet open -> verify stop -> close
```

## Using the terminal

| Screen | Key | What it shows |
|---|---|---|
| MON | F1 | Watchlist, chart with model markers, model signal, account and positions, relative performance, market map, order book, news, liquidations, derivatives/flow/trend, decision blotter |
| EVAL | F2 | Findings, calibration, EV ranking, PIT, per-question scores, model vs base rates, probes, operations, testnet P&L, execution quality; filter by model, run, symbol, question set, timeframe |
| VALID | F6 | Validation on independent windows: per-symbol skill with 90% intervals, skill and R by hour, session, regime and market event, walk-forward folds, edge vs cost, drift, execution quality, calibration weights |
| TRADES | F3 | Runs table (Run 1, 2, 3 … and Overall) and every trade with reference price, slippage, fills and exact R |
| NEWS | F4 | Kairos news_return forecasts (Kairos only) and headlines |
| LOG | F5 | System events |

Command line (press `/`): `BTC`, `ETH` … load a symbol · `ASK` / `PRB` ask the model now (with
consistency probes) · `1M`–`4H` chart timeframe · `TF 5M` trading timeframe · `UNIV` choose symbols
· `STRAT` strategy settings · `VALID` validation · `POS 10` max positions · `KEYS` · `HELP`.

## How it works

```
Bybit mainnet public data (sharded WS + REST)        news RSS · Fear & Greed · macro
  klines 1m–4h, tickers, funding, OI, long/short            │
  accounts, liquidations, taker trades, order book          │
        │                                                   │
        └──► features.py: compact, unit-labelled state + historical base rates for the bracket
                   │
                   ▼
   model (Kairos or Jev, /v1/systemone): 7 labellable questions per closed bar per symbol
   long bracket · short bracket · return distribution · direction · big move · mean reversion · setup quality
                   │
                   ▼
   parse.py: strict contract validation (an error is an error, never a "no")
                   │
                   ▼
   calibration.py: w · model + (1 − w) · base rates, w fitted on labelled outcomes
   policy.py: EV in R = P(target)·RR − P(stop) − cost/stop, gates, risk sizing, portfolio caps
                   │
                   ▼
   executor.py (Bybit TESTNET): IOC-capped entry → bracket anchored to the fill → resting take-profit
                → managed stop triggered on mainnet, exited with capped IOC → mark-price backstop
                → time-stop · reduce-only exits · kill switch · daily loss limit
                   │
                   ▼
   SQLite (every request, answer, label, trade, run) → labeler → metrics → terminal + report
```

## Results so far

Live forward tests on 10 Nov 2026 (5m decisions, 2 h horizon, Bybit testnet). P&L is from Bybit's
own closed-P&L records, after fees. Model quality is scored on mainnet prices for every decision.

| Run | Setup | Hours | Model decisions | Trades | Net P&L (USDT) |
|---|---|---|---|---|---|
| 1 | v1 questions · explore · market entries | 3.9 | 235 | 18 | −2,233.49 |
| 2 | v1 questions · explore · IOC entries, managed exits | 0.5 | 35 | 7 | −359.05 |
| 3 (strict) | v2 questions · calibrated strict policy · EXPLORE off | 3.75 | 322 | **0** | 0 |
| 3 (explore) | v2 questions · EXPLORE on | 2.0 | 177 | 15 | −8.48 |
| **All** | | 10.5 | 769 (3 errors) | 40 | **−2,601.01** |

What we learned:

- **Testnet liquidity dominates testnet P&L.** Run 1's −2,041 USDT on SOL came from market orders
  filling 3–11% away from the price on thin testnet books. Capped IOC entries brought entry
  slippage to ~0–4 bp; testnet-only crashes of 4–6% are now handled by mainnet-confirmed stops.
- **v1 questions lost to simple base rates;** v2, which shows the model those base rates, beats them
  on the short bracket, direction and big-move questions (Brier skill vs base rates +0.09, +0.17,
  +0.11 over 320 strict-mode decisions) and is level on the long bracket (−0.03).
- **Strict mode took no trades in 3 h 45 m.** With a 15 bp round trip costing ~0.25R on these
  stops, the base rates are never positive-EV on their own, so a strict trade needs the model to be
  clearly more confident than the base rates. That is the point of strict mode, and the reason
  exploration mode exists for collecting trade data.

Read these as an early sample: one day, one market regime, correlated symbols and overlapping
horizons.

## Design choices

| Choice | Reason |
|---|---|
| Ask *which is hit first within the horizon* instead of *buy / sell / hold* | Every answer is labellable from price data, so calibration and skill are measurable. Code, not the model, decides. |
| Show the model the instrument's base rates | v1 forecasts expected far more movement than happened. Anchoring on measured frequencies fixed most of that, and gives a fair baseline to beat. |
| 7 questions per request | Kairos allows up to 8 (Jev limits by tokens: 64k per request, 32k per question); each question carries the full state, so fewer questions means fewer tokens and lower latency. |
| Relative, unit-labelled features | "0.6% below EMA50" is informative; a raw MA level is not. The state stays well inside the per-question context budget. |
| `null` until a window has data | After a restart a 60-minute flow window holds one minute of trades; reporting it as 60 minutes would mislead the model. |
| Mainnet signals, testnet orders | Testnet flow, OI and funding are synthetic and its prices drift, so signals and labels use mainnet and orders are sized to the testnet book. |
| Label everything, trade a subset | Model skill is measured on every bar (unbiased); trading P&L measures the full system with fees and slippage. |
| Repeat and mirror probes | Measure determinism, and whether a mirrored (bullish ⇄ bearish) market flips the answer. |

## Evaluation

- **Per question:** multiclass Brier / RPS vs climatology and uniform, skill scores, log loss,
  accuracy vs majority class, AUC, reliability diagrams, ECE, PIT for the return distribution.
- **Vs base rates:** Brier of the model, of the base rates it was shown, and of the best blend.
- **Edge:** does predicted EV rank realised R (Spearman, EV buckets)? Signal results vs
  always-long / always-short.
- **Consistency:** repeat determinism, mirror symmetry, long/short coherence.
- **Operations:** errors by kind, contract violations, latency, tokens, retries.
- **Trading and execution:** net P&L after fees, win rate, profit factor, R, drawdown, exit reasons,
  entry and exit slippage, fill ratio, entry latency.
- **news_return:** RPS skill and lean vs realised 15-minute return.

Labels use mainnet 1m bars from the first bar after the decision (no look-ahead). A bar touching
both target and stop counts as a stop. EVAL → EXPORT MINARA REPORT writes a Markdown report, which
now includes the validation section below.

### Validation (VALID screen, `/api/validation`)

Consecutive decisions on 1m bars with a 30 min horizon share 29 of 30 minutes of outcome, so raw
label counts overstate the evidence by roughly the horizon length (Run 5: 1,400 labels were 49
independent windows). Every number on VALID uses **independent windows only**: per symbol, a
decision counts only if it starts after the previous counted decision's horizon has ended.

- **Per symbol:** Brier skill against the base rates for the bracket, direction, big-move and
  return-distribution questions, with a 90% bootstrap interval and a verdict (skill, no skill,
  undecided, not enough data). A symbol is *validated* only when the whole bracket interval is
  above 0; strict trades require it (`CTB_REQUIRE_VALIDATED`). Until then the harness is
  effectively shadow-logging: every bar is asked and scored, exploration trades at small size.
- **By hour (UTC), session, regime and market event:** bracket skill, realised R of the model's
  preferred side, and real trade P&L, win rate and R, so an edge that lives in one session is not
  averaged away.
- **Walk-forward:** expanding window; calibration weights are fitted on everything before a fold
  and scored on the fold, with the policy's trades and a base-rate-only control.
- **Edge vs cost:** gross edge of the model's side (bp and R) before costs, the cost charged per
  outcome, the net, and the cost at which the edge would break even.
- **Drift:** Page-Hinkley test on Brier(model) − Brier(base) in time order, plus rolling skill. On
  an alarm the model's weight for that question drops to 0 (`CTB_DRIFT_ACTION=base`), or trading
  pauses (`pause`).
- **Execution:** implementation shortfall against the mainnet decision price, entry and stop
  slippage, fees, a market-impact proxy (our size against mainnet depth within 10 bp), markouts
  1/5/15 minutes after entry, maker fill rate, wait time and the R of missed maker entries.

## Execution on testnet

- **Entries** (`CTB_ENTRY_MODE`): `maker` (default) rests a post-only limit at the best bid (long)
  or ask (short) with the backstop stop attached, and cancels whatever is unfilled after
  `CTB_MAKER_WAIT_S`; it pays the maker fee and no slippage, and a missed entry is recorded with its
  hypothetical result. `taker` sends IOC limit orders capped `CTB_MAX_ENTRY_SLIP_BP` past the best
  testnet price, sized to the depth inside the cap. The filled quantity is what is recorded.
- **Sizing** (`CTB_DYNAMIC_SIZING`): base risk (strict or exploration) × confidence (EV ÷ min edge,
  0.5–1.5, strict only) × 0.5 in a high-volatility regime × 0.5 once the run is down
  `CTB_DD_THROTTLE_PCT` of equity × 1 ÷ (1 + Σ positive correlations with open same-side positions).
  Four longs on coins that move with BTC are sized as roughly one bet. Each trade stores how its
  size was set.
- **Break-even** (`CTB_BE_TRIGGER_R`): once a trade is that many R in profit on mainnet, the managed
  stop moves to entry + `CTB_BE_OFFSET_BP`. R is still measured against the original stop.
- **Exits** (`CTB_EXIT_MODE=managed`): the target rests as a reduce-only limit (fills at it or better).
  The stop is evaluated on **mainnet's** executable price from the mainnet decision price; when it
  is crossed the position is exited on testnet with reduce-only IOC orders widening
  `CTB_EXIT_CHASE_STEP_BP` per step. A testnet-only crossing is logged as a dislocation and held.
  An exchange stop at `CTB_BACKSTOP_MULT` × the distance on mark price covers the bot being down.
- **Booking:** P&L, fees, exit price and R are summed from every Bybit closed-P&L row of the exit,
  with R measured against the stop that was really live.

## Question set v2 and calibration

- **Anchoring.** The state carries `historical_base_rates`: how often each outcome happened for the
  exact bracket on this instrument over the last ~33–40 h of completed windows.
- **Questions.** Long/short bracket, return distribution, direction, big move, mean reversion,
  setup quality.
- **Calibration.** `Σ wᵢ · sourceᵢ + (1 − Σ wᵢ) · base rates` per question group; the primary model
  starts at 0.5 and weights are refitted every 15 minutes once a group has 30 independent labelled
  windows. Long and short brackets share one weight.
- **Strict gates.** Calibrated EV ≥ `CTB_MIN_EDGE_R`, setup quality ≥ `CTB_MIN_SETUP_QUALITY`,
  calibrated direction agreeing with the side by `CTB_MIN_DIRECTION_MARGIN`, return distribution
  agreeing, plus the hard gates (existing position, venue divergence, shorts disabled).
- **Exploration** (`CTB_EXPLORATION=1`) also trades the model's preferred side when the strict gates
  block it, at reduced risk, to collect real fills on its directional lean.

## Strategy v3: brackets, costs, triggers and filters

Run 5 showed that on 1m bars the old bracket could not work: the ATR stop sat on the 40 bp floor for
most symbols, ~60–80% of windows hit neither stop nor target, and a flat 15 bp cost took 0.375R
before any trade started. If price moves at random, a bracket's expected value is exactly −cost, so
an edge has to beat that first. v3 changes what is traded and when; every setting is in the STRAT
dialog (applies while flat and starts a new run) and in `.env`.

- **Volatility brackets** (`CTB_BRACKET_MODE=vol`): stop = `CTB_SL_SIGMA` × the GARCH(1,1) forecast
  standard deviation of the return over the horizon; target = stop × `CTB_REWARD_RISK`. The stop may
  not be so tight that the expected cost exceeds `CTB_MAX_COST_R` of it; when that floor binds the
  horizon extends (`CTB_AUTO_HORIZON`, up to `CTB_MAX_HORIZON_MULT` × the profile horizon) until the
  stop is again one forecast standard deviation of the move, so the target stays reachable.
- **Cost per outcome:** a resting target pays the maker fee; a stop pays the taker fee plus stop
  slippage; a time exit pays the taker fee plus a small slip. Slippage defaults are replaced by the
  medians of the bot's own recent trades. EV, labels and the trade plan sent to the model all use it:
  `EV = P(t)·(RR − c_t/SL) − P(s)·(1 + c_s/SL) − P(n)·c_n/SL`.
- **Mirror averaging** (`CTB_SYMMETRIZE`): every decision is also asked on the mirrored market and
  the two are averaged, `p_long = (p_long(state) + p_short(mirror)) / 2`. A constant long or short
  lean cancels; anything directional in the data survives because the mirror flips the data too.
  Doubles the requests (raise `CTB_KAIROS_MAX_RPH`; 7 symbols on 1m need ~900/h).
- **Ensemble** (`CTB_ENSEMBLE=kairos`): extra models are asked on the real state and blended;
  calibration fits one weight per source on the simplex `Σw ≤ 1`, the rest going to the base rates.
- **Trade on events** (`CTB_TRADE_TRIGGER=events`): every bar is still asked and scored, but trades
  happen only on bars with a market event: volume spike (≥ 3σ), close beyond the 60-bar range, rolling
  VWAP cross, outsized bar, net-flow burst (≥ 3σ), flow/price divergence, liquidation burst,
  Hawkes-excited flow, GARCH volatility jump. Bar events are replayed over history to give
  `historical_base_rates_after_same_events`: how this exact bracket did after the same event.
- **Filters:** `CTB_REQUIRE_BIG_MOVE` trades only when the calibrated P(move ≥ 1 stop) is above its
  base rate (the one question Jev showed some skill on); `CTB_DIRECTION_SOURCE=flow|agree` takes the
  side from order flow or requires model and flow to agree; `CTB_ALLOWED_REGIMES` restricts regimes;
  liquidity gates check mainnet spread (`CTB_MAX_SPREAD_BP`), testnet spread and mainnet depth within
  10 bp against our size (`CTB_MIN_DEPTH_MULT`).
- **Calibration on independent windows**, with **one weight shared by the long and short brackets**,
  fitted once 30 independent windows exist.

New state blocks sent to the model: a 1m trend block, `volatility_forecast` (GARCH), `regime`
(volatility percentile × efficiency-ratio trend), `flow_dynamics` (Hawkes intensities per side,
imbalance, branching ratio, excitation against the fitted baseline), `order_book.best_queue`
(best-level queue imbalance and the learned P(next mid move up | imbalance), microprice),
`market_events_on_last_bar` and `session`. The mirror transform covers all of them.

### Market models (`ctb/quant.py`, `ctb/analytics.py`)

- **GARCH(1,1)** by maximum likelihood with variance targeting, refitted every 15 minutes per symbol
  and rolled forward bar by bar; horizon variance is the sum of the per-step forecasts, which revert
  to the long-run level at rate α + β.
- **Hawkes process** with an exponential kernel per aggressor side, fitted by profile likelihood
  (μ pinned by the mean rate, grid over decay β and branching ratio n) on the last 30 minutes of
  trades, every 5 minutes in a worker thread. Several fills of one aggressive order count once.
- **Queue model:** the empirical core of queue-reactive models (Cont & de Larrard; Huang, Lehalle &
  Rosenbaum): P(next mid move up | best-queue imbalance bucket), learned online from the symbol's own
  book with decaying counts.
- **Regime:** GARCH sigma's percentile against the symbol's recent rolling sigma (low / normal /
  high) × efficiency ratio of the last 60 bars (trend up / down / range).
- **Correlation** of 5m returns between symbols, for sizing.

## Symbols and runs

- **Universe.** `UNIV` lists the top 100 USDT perps by turnover with testnet listing, depth and
  spread, and flags thin books and stock/commodity perps. Up to 30 symbols; the model request budget
  is checked; the service restarts itself to apply the change.
- **Runs.** Every decision and trade is stamped with the active run. A new run starts with
  `POST /api/runs/start` once no bot trade is open, and snapshots its configuration.

## Configuration (`.env`)

| Variable | Default | Meaning |
|---|---|---|
| `CTB_SYMBOLS` | see `.env.example` | Perps to watch and trade (or use `UNIV`) |
| `CTB_DECISION_TF` / `CTB_HORIZON_BARS` / `CTB_ATR_TF` | 5 / 24 / 15 | Decide on every closed 5m bar; 2 h horizon and time-stop; stop sized from 15m ATR |
| `CTB_SL_ATR` / `CTB_TP_ATR` | 1.5 / 2.5 | Stop and target in ATR multiples |
| `CTB_MIN_SL_BP` / `CTB_MAX_SL_BP` | 40 / 300 | Stop distance clamp |
| `CTB_MIN_EDGE_R` / `CTB_MIN_SETUP_QUALITY` / `CTB_MIN_DIRECTION_MARGIN` | 0.15 / 1.5 / 0.03 | Strict entry gates |
| `CTB_EXPLORATION` / `CTB_EXPLORE_RISK_PCT` | 0 / 0.1 | Exploration mode and its risk per trade |
| `CTB_TAKER_FEE_BP` / `CTB_SLIPPAGE_BP` | 5.5 / 2 | Cost model used in EV and labels |
| `CTB_RISK_PER_TRADE_PCT` | 0.5 | % of equity lost if a stop is hit |
| `CTB_MAX_POSITIONS` / `CTB_MAX_SAME_SIDE` / `CTB_MAX_OPEN_RISK_PCT` | 3 (10 in `.env.example`) / 4 / 2 | Portfolio caps |
| `CTB_MAX_LEVERAGE` / `CTB_MAX_NOTIONAL_USDT` / `CTB_DAILY_LOSS_LIMIT_PCT` | 3 / 5000 / 3 | Hard caps |
| `CTB_MAX_ENTRY_SLIP_BP` / `CTB_EXIT_MODE` | 10 / managed | Entry price cap and exit handling |
| `CTB_MODEL_PROVIDER` | kairos | Which model decides: `kairos` or `jev` (or MODEL in the terminal) |
| `KAIROS_API_KEY` / `KAIROS_BASE_URL` | – / https://minara.cloud | Kairos credentials |
| `TYPESAFE_API_KEY` / `CTB_JEV_MODEL` | – / jev-1.14.0 | Jev credentials and pinned model version |
| `CTB_KAIROS_MAX_RPH` / `CTB_KAIROS_CONCURRENCY` | 120 / 6 | Model request budget and parallelism (any model) |
| `CTB_BRACKET_MODE` / `CTB_SL_SIGMA` / `CTB_REWARD_RISK` | vol / 1.0 / 1.5 | Volatility bracket |
| `CTB_MAX_COST_R` / `CTB_AUTO_HORIZON` / `CTB_MAX_HORIZON_MULT` / `CTB_MIN_SL_BP_VOL` | 0.2 / 1 / 6 / 10 | Cost floor and horizon extension |
| `CTB_ENTRY_MODE` / `CTB_MAKER_WAIT_S` / `CTB_MAKER_FEE_BP` | maker / 20 / 2 | Entries |
| `CTB_STOP_SLIP_BP` / `CTB_TIME_EXIT_SLIP_BP` | 5 / 2 | Cost defaults until slippage is measured |
| `CTB_SYMMETRIZE` / `CTB_ENSEMBLE` / `CTB_ENSEMBLE_MAX_RPH` | 1 / – / 120 | Mirror averaging, extra models |
| `CTB_TRADE_TRIGGER` / `CTB_DIRECTION_SOURCE` / `CTB_MIN_FLOW_SCORE` | events / model / 0.25 | When and which side to trade |
| `CTB_REQUIRE_BIG_MOVE` / `CTB_BIG_MOVE_MARGIN` / `CTB_ALLOWED_REGIMES` | 1 / 0.03 / – | Filters |
| `CTB_MAX_SPREAD_BP` / `CTB_MAX_TESTNET_SPREAD_BP` / `CTB_MIN_DEPTH_MULT` | 5 / 25 / 3 | Liquidity gates |
| `CTB_REQUIRE_VALIDATED` / `CTB_DRIFT_ACTION` | 1 / base | Validation gate for strict trades; action on drift |
| `CTB_DYNAMIC_SIZING` / `CTB_DD_THROTTLE_PCT` / `CTB_BE_TRIGGER_R` / `CTB_BE_OFFSET_BP` | 1 / 1.0 / 1.0 / 5 | Sizing and break-even |

## Safety

- The Bybit host is hard-coded to `api-testnet.bybit.com`. There is no mainnet switch.
- Keys are read from `.env` only (owner-only permissions, gitignored). They are never logged,
  stored in the database or sent to the browser, which only sees the last four characters.
- The server binds to 127.0.0.1 and refuses cross-origin writes.
- Positions opened by hand on testnet are shown and never touched by the bot (except the kill switch).

## Project layout

```
ctb/                      Python service (internal package name from the project's first version)
  config.py               settings from .env
  market/                 Bybit mainnet/testnet streams, store, news/macro context
  features.py             state builder, brackets, base rates (also after events), mirror transform
  quant.py                GARCH(1,1), Hawkes fits, queue model, regime, correlation (pure Python)
  analytics.py            per-symbol models kept warm, market-event detection
  costs.py                execution cost per outcome, measured slippage, EV
  ensemble.py             mirror symmetrization, multi-source blending
  models.py               supported models (Kairos, Jev): endpoint, model id, key, limits, retries
  kairos/                 System One client, versioned question sets, strict parsing, news_return
  calibration.py          source weights on independent windows, shared bracket weight
  policy.py               EV, hard gates, filters, quality gates, dynamic sizing
  broker/                 signed testnet client, private stream, executor
  evaluation/             labeler, metrics, validation (per symbol, splits, walk-forward, drift), report
  runs.py · universe.py   test runs, symbol validation
  engine.py · server.py   orchestration, API, /ws, serves the UI
web-next/                 terminal UI (Next.js 16, static export to web-next/out)
scripts/                  smoke tests, backfills, run definitions
docs/                     brand assets and demo media
```

## Roadmap

- More models next to Kairos and Jev, and side-by-side runs of two models on the same bars
- Order-flow screener integration (aggregated multi-venue flow, absorption, icebergs, liquidity
  walls, volume-profile levels) once the current strategy is validated
- Limit-based exchange backstops so a testnet liquidity hole cannot fill a disaster stop
- Offline replay of stored states through new question sets
- Multi-day, multi-regime evaluation runs and published reports

## Contributing

Issues and pull requests are welcome while the project is under development. Please run
`.venv/bin/python -m pytest -q tests` and `cd web-next && npx tsc --noEmit` before opening a PR, and
never commit `.env` or anything under `data/`.

## Disclaimer

Calibrain Harness is research software for testnet forward-testing. It is not investment advice, it has not
been audited, and past testnet results say nothing reliable about future returns.

## License

[MIT](LICENSE). Use, modify and redistribute freely; the software comes with no warranty.

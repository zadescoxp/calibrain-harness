"""Runtime configuration. Everything is read from the environment (optionally a local .env).

Secrets (KAIROS_API_KEY, TYPESAFE_API_KEY, BYBIT_API_KEY, BYBIT_API_SECRET) are only ever read here and passed
to the clients that need them. They are never logged, stored in SQLite, or sent to the browser.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # source tree (or the bundled app's resources)
# Where mutable state lives: .env (keys + settings), data/ctb.db, KILL. Defaults to the source tree so
# `python -m ctb.server` works as before; the desktop app points CTB_HOME at a per-user app-data dir.
HOME = Path(os.environ["CTB_HOME"]).expanduser() if os.environ.get("CTB_HOME") else ROOT
ENV_PATH = HOME / ".env"


def _init_home() -> None:
    """App-data dir: owner-only, seeded on first run so nobody has to edit .env by hand."""
    if HOME == ROOT:
        return
    HOME.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(HOME, 0o700)
    except OSError:
        pass
    if not ENV_PATH.exists():
        ENV_PATH.write_text("# Calibrain Harness settings and keys. Edited from the app (KEYS / CONFIG).\n"
                            "CTB_VENUE=demo\n", encoding="utf-8")
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


_init_home()


def _load_restart_env() -> None:
    """Env overrides a supervised restart left behind (ctb/server.py). Must run before the Settings
    defaults below are evaluated, which happens when this module is imported."""
    f = HOME / ".restart-env.json"
    if os.environ.get("CTB_SUPERVISED") != "1" or not f.exists():
        return
    import json
    try:
        os.environ.update({k: str(v) for k, v in json.loads(f.read_text(encoding="utf-8")).items() if v})
    except (OSError, ValueError):
        pass
    finally:
        f.unlink(missing_ok=True)


_load_restart_env()


def load_dotenv(path: Path | None = None) -> None:
    """CTB_ENV_FILE points at another env file (the tests use it to ignore the local .env)."""
    path = path or Path(os.environ.get("CTB_ENV_FILE") or ENV_PATH)
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        key = key.strip()
        if sep and key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


def save_env(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    """Upsert KEY=value lines in .env (keeps comments/other keys), owner-only permissions."""
    for k, v in updates.items():
        if not k.replace("_", "").isalnum() or any(c in v for c in "\r\n"):
            raise ValueError(f"invalid value for {k}")
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    done: set[str] = set()
    out = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in updates and not line.lstrip().startswith("#"):
            if key not in done:
                out.append(f"{key}={updates[key]}")
                done.add(key)
            continue
        out.append(line)
    out += [f"{k}={v}" for k, v in updates.items() if k not in done]
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.environ.update(updates)


def key_hint(value: str) -> dict:
    """What the browser may see about a secret: whether it is set and its last 4 chars."""
    return {"set": bool(value), "hint": f"…{value[-4:]}" if len(value) >= 8 else ("set" if value else "")}


# Trading-timeframe profiles: horizon and stop sizing move together with the decision cadence,
# so switching timeframe never silently changes the strategy's holding period or risk geometry.
TF_PROFILES: dict[str, dict] = {
    "1": {"label": "1M", "horizon_bars": 30, "atr_tf": "5"},     # 30 min hold, 5m ATR stop
    "5": {"label": "5M", "horizon_bars": 24, "atr_tf": "15"},    # 2 h hold, 15m ATR stop
    "15": {"label": "15M", "horizon_bars": 16, "atr_tf": "60"},  # 4 h hold, 1h ATR stop
    "60": {"label": "1H", "horizon_bars": 12, "atr_tf": "240"},  # 12 h hold, 4h ATR stop
}


# Field defaults below read os.environ at import time, so .env must be loaded first.
load_dotenv()


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(float(os.environ.get(name, default)))
    except ValueError:
        return default


def _b(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _list(name: str, default: str) -> list[str]:
    return [s.strip().upper() for s in os.environ.get(name, default).split(",") if s.strip()]


@dataclass
class Settings:
    # --- markets ---
    symbols: list[str] = field(default_factory=lambda: _list("CTB_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT"))
    decision_tf: str = os.environ.get("CTB_DECISION_TF", "5")  # Bybit interval string, minutes
    horizon_bars: int = _i("CTB_HORIZON_BARS", 24)  # bracket/time-stop horizon, in decision bars
    atr_tf: str = os.environ.get("CTB_ATR_TF", "15")  # timeframe whose ATR sizes the bracket

    # --- kairos ---
    # which classification model makes the decisions: "kairos" or "jev" (see ctb/models.py)
    model_provider: str = os.environ.get("CTB_MODEL_PROVIDER", "kairos").strip().lower()
    kairos_base_url: str = os.environ.get("KAIROS_BASE_URL", "https://minara.cloud").rstrip("/")
    kairos_model: str = "DMIND/Kairos"
    kairos_timeout_s: float = _f("CTB_KAIROS_TIMEOUT_S", 120)
    kairos_max_requests_per_hour: int = _i("CTB_KAIROS_MAX_RPH", 120)
    kairos_concurrency: int = _i("CTB_KAIROS_CONCURRENCY", 6)  # simultaneous requests at a bar close
    kairos_news_enabled: bool = _b("CTB_KAIROS_NEWS", True)
    kairos_news_max_per_hour: int = _i("CTB_KAIROS_NEWS_MAX_RPH", 20)

    # --- bracket geometry (multiples of decision-timeframe ATR) ---
    sl_atr: float = _f("CTB_SL_ATR", 1.5)
    tp_atr: float = _f("CTB_TP_ATR", 2.5)
    min_sl_bp: float = _f("CTB_MIN_SL_BP", 40)  # floor so stops are never inside noise/costs
    max_sl_bp: float = _f("CTB_MAX_SL_BP", 300)

    # --- policy ---
    min_edge_r: float = _f("CTB_MIN_EDGE_R", 0.15)  # required EV in R units after costs
    min_setup_quality: float = _f("CTB_MIN_SETUP_QUALITY", 1.5)  # score scale 0..4
    min_direction_margin: float = _f("CTB_MIN_DIRECTION_MARGIN", 0.03)  # long needs calibrated P(up) >= 0.5 + margin
    taker_fee_bp: float = _f("CTB_TAKER_FEE_BP", 5.5)
    slippage_bp: float = _f("CTB_SLIPPAGE_BP", 2.0)
    allow_short: bool = _b("CTB_ALLOW_SHORT", True)
    # entries are IOC limits at most this far past the best testnet price; unfilled size is dropped
    max_entry_slip_bp: float = _f("CTB_MAX_ENTRY_SLIP_BP", 10)
    # managed exits: target = resting reduce-only limit at the target price (fills at it or better);
    # stop = watched on the testnet bid/ask and exited with capped IOC orders that widen step by step,
    # so a thin book cannot turn a 1R stop into a 7R loss; exchange stop kept at backstop_mult x the
    # distance in case the bot is down. "exchange" = plain Full-mode market TP/SL on Bybit.
    exit_mode: str = os.environ.get("CTB_EXIT_MODE", "managed")
    backstop_mult: float = _f("CTB_BACKSTOP_MULT", 2.0)
    exit_chase_step_bp: float = _f("CTB_EXIT_CHASE_STEP_BP", 10)
    exit_chase_max_bp: float = _f("CTB_EXIT_CHASE_MAX_BP", 150)
    tpsl_trigger: str = os.environ.get("CTB_TPSL_TRIGGER", "LastPrice")  # LastPrice | MarkPrice | IndexPrice
    bar_settle_s: float = _f("CTB_BAR_SETTLE_S", 0.3)  # wait after a bar closes before building state
    trading_enabled: bool = _b("CTB_TRADING_ENABLED", False)  # start in observe-only
    # exploration: trade Kairos's preferred side even when the strict quality gates block it
    exploration_mode: bool = _b("CTB_EXPLORATION", False)
    explore_min_lean_r: float = _f("CTB_EXPLORE_MIN_LEAN_R", 0.05)  # |EV long - EV short| needed
    explore_risk_pct: float = _f("CTB_EXPLORE_RISK_PCT", 0.1)  # % equity risked per exploration trade

    # --- strategy v3: brackets, costs, triggers, filters (see README "Strategy settings") ---
    # vol: stop = sl_sigma x GARCH sigma over the horizon, never so tight that costs exceed max_cost_r of it;
    #      when that floor binds, the horizon extends (auto_horizon) until the stop is a fair move again.
    # atr: the original 1.5 x ATR stop with a min_sl_bp floor and a fixed horizon.
    bracket_mode: str = os.environ.get("CTB_BRACKET_MODE", "vol").strip().lower()
    sl_sigma: float = _f("CTB_SL_SIGMA", 1.0)
    reward_risk: float = _f("CTB_REWARD_RISK", 1.5)
    max_cost_r: float = _f("CTB_MAX_COST_R", 0.2)  # expected cost may be at most this share of the stop
    min_sl_bp_vol: float = _f("CTB_MIN_SL_BP_VOL", 10)
    auto_horizon: bool = _b("CTB_AUTO_HORIZON", True)
    max_horizon_mult: float = _f("CTB_MAX_HORIZON_MULT", 6)  # longest horizon = this x the profile horizon
    # entries: maker = post-only limit at the best bid/ask, cancelled after maker_wait_s; taker = capped IOC
    entry_mode: str = os.environ.get("CTB_ENTRY_MODE", "maker").strip().lower()
    maker_wait_s: float = _f("CTB_MAKER_WAIT_S", 20)
    maker_fee_bp: float = _f("CTB_MAKER_FEE_BP", 2.0)
    stop_slip_bp: float = _f("CTB_STOP_SLIP_BP", 5.0)  # default until enough stops are measured
    time_exit_slip_bp: float = _f("CTB_TIME_EXIT_SLIP_BP", 2.0)
    # ask every decision on the real and the mirrored market and average: cancels any built-in long/short bias
    symmetrize: bool = _b("CTB_SYMMETRIZE", True)
    ensemble: list[str] = field(default_factory=lambda: [x.lower() for x in _list("CTB_ENSEMBLE", "")])  # extra models
    ensemble_max_rph: int = _i("CTB_ENSEMBLE_MAX_RPH", 120)  # request budget per extra model
    # trade only on bars with a market event (decisions are still made and scored on every bar)
    trade_trigger: str = os.environ.get("CTB_TRADE_TRIGGER", "events").strip().lower()  # events | every_bar
    direction_source: str = os.environ.get("CTB_DIRECTION_SOURCE", "model").strip().lower()  # model | flow | agree
    min_flow_score: float = _f("CTB_MIN_FLOW_SCORE", 0.25)
    require_big_move: bool = _b("CTB_REQUIRE_BIG_MOVE", True)  # calibrated P(move >= 1 stop) above its base rate
    big_move_margin: float = _f("CTB_BIG_MOVE_MARGIN", 0.03)
    allowed_regimes: list[str] = field(default_factory=lambda: [x.lower() for x in _list("CTB_ALLOWED_REGIMES", "")])
    max_spread_bp: float = _f("CTB_MAX_SPREAD_BP", 5)  # mainnet
    max_testnet_spread_bp: float = _f("CTB_MAX_TESTNET_SPREAD_BP", 25)
    min_depth_mult: float = _f("CTB_MIN_DEPTH_MULT", 3)  # mainnet depth within 10 bp >= this x our notional
    require_validated: bool = _b("CTB_REQUIRE_VALIDATED", True)  # strict trades need proven out-of-sample skill
    drift_action: str = os.environ.get("CTB_DRIFT_ACTION", "base").strip().lower()  # base | pause | alert
    dynamic_sizing: bool = _b("CTB_DYNAMIC_SIZING", True)
    dd_throttle_pct: float = _f("CTB_DD_THROTTLE_PCT", 1.0)  # run drawdown (% equity) that halves risk
    be_trigger_r: float = _f("CTB_BE_TRIGGER_R", 1.0)  # move the stop to break-even after this many R (0 = off)
    be_offset_bp: float = _f("CTB_BE_OFFSET_BP", 5)

    # --- risk ---
    risk_per_trade_pct: float = _f("CTB_RISK_PER_TRADE_PCT", 0.5)
    max_leverage: int = _i("CTB_MAX_LEVERAGE", 3)
    max_positions: int = _i("CTB_MAX_POSITIONS", 3)
    # portfolio caps: more symbols means more correlated positions (alts move with BTC)
    max_open_risk_pct: float = _f("CTB_MAX_OPEN_RISK_PCT", 2.0)  # sum of open stop-risk, % of equity
    max_same_side: int = _i("CTB_MAX_SAME_SIDE", 4)  # open positions in one direction
    max_notional_usdt: float = _f("CTB_MAX_NOTIONAL_USDT", 5000)
    daily_loss_limit_pct: float = _f("CTB_DAILY_LOSS_LIMIT_PCT", 3.0)
    max_venue_divergence_bp: float = _f("CTB_MAX_VENUE_DIVERGENCE_BP", 150)

    # --- bybit: paper-money venues only, enforced in code (ctb/broker/venue.py) ---
    # testnet: api-testnet.bybit.com, its own thin book | demo: Bybit Demo Trading on the mainnet market
    venue: str = os.environ.get("CTB_VENUE", "testnet").strip().lower()
    # demo: book every fill at the worse of Bybit's report and the real mainnet book walk / fee schedule
    cost_realism: bool = _b("CTB_COST_REALISM", True)
    # keys are kept per venue (BYBIT_DEMO_API_KEY, BYBIT_TESTNET_API_KEY, ...) so switching venue in the
    # UI never asks for them again; the old BYBIT_API_KEY / BYBIT_API_SECRET pair is the testnet pair
    bybit_keys: dict = field(default_factory=lambda: _load_bybit_keys())

    # --- server ---
    host: str = os.environ.get("CTB_HOST", "127.0.0.1")
    port: int = _i("CTB_PORT", 8800)
    db_path: Path = Path(os.environ.get("CTB_DB_PATH", str(HOME / "data" / "ctb.db")))
    kill_file: Path = HOME / "KILL"

    @property
    def kairos_api_key(self) -> str:
        # Exact variable name only, per the Kairos contract. Never substitute another key.
        return os.environ.get("KAIROS_API_KEY", "").strip()

    @property
    def bybit_api_key(self) -> str:
        return self.bybit_keys.get(self.venue, ("", ""))[0]

    @property
    def bybit_api_secret(self) -> str:
        return self.bybit_keys.get(self.venue, ("", ""))[1]

    @property
    def venue_spec(self):
        from .broker.venue import get
        return get(self.venue)

    @property
    def shadow_costs(self) -> bool:
        return self.cost_realism and self.venue_spec.shadow_costs

    @property
    def model_spec(self):
        from .models import get
        return get(self.model_provider)

    @property
    def round_trip_cost_bp(self) -> float:
        return 2 * (self.taker_fee_bp + self.slippage_bp)

    @property
    def decision_tf_minutes(self) -> int:
        return {"D": 1440, "W": 10080}.get(self.decision_tf, None) or int(self.decision_tf)

    def required_rph(self, tf: str | None = None, probe_rate: float = 0.05) -> int:
        """Kairos requests/hour a trading timeframe needs: one per closed bar per symbol, plus probes."""
        minutes = int(tf or self.decision_tf)
        per_decision = 2 if self.symmetrize else 1  # real + mirrored market
        return int(round(len(self.symbols) * (60 / minutes) * (per_decision + 3 * probe_rate)))

    def apply_tf_profile(self, tf: str) -> dict[str, str]:
        p = TF_PROFILES[tf]
        self.decision_tf, self.horizon_bars, self.atr_tf = tf, p["horizon_bars"], p["atr_tf"]
        return {"CTB_DECISION_TF": tf, "CTB_HORIZON_BARS": str(p["horizon_bars"]), "CTB_ATR_TF": p["atr_tf"]}

    def public_view(self) -> dict:
        """Settings safe to show in the dashboard (no secrets)."""
        return {
            "symbols": self.symbols,
            "decision_tf": self.decision_tf,
            "tf_profiles": {tf: {**p, "horizon_minutes": p["horizon_bars"] * int(tf), "required_rph": self.required_rph(tf)}
                            for tf, p in TF_PROFILES.items()},
            "decision_tf_minutes": self.decision_tf_minutes,
            "horizon_bars": self.horizon_bars,
            "atr_tf": self.atr_tf,
            "horizon_minutes": self.horizon_bars * self.decision_tf_minutes,
            "sl_atr": self.sl_atr,
            "tp_atr": self.tp_atr,
            "min_sl_bp": self.min_sl_bp,
            "max_sl_bp": self.max_sl_bp,
            "min_edge_r": self.min_edge_r,
            "min_setup_quality": self.min_setup_quality,
            "round_trip_cost_bp": self.round_trip_cost_bp,
            "max_entry_slip_bp": self.max_entry_slip_bp,
            "tpsl_trigger": self.tpsl_trigger,
            "exit_mode": self.exit_mode,
            "backstop_mult": self.backstop_mult,
            "exit_chase_step_bp": self.exit_chase_step_bp,
            "exit_chase_max_bp": self.exit_chase_max_bp,
            "allow_short": self.allow_short,
            "trading_enabled": self.trading_enabled,
            "exploration_mode": self.exploration_mode,
            "explore_min_lean_r": self.explore_min_lean_r,
            "explore_risk_pct": self.explore_risk_pct,
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_leverage": self.max_leverage,
            "max_positions": self.max_positions,
            "max_open_risk_pct": self.max_open_risk_pct,
            "max_same_side": self.max_same_side,
            "min_direction_margin": self.min_direction_margin,
            "max_notional_usdt": self.max_notional_usdt,
            "daily_loss_limit_pct": self.daily_loss_limit_pct,
            "model_provider": self.model_provider,
            "model_label": self.model_spec.label,
            "model_id": self.model_spec.model,
            "models": {p: {"label": m.label, "vendor": m.vendor, "model_id": m.model, "configured": bool(m.api_key),
                           "news_return": m.supports_news_return} for p, m in _models().items()},
            "kairos_base_url": self.kairos_base_url,
            "kairos_model": self.kairos_model,
            "kairos_key_configured": bool(self.kairos_api_key),
            "kairos_max_requests_per_hour": self.kairos_max_requests_per_hour,
            "bybit_keys_configured": bool(self.bybit_api_key and self.bybit_api_secret),
            "venue": f"bybit-{self.venue}",
            "venue_name": self.venue,
            "venue_label": self.venue_spec.label,
            "venue_keys_url": self.venue_spec.keys_url,
            "venue_is_mainnet_book": self.venue_spec.public_rest == "https://api.bybit.com",
            "cost_realism": self.shadow_costs,
            "taker_fee_bp": self.taker_fee_bp,
            "maker_fee_bp": self.maker_fee_bp,
            "strategy": self.strategy_view(),
        }

    STRATEGY_ENV = {
        "bracket_mode": "CTB_BRACKET_MODE", "sl_sigma": "CTB_SL_SIGMA", "reward_risk": "CTB_REWARD_RISK",
        "max_cost_r": "CTB_MAX_COST_R", "auto_horizon": "CTB_AUTO_HORIZON", "entry_mode": "CTB_ENTRY_MODE",
        "maker_wait_s": "CTB_MAKER_WAIT_S", "symmetrize": "CTB_SYMMETRIZE", "trade_trigger": "CTB_TRADE_TRIGGER",
        "direction_source": "CTB_DIRECTION_SOURCE", "require_big_move": "CTB_REQUIRE_BIG_MOVE",
        "allowed_regimes": "CTB_ALLOWED_REGIMES", "require_validated": "CTB_REQUIRE_VALIDATED",
        "drift_action": "CTB_DRIFT_ACTION", "dynamic_sizing": "CTB_DYNAMIC_SIZING", "be_trigger_r": "CTB_BE_TRIGGER_R",
        "ensemble": "CTB_ENSEMBLE", "max_spread_bp": "CTB_MAX_SPREAD_BP", "min_depth_mult": "CTB_MIN_DEPTH_MULT",
    }
    STRATEGY_CHOICES = {
        "bracket_mode": ("vol", "atr"), "entry_mode": ("maker", "taker"), "trade_trigger": ("events", "every_bar"),
        "direction_source": ("model", "flow", "agree"), "drift_action": ("base", "pause", "alert"),
    }

    def strategy_view(self) -> dict:
        return {k: getattr(self, k) for k in self.STRATEGY_ENV}

    def set_strategy(self, updates: dict) -> dict[str, str]:
        """Validate and apply strategy settings; returns the env lines to persist."""
        env: dict[str, str] = {}
        for k, v in updates.items():
            if k not in self.STRATEGY_ENV:
                raise ValueError(f"unknown strategy setting {k}")
            cur = getattr(self, k)
            if k in self.STRATEGY_CHOICES:
                v = str(v).strip().lower()
                if v not in self.STRATEGY_CHOICES[k]:
                    raise ValueError(f"{k} must be one of {', '.join(self.STRATEGY_CHOICES[k])}")
            elif isinstance(cur, bool):
                v = v if isinstance(v, bool) else str(v).strip().lower() in {"1", "true", "yes", "on"}
            elif isinstance(cur, list):
                v = [x.strip().lower() for x in (v if isinstance(v, list) else str(v).split(",")) if x.strip()]
            elif isinstance(cur, (int, float)):
                v = float(v)
                if v < 0:
                    raise ValueError(f"{k} must be >= 0")
            setattr(self, k, v)
            env[self.STRATEGY_ENV[k]] = ",".join(v) if isinstance(v, list) else ("1" if v is True else "0" if v is False else str(v))
        return env


# ---------------------------------------------------------------- settings editable from the UI
# key -> (env var, kind, min, max, group, label, help[, choices]). Strategy v3 settings have their own
# dialog (STRATEGY_ENV). "restart" settings are saved and the service restarts itself to apply them.
_G_VENUE, _G_RISK, _G_GATES, _G_EXEC, _G_BRK, _G_MODEL = ("Venue & costs", "Risk", "Signal gates", "Execution",
                                                         "Brackets", "Model budget")
EDITABLE: dict[str, tuple] = {
    "venue": ("CTB_VENUE", "choice", None, None, _G_VENUE, "Order venue",
              "demo = Bybit Demo Trading on the mainnet market · testnet = Bybit testnet book. Needs keys for that venue; "
              "restarts the service, starts a new run and switches auto-trade off", ("demo", "testnet")),
    "cost_realism": ("CTB_COST_REALISM", "bool", None, None, _G_VENUE, "Cost realism (demo)",
                     "Book demo trades at the worse of Bybit's fill/fees and the mainnet book walk, fee schedule and funding"),
    "taker_fee_bp": ("CTB_TAKER_FEE_BP", "float", 0, 50, _G_VENUE, "Taker fee bp", "Fee schedule for taker legs (Bybit VIP0 linear: 5.5)"),
    "maker_fee_bp": ("CTB_MAKER_FEE_BP", "float", -5, 50, _G_VENUE, "Maker fee bp", "Fee schedule for maker legs (Bybit VIP0 linear: 2.0)"),
    "slippage_bp": ("CTB_SLIPPAGE_BP", "float", 0, 100, _G_VENUE, "Taker entry slip bp", "Default until enough taker entries are measured"),
    "stop_slip_bp": ("CTB_STOP_SLIP_BP", "float", 0, 200, _G_VENUE, "Stop slip bp",
                     "Default stop slippage until measured; also the demo floor for exchange-triggered stops"),
    "time_exit_slip_bp": ("CTB_TIME_EXIT_SLIP_BP", "float", 0, 100, _G_VENUE, "Time-exit slip bp", "Expected slippage of a horizon close"),

    "risk_per_trade_pct": ("CTB_RISK_PER_TRADE_PCT", "float", 0.01, 5, _G_RISK, "Risk / trade %", "Equity lost if a strict trade's stop is hit"),
    "explore_risk_pct": ("CTB_EXPLORE_RISK_PCT", "float", 0, 5, _G_RISK, "Explore risk / trade %", "Same for exploration trades"),
    "max_leverage": ("CTB_MAX_LEVERAGE", "int", 1, 20, _G_RISK, "Max leverage", "Set on each symbol before its next entry"),
    "max_positions": ("CTB_MAX_POSITIONS", "int", 1, 50, _G_RISK, "Max positions", "Open positions across all symbols"),
    "max_same_side": ("CTB_MAX_SAME_SIDE", "int", 1, 50, _G_RISK, "Max same side", "Open positions in one direction"),
    "max_open_risk_pct": ("CTB_MAX_OPEN_RISK_PCT", "float", 0.1, 20, _G_RISK, "Max open risk %", "Sum of open stop-risk, % of equity"),
    "max_notional_usdt": ("CTB_MAX_NOTIONAL_USDT", "float", 10, 1_000_000, _G_RISK, "Max notional USDT", "Per position"),
    "daily_loss_limit_pct": ("CTB_DAILY_LOSS_LIMIT_PCT", "float", 0.1, 50, _G_RISK, "Daily loss limit %", "Trading halts for the UTC day"),
    "dd_throttle_pct": ("CTB_DD_THROTTLE_PCT", "float", 0, 50, _G_RISK, "Drawdown throttle %", "Run drawdown that halves risk (dynamic sizing)"),

    "min_edge_r": ("CTB_MIN_EDGE_R", "float", 0, 2, _G_GATES, "Min edge R", "Required EV after costs, in R"),
    "min_setup_quality": ("CTB_MIN_SETUP_QUALITY", "float", 0, 4, _G_GATES, "Min setup quality", "Model's setup score, 0–4"),
    "min_direction_margin": ("CTB_MIN_DIRECTION_MARGIN", "float", 0, 0.5, _G_GATES, "Min direction margin",
                             "Long needs calibrated P(up) ≥ 0.5 + this"),
    "allow_short": ("CTB_ALLOW_SHORT", "bool", None, None, _G_GATES, "Allow shorts", ""),
    "explore_min_lean_r": ("CTB_EXPLORE_MIN_LEAN_R", "float", 0, 1, _G_GATES, "Explore min lean R", "|EV long − EV short| an exploration trade needs"),
    "max_testnet_spread_bp": ("CTB_MAX_TESTNET_SPREAD_BP", "float", 0, 500, _G_GATES, "Max venue spread bp", "Spread of the book orders go to"),
    "max_venue_divergence_bp": ("CTB_MAX_VENUE_DIVERGENCE_BP", "float", 0, 2000, _G_GATES, "Max venue divergence bp",
                                "Venue price vs mainnet (≈0 on demo)"),

    "max_entry_slip_bp": ("CTB_MAX_ENTRY_SLIP_BP", "float", 0, 200, _G_EXEC, "Entry IOC cap bp", "Taker entries take liquidity only this far past the best price"),
    "exit_mode": ("CTB_EXIT_MODE", "choice", None, None, _G_EXEC, "Exit mode",
                  "managed = resting target + watched stop with capped IOC exits · exchange = plain Bybit TP/SL (new trades)",
                  ("managed", "exchange")),
    "backstop_mult": ("CTB_BACKSTOP_MULT", "float", 1, 10, _G_EXEC, "Backstop × stop", "Exchange stop distance while the managed stop is watched"),
    "exit_chase_step_bp": ("CTB_EXIT_CHASE_STEP_BP", "float", 1, 100, _G_EXEC, "Exit chase step bp", "IOC exit widens by this per step"),
    "exit_chase_max_bp": ("CTB_EXIT_CHASE_MAX_BP", "float", 10, 1000, _G_EXEC, "Exit chase max bp", "Then a market order"),
    "tpsl_trigger": ("CTB_TPSL_TRIGGER", "choice", None, None, _G_EXEC, "TP/SL trigger", "Exchange mode trigger price",
                     ("LastPrice", "MarkPrice", "IndexPrice")),
    "be_offset_bp": ("CTB_BE_OFFSET_BP", "float", 0, 50, _G_EXEC, "Break-even offset bp", "Stop goes this far past entry at break-even"),

    "sl_atr": ("CTB_SL_ATR", "float", 0.1, 10, _G_BRK, "Stop × ATR (atr mode)", ""),
    "tp_atr": ("CTB_TP_ATR", "float", 0.1, 20, _G_BRK, "Target × ATR (atr mode)", ""),
    "min_sl_bp": ("CTB_MIN_SL_BP", "float", 1, 1000, _G_BRK, "Min stop bp (atr mode)", ""),
    "max_sl_bp": ("CTB_MAX_SL_BP", "float", 1, 5000, _G_BRK, "Max stop bp", ""),
    "min_sl_bp_vol": ("CTB_MIN_SL_BP_VOL", "float", 1, 1000, _G_BRK, "Min stop bp (vol mode)", ""),
    "max_horizon_mult": ("CTB_MAX_HORIZON_MULT", "float", 1, 20, _G_BRK, "Max horizon ×", "Longest auto-extended horizon, × the profile horizon"),

    "kairos_max_requests_per_hour": ("CTB_KAIROS_MAX_RPH", "int", 1, 10000, _G_MODEL, "Model requests / hour", "Hard hourly cap"),
    "kairos_concurrency": ("CTB_KAIROS_CONCURRENCY", "int", 1, 32, _G_MODEL, "Concurrent requests", "At a bar close"),
    "kairos_timeout_s": ("CTB_KAIROS_TIMEOUT_S", "float", 5, 600, _G_MODEL, "Request timeout s", ""),
    "kairos_news_enabled": ("CTB_KAIROS_NEWS", "bool", None, None, _G_MODEL, "News questions", "Ask the model about news (Kairos)"),
    "kairos_news_max_per_hour": ("CTB_KAIROS_NEWS_MAX_RPH", "int", 0, 1000, _G_MODEL, "News requests / hour", ""),
}
RESTART_SETTINGS = {"venue"}


def config_view(s: "Settings") -> dict:
    return {"fields": [{"k": k, "env": e[0], "kind": e[1], "min": e[2], "max": e[3], "group": e[4], "label": e[5], "help": e[6],
                        "choices": list(e[7]) if len(e) > 7 else None, "restart": k in RESTART_SETTINGS} for k, e in EDITABLE.items()],
            "values": {k: getattr(s, k) for k in EDITABLE}}


def parse_config(s: "Settings", updates: dict) -> dict:
    """Validate UI edits -> {key: typed value}, only for values that differ. Raises ValueError."""
    out = {}
    for k, v in updates.items():
        if k not in EDITABLE:
            raise ValueError(f"{k} is not an editable setting")
        env, kind, lo, hi, _g, label, *_rest = EDITABLE[k]
        if kind == "bool":
            v = v if isinstance(v, bool) else str(v).strip().lower() in {"1", "true", "yes", "on"}
        elif kind == "choice":
            choices = EDITABLE[k][7]
            v = next((c for c in choices if c.lower() == str(v).strip().lower()), None)
            if v is None:
                raise ValueError(f"{label} must be one of {', '.join(choices)}")
        else:
            try:
                v = float(v)
            except (TypeError, ValueError):
                raise ValueError(f"{label} must be a number") from None
            if kind == "int":
                if v != int(v):
                    raise ValueError(f"{label} must be a whole number")
                v = int(v)
            if not lo <= v <= hi:
                raise ValueError(f"{label} must be between {lo:g} and {hi:g}")
        if v != getattr(s, k):
            out[k] = v
    return out


def config_env(values: dict) -> dict[str, str]:
    return {EDITABLE[k][0]: ("1" if v is True else "0" if v is False else str(v)) for k, v in values.items()}


def bybit_env_names(venue: str) -> tuple[str, str]:
    v = venue.upper()
    return f"BYBIT_{v}_API_KEY", f"BYBIT_{v}_API_SECRET"


def _load_bybit_keys() -> dict:
    from .broker.venue import VENUES
    out = {}
    for v in VENUES:
        k, sec = bybit_env_names(v)
        key, secret = os.environ.get(k, "").strip(), os.environ.get(sec, "").strip()
        if v == "testnet" and not (key and secret):
            key, secret = os.environ.get("BYBIT_API_KEY", "").strip(), os.environ.get("BYBIT_API_SECRET", "").strip()
        out[v] = (key, secret)
    return out


def _models():
    from .models import MODELS
    return MODELS


def load_settings() -> Settings:
    load_dotenv()
    return Settings()

"""Orchestration: market data -> state -> Kairos -> validation -> policy -> execution -> records."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any

import aiohttp

from .broker.bybit_private_ws import BybitPrivateStream
from .broker.bybit_rest import BybitTestnet
from .analytics import MarketModels
from .broker.executor import Executor
from . import runs as runs_mod
from .calibration import Calibrator
from .costs import CostModel
from .ensemble import symmetrize
from .evaluation import validation
from .config import Settings, save_env
from .db import DB
from .evaluation.labeler import Labeler
from .features import build_state, mirror_state
from .kairos import news as news_profile
from .kairos.client import KairosError, RateBudget, SystemOneClient
from .kairos.parse import validate
from .kairos.questions import QUESTION_SET_VERSION, build_questions
from .market.bybit_public import BybitPublicFeed
from .market.context import ContextFeed
from .market.store import MarketStore
from .market.testnet_ws import TestnetTickerStream
from .models import get as get_model
from .policy import decide, risk_pct_for

log = logging.getLogger("ctb.engine")


class Engine:
    def __init__(self, s: Settings):
        self.s = s
        self.db = DB(s.db_path)
        self.store = MarketStore(s.symbols)
        self.subscribers: set[asyncio.Queue] = set()
        self.session: aiohttp.ClientSession | None = None
        self.executor: Executor | None = None
        self.budget = RateBudget(s.kairos_max_requests_per_hour)
        self.news_budget = RateBudget(s.kairos_news_max_per_hour)
        self.inflight: set[str] = set()
        self.latest_state: dict[str, dict[str, Any]] = {}
        self.probe_rate = 0.05
        self.started_at = time.time()
        self.tasks: list[asyncio.Task] = []
        self.last_decision_error: str | None = None
        self.calibrator = Calibrator(self.db, tf_fn=lambda: self.s.decision_tf, provider_fn=lambda: self.s.model_provider,
                                     sources_fn=lambda: ("model", *self._ensemble_names()))
        self.kairos_sem = asyncio.Semaphore(max(1, s.kairos_concurrency))
        self.models = MarketModels(self.store)
        self.costs = CostModel(s, self.db)
        self.validation: dict[str, Any] = {}
        self.validated: dict[str, dict[str, Any]] = {}
        self.drift_state: dict[str, Any] = {}
        self.members: dict[str, SystemOneClient] = {}
        self.member_budgets: dict[str, RateBudget] = {}

    # ------------------------------------------------------------------ pub/sub
    def emit(self, kind: str, data: dict[str, Any]) -> None:
        msg = json.dumps({"kind": kind, "data": data}, default=str)
        for q in list(self.subscribers):
            if q.qsize() < 500:
                q.put_nowait(msg)

    # ------------------------------------------------------------------ lifecycle
    async def start(self) -> None:
        self.session = aiohttp.ClientSession()
        self.feed = BybitPublicFeed(self.store, self.session)
        self.context = ContextFeed(self.store, self.session)
        self.kairos = self._model_client()  # the active classification model (attribute name kept for history)
        self.labeler = Labeler(self.db, self.session, self.emit)
        self._build_members()
        self.feed.on_bar_close.append(self.on_bar_close)
        self.testnet_stream = TestnetTickerStream(self.store, self.session, self.s.symbols)
        self.testnet_stream.on_tick.append(self._on_testnet_tick)
        self.context.on_news.append(self.on_news)
        # the hourly budget must survive restarts, so seed it from what was actually sent
        hour_ago = time.time() - 3600
        for r in self.db.query("SELECT ts FROM decisions WHERE ts > ? ORDER BY ts", (hour_ago,)):
            self.budget.calls.append(r["ts"])
        for r in self.db.query("SELECT ts FROM news WHERE ts > ? ORDER BY ts", (hour_ago,)):
            self.news_budget.calls.append(r["ts"])
        self.run = runs_mod.ensure(self.db, self.s)
        try:
            self.calibrator.refit()
        except Exception as exc:  # noqa: BLE001
            self.db.event("error", "calibration", f"calibration refit failed: {exc}")
        await self.feed.warmup()
        if self.s.bybit_api_key and self.s.bybit_api_secret:
            await self._start_executor()
        self.tasks = [
            asyncio.create_task(self.feed.ws_loop(), name="ws"),
            asyncio.create_task(self.testnet_stream.run(), name="testnet_ws"),
            asyncio.create_task(self.feed.poll_loop(), name="poll"),
            asyncio.create_task(self.context.loop(), name="context"),
            asyncio.create_task(self.labeler.loop(), name="labeler"),
            asyncio.create_task(self._preview_loop(), name="preview"),
            asyncio.create_task(self.models.loop(), name="models"),
            asyncio.create_task(self._validation_loop(), name="validation"),
        ]
        self.db.event("info", "started", f"engine started; model={self.s.model_spec.label} ({self.kairos.model}) "
                                         f"{'ready' if self.kairos.configured else 'NOT CONFIGURED'}; "
                                         f"trading={'on' if self.s.trading_enabled else 'observe-only'}")

    async def _start_executor(self) -> None:
        self.executor = Executor(self.s, self.db, BybitTestnet(self.s.bybit_api_key, self.s.bybit_api_secret, self.session), self.emit)
        try:
            await self.executor.start()
            for sym, inst in self.executor.instruments.items():
                self.store[sym].instrument = inst
        except Exception as exc:  # noqa: BLE001
            self.db.event("error", "executor_start", str(exc))
            self.executor.sync_error = str(exc)
        self._reconcile_task = asyncio.create_task(self.executor.reconcile_loop(), name="reconcile")
        old = getattr(self, "_private_stream", None)
        if old:
            old.stop()
            self._private_task.cancel()
        self._private_stream = BybitPrivateStream(self.s.bybit_api_key, self.s.bybit_api_secret, self.session, self.executor.on_private)
        self.executor.stream = self._private_stream
        self.executor.run_id_fn = lambda: self.run["id"]
        self.executor.price_fn = lambda sym: self.store[sym].testnet_ticker
        self.executor.mainnet_fn = lambda sym: self.store[sym].ticker
        self.executor.mainnet_age_fn = lambda sym: self.store[sym].last_update
        self.executor.corr_fn = self.models.corr_with
        if getattr(self, "_stop_task", None):
            self._stop_task.cancel()
        self._stop_task = asyncio.create_task(self._stop_watch(), name="stop_watch")
        self._private_task = asyncio.create_task(self._private_stream.run(), name="private_ws")

    # ------------------------------------------------------------------ keys (set from the dashboard)
    async def set_bybit_keys(self, api_key: str, api_secret: str) -> dict[str, Any]:
        """Validate testnet keys against the account, then persist and hot-swap the executor."""
        if self.db.one("SELECT id FROM trades WHERE status IN ('open','opening') LIMIT 1"):
            raise ValueError("the bot has open trades on the current account; close them (or use the kill switch) before switching keys")
        probe = BybitTestnet(api_key, api_secret, self.session)
        await probe.sync_time()
        wallet = await probe.wallet()  # raises BybitError on bad key / wrong environment (10003)
        await probe.positions()  # needs Contract read permission
        old_task = getattr(self, "_reconcile_task", None)
        if old_task:
            old_task.cancel()
        save_env({"BYBIT_API_KEY": api_key, "BYBIT_API_SECRET": api_secret})
        self.s.bybit_api_key, self.s.bybit_api_secret = api_key, api_secret
        self.s.trading_enabled = False  # new account starts observe-only
        await self._start_executor()
        self.emit("event", self.db.event("warn", "keys", "Bybit testnet keys updated from dashboard; trading set to observe-only"))
        return {"ok": True, "equity": wallet.get("equity")}

    async def set_kairos_key(self, api_key: str, base_url: str | None = None) -> dict[str, Any]:
        updates = {"KAIROS_API_KEY": api_key}
        if base_url:
            base_url = base_url.strip().rstrip("/")
            if not base_url.startswith("https://"):
                raise ValueError("KAIROS_BASE_URL must be an https:// origin without /v1")
            updates["KAIROS_BASE_URL"] = base_url
        save_env(updates)
        if base_url:
            self.s.kairos_base_url = base_url
            if self.s.model_provider == "kairos":
                self.kairos = self._model_client("kairos")
        self.last_decision_error = None
        self.emit("event", self.db.event("info", "keys", "Kairos key updated from dashboard"))
        return await self.test_model("kairos")

    def _model_client(self, provider: str | None = None) -> SystemOneClient:
        from .models import get
        spec = get(provider or self.s.model_provider)
        base = self.s.kairos_base_url if spec.provider == "kairos" else None
        return SystemOneClient(spec, self.session, timeout_s=self.s.kairos_timeout_s, base_url=base)

    async def set_model(self, provider: str) -> dict[str, Any]:
        """Switch the classification model. Only while flat: a run never mixes two models, so the
        current run is closed and a new one starts with the new model."""
        from . import runs as runs_mod
        from .models import get
        spec = get(provider)
        if provider == self.s.model_provider:
            return {"ok": True, "unchanged": True, "model_provider": provider}
        if self.db.one("SELECT id FROM trades WHERE status IN ('open','opening') LIMIT 1"):
            raise ValueError("close the open bot trades first (KILL, or wait for their exits): a run must not mix two models")
        if not spec.api_key:
            raise ValueError(f"{spec.key_env} is not set; add the {spec.label} key under KEYS first")
        save_env({"CTB_MODEL_PROVIDER": provider})
        self.s.model_provider = provider
        self.kairos = self._model_client(provider)
        self.calibrator.refit()
        self.run = runs_mod.start(self.db, self.s)
        self.last_decision_error = None
        self.emit("event", self.db.event("warn", "model", f"model switched to {spec.label} ({self.kairos.model}); started {self.run['name']}"))
        self.emit("settings", {"model_provider": provider})
        return {"ok": True, "model_provider": provider, "model_id": self.kairos.model, "run": self.run}

    async def set_strategy(self, updates: dict[str, Any]) -> dict[str, Any]:
        """Change strategy settings. Only while flat, and a new run starts, so every run's results come
        from one strategy configuration."""
        from . import runs as runs_mod
        if self.db.one("SELECT id FROM trades WHERE status IN ('open','opening') LIMIT 1"):
            raise ValueError("close the open bot trades first (KILL, or wait for their exits): a run must not mix two strategies")
        before = self.s.strategy_view()
        env = self.s.set_strategy(updates)
        if not env:
            return {"ok": True, "unchanged": True, "strategy": before}
        save_env(env)
        self._build_members()
        self.calibrator.refit()
        changed = {k: v for k, v in self.s.strategy_view().items() if before.get(k) != v}
        self.run = runs_mod.start(self.db, self.s)
        self.emit("event", self.db.event("warn", "strategy", f"strategy changed ({', '.join(f'{k}={v}' for k, v in changed.items())}); "
                                                            f"started {self.run['name']}"))
        self.emit("settings", {"strategy": self.s.strategy_view()})
        return {"ok": True, "strategy": self.s.strategy_view(), "changed": changed, "run": self.run}

    async def set_model_key(self, provider: str, api_key: str) -> dict[str, Any]:
        """Save a model API key, then test it with one tiny request against that model."""
        from .models import get
        spec = get(provider)
        if provider == "kairos":
            return await self.set_kairos_key(api_key)
        save_env({spec.key_env: api_key})
        if provider == self.s.model_provider:
            self.kairos = self._model_client(provider)
        self.emit("event", self.db.event("info", "keys", f"{spec.label} key updated from dashboard"))
        return await self.test_model(provider)

    async def test_kairos(self) -> dict[str, Any]:
        return await self.test_model(self.s.model_provider)

    async def test_model(self, provider: str | None = None) -> dict[str, Any]:
        """One tiny live request (1 noul question, counts against the hourly budget)."""
        client = self.kairos if provider in (None, self.s.model_provider) else self._model_client(provider)
        spec = client.spec
        if not client.configured:
            return {"ok": False, "error": f"{spec.key_env} is not set"}
        if not self.budget.try_take():
            return {"ok": False, "error": "hourly model request budget exhausted"}
        try:
            res = await client.ask({"message": "Connectivity check from Calibrain Harness."},
                                        {"is_check": {"type": "noul", "instructions": "Does the message say it is a connectivity check?"}})
        except KairosError as exc:
            return {"ok": False, "error": f"{exc.kind}: {exc.message}", "request_id": exc.request_id}
        ans = res.answers.get("is_check", {})
        return {"ok": True, "latency_ms": round(res.latency_ms), "answer_noul": ans.get("noul"),
                "model": res.metadata.get("model") or client.model, "provider": spec.provider}

    async def _stop_watch(self) -> None:
        """Evaluate managed stops on the mainnet ticker (pushed ~10x/s) twice a second."""
        while True:
            await asyncio.sleep(0.5)
            ex = self.executor
            if not ex:
                continue
            for sym in list(ex._managed):
                try:
                    await ex.check_stop(sym)
                except Exception as exc:  # noqa: BLE001
                    log.warning("stop watch %s: %s", sym, exc)

    async def _on_testnet_tick(self, symbol: str, t: dict) -> None:
        if self.executor:
            await self.executor.on_testnet_tick(symbol, t)

    async def stop(self) -> None:
        self.feed.stop()
        self.testnet_stream.stop()
        self.context.stop()
        if getattr(self, "_reconcile_task", None):
            self._reconcile_task.cancel()
        if getattr(self, "_private_stream", None):
            self._private_stream.stop()
            self._private_task.cancel()
        if getattr(self, "_stop_task", None):
            self._stop_task.cancel()
        for t in self.tasks:
            t.cancel()
        if self.session:
            await self.session.close()

    async def _preview_loop(self) -> None:
        """Keep the latest would-be Kairos state per symbol fresh for the dashboard."""
        while True:
            for sym in self.s.symbols:
                try:
                    pos = self.executor.position_summary(sym) if self.executor else None
                    built = build_state(self.store, sym, self.s, position=pos, models=self.models, costs=self.costs.outcome_costs())
                    if built:
                        self.latest_state[sym] = {"state": built[0], "meta": built[1], "ts": time.time()}
                except Exception as exc:  # noqa: BLE001
                    self.store.record_error(f"preview:{sym}", repr(exc))
            await asyncio.sleep(5)

    # ------------------------------------------------------------------ decisions
    async def on_bar_close(self, symbol: str, tf: str) -> None:
        if tf == "1":
            self.emit("bar", {"symbol": symbol})
        if tf != self.s.decision_tf:
            return
        await asyncio.sleep(self.s.bar_settle_s)  # let the last trades of the closed bar arrive
        await self.decide(symbol)

    # ------------------------------------------------------------------ ensemble members and validation
    def _ensemble_names(self) -> tuple[str, ...]:
        return tuple(m for m in self.s.ensemble if m != self.s.model_provider and m in self.members)

    def _build_members(self) -> None:
        """Extra models asked alongside the primary on the real state (not mirrored, to bound cost)."""
        self.members = {}
        for name in self.s.ensemble:
            if name == self.s.model_provider:
                continue
            try:
                spec = get_model(name)
            except KeyError:
                continue
            if not spec.api_key:
                continue
            self.members[name] = SystemOneClient(spec, self.session, timeout_s=self.s.kairos_timeout_s, max_retries=1)
            self.member_budgets.setdefault(name, RateBudget(self.s.ensemble_max_rph))

    async def _validation_loop(self) -> None:
        """Refresh per-symbol validation and drift for the active model and timeframe every few minutes.
        Validation reads all runs of this model and timeframe: skill is a property of the model, not the run."""
        while True:
            try:
                v = await asyncio.to_thread(validation.compute, self.db, model=self.s.model_provider, decision_tf=self.s.decision_tf)
                self.validation = v
                self.validated = v["validated"]
                self.drift_state = {"alarm": v["drift"]["alarm"], "questions": v["drift"]["questions"]}
                if self.s.drift_action in ("base", "pause"):
                    self.calibrator.set_overrides({q: "drift" for q in v["drift"]["questions"]})
                else:
                    self.calibrator.set_overrides({})
                if v["drift"]["alarm"] and not getattr(self, "_drift_noted", False):
                    self._drift_noted = True
                    self.emit("event", self.db.event("warn", "model_drift",
                                                     f"drift detected on {', '.join(v['drift']['questions'])}; action: {self.s.drift_action}"))
                elif not v["drift"]["alarm"]:
                    self._drift_noted = False
            except Exception as exc:  # noqa: BLE001
                log.warning("validation: %s", exc)
            await asyncio.sleep(300)

    def _planned_notional(self, sl_bp: float, price: float) -> float | None:
        if not self.executor or not self.executor.wallet.get("equity"):
            return None
        eq = float(self.executor.wallet["equity"])
        pct, _ = risk_pct_for(self.s, exploration=self.s.exploration_mode)
        return min(eq * pct / 100 / (sl_bp / 10000), self.s.max_notional_usdt, eq * self.s.max_leverage * 0.9)

    async def decide(self, symbol: str, *, force_probe: bool = False) -> dict[str, Any] | None:
        if symbol in self.inflight:
            return None
        if not self.kairos.configured:
            return None
        if not self.budget.try_take():
            self.emit("event", self.db.event("warn", "budget", f"model hourly budget ({self.s.kairos_max_requests_per_hour}) exhausted; skipped {symbol}", symbol))
            return None
        self.inflight.add(symbol)
        try:
            pos = self.executor.position_summary(symbol) if self.executor else None
            costs = self.costs.outcome_costs()
            built = build_state(self.store, symbol, self.s, position=pos, models=self.models, costs=costs)
            if not built:
                self.db.event("warn", "state", "not enough data to build state", symbol)
                return None
            state, meta = built
            if meta.get("testnet_price"):
                meta["venue_divergence_bp"] = round(10000 * (meta["testnet_price"] / meta["price"] - 1), 1)
            meta["planned_notional"] = self._planned_notional(meta["sl_bp"], meta["price"])
            meta["validated"] = self.validated.get(symbol) or {"ok": False, "why": "no validation for this symbol yet"}
            meta["drift"] = self.drift_state
            questions = build_questions(meta["sl_bp"], meta["tp_bp"], meta["horizon_min"])

            mirror_task = None
            if self.s.symmetrize and self.budget.try_take():
                mirror_task = asyncio.create_task(self._ask_raw(mirror_state(state), questions))
            member_tasks = {name: asyncio.create_task(self._ask_raw(state, questions, client=c))
                            for name, c in self.members.items() if self.member_budgets[name].try_take()}
            row = await self._ask(symbol, state, questions, meta, probe=None)
            mirror_parsed, members = None, {}
            if mirror_task:
                res = await mirror_task
                mirror_parsed = res.get("parsed")
                await self._store_probe(symbol, mirror_state(state), questions, meta, res, probe="mirror_pair", probe_of=row["id"])
            for name, t in member_tasks.items():
                res = await t
                if res.get("parsed"):
                    members[name] = res["parsed"]
            if row.get("parsed_json") is not None:
                try:
                    weights = self.calibrator.maybe_refit()
                except Exception as exc:  # noqa: BLE001
                    weights = self.calibrator.weights
                    self.db.event("error", "calibration", f"calibration refit failed: {exc}", symbol)
                used = symmetrize(row["parsed_json"], mirror_parsed)
                decision = decide(used, meta, self.s, has_position=pos is not None, weights=weights, members=members)
                dj = {**decision.as_dict(), "cost_bp": meta["cost_bp"], "meta": meta, "parsed_used": used,
                      "symmetrized": mirror_parsed is not None, "members": members}
                self.db.update("decisions", row["id"], {"decision_json": dj, "action": decision.action})
                row.update(decision_json=dj, action=decision.action)
                self.emit("decision", self._decision_view(row))
                if decision.action in ("long", "short") and self.executor:
                    ev = decision.ev_long_r if decision.action == "long" else decision.ev_short_r
                    res = await self.executor.open(symbol=symbol, side=decision.action, sl_bp=meta["sl_bp"], tp_bp=meta["tp_bp"],
                                                   decision_id=row["id"], ev_r=ev or 0.0, exploration=decision.exploration,
                                                   decision_price=meta["price"], horizon_min=meta["horizon_min"],
                                                   vol_state=meta.get("vol_state"), regime=meta.get("regime"), events=meta.get("events"))
                    if not res.get("ok"):
                        self.emit("event", self.db.event("info", "signal_not_traded", f"{decision.action} signal not traded: {res.get('reason')}", symbol))
            else:
                self.emit("decision", self._decision_view(row))
            if force_probe or random.random() < self.probe_rate:
                asyncio.create_task(self._probes(symbol, state, questions, meta, row["id"]))
            return row
        finally:
            self.inflight.discard(symbol)

    async def _ask_raw(self, state: dict, questions: dict, client: SystemOneClient | None = None) -> dict[str, Any]:
        """One request whose answer is used inside a decision (mirror leg, ensemble member)."""
        client = client or self.kairos
        try:
            async with self.kairos_sem:
                res = await client.ask(state, questions)
            parsed, issues = validate(res.answers, questions)
            return {"res": res, "parsed": parsed if all(q in parsed for q in ("long_bracket", "short_bracket")) else None, "issues": issues}
        except KairosError as exc:
            return {"error": exc}

    async def _store_probe(self, symbol: str, state: dict, questions: dict, meta: dict, out: dict[str, Any], *, probe: str, probe_of: int) -> None:
        row: dict[str, Any] = {
            "ts": time.time(), "symbol": symbol, "bar_time": meta.get("bar_time"), "qset_version": QUESTION_SET_VERSION,
            "probe": probe, "probe_of": probe_of, "decision_tf": self.s.decision_tf, "run_id": self.run["id"],
            "model_provider": self.s.model_provider, "state_json": state, "questions_json": questions, "price": meta["price"],
            "sl_bp": meta["sl_bp"], "tp_bp": meta["tp_bp"], "horizon_min": meta["horizon_min"],
        }
        if out.get("error"):
            exc = out["error"]
            row.update(error_kind=exc.kind, error_msg=exc.message[:1000], request_id=exc.request_id)
        else:
            res = out["res"]
            row.update(answers_json=res.answers, parsed_json=out.get("parsed"), issues_json=out.get("issues"), usage_json=res.usage,
                       model_meta_json=res.metadata, latency_ms=round(res.latency_ms, 1), attempts=res.attempts,
                       request_bytes=res.request_bytes)
        self.db.insert("decisions", row)

    async def _ask(self, symbol: str, state: dict, questions: dict, meta: dict, *, probe: str | None, probe_of: int | None = None) -> dict[str, Any]:
        row: dict[str, Any] = {
            "ts": time.time(), "symbol": symbol, "bar_time": meta.get("bar_time"), "qset_version": QUESTION_SET_VERSION,
            "probe": probe, "probe_of": probe_of, "decision_tf": self.s.decision_tf, "run_id": self.run["id"],
            "model_provider": self.s.model_provider,
            "state_json": state, "questions_json": questions, "price": meta["price"],
            "testnet_price": meta.get("testnet_price"), "sl_bp": meta["sl_bp"], "tp_bp": meta["tp_bp"], "horizon_min": meta["horizon_min"],
        }
        try:
            async with self.kairos_sem:
                res = await self.kairos.ask(state, questions)
            parsed, issues = validate(res.answers, questions)
            row.update(answers_json=res.answers, parsed_json=parsed, issues_json=issues, usage_json=res.usage,
                       model_meta_json=res.metadata, latency_ms=round(res.latency_ms, 1), attempts=res.attempts,
                       request_bytes=res.request_bytes, request_id=res.metadata.get("request_id"))
            self.last_decision_error = None
        except KairosError as exc:
            row.update(error_kind=exc.kind, error_msg=exc.message[:1000], request_id=exc.request_id)
            self.last_decision_error = f"{exc.kind}: {exc.message[:200]}"
            self.emit("event", self.db.event("error", f"kairos_{exc.kind}", exc.message[:300], symbol))
        row["id"] = self.db.insert("decisions", row)
        return row

    async def _probes(self, symbol: str, state: dict, questions: dict, meta: dict, live_id: int) -> None:
        """Consistency probes (extra budget): identical repeat, and a mirrored market."""
        if not self.budget.try_take():
            return
        await self._ask(symbol, state, questions, meta, probe="repeat", probe_of=live_id)
        if self.s.symmetrize:
            self.emit("probe", {"symbol": symbol, "live_id": live_id})
            return  # every decision already has its mirrored pair
        if not (self.budget.try_take() and self.budget.try_take()):
            return
        base = {k: v for k, v in state.items() if k not in ("recent_headlines", "open_position")}
        base_row = await self._ask(symbol, base, questions, meta, probe="mirror_base", probe_of=live_id)
        await self._ask(symbol, mirror_state(state), questions, meta, probe="mirror", probe_of=base_row["id"])
        self.emit("probe", {"symbol": symbol, "live_id": live_id})

    # ------------------------------------------------------------------ news
    async def on_news(self, item: dict[str, Any]) -> None:
        if not (self.s.kairos_news_enabled and self.kairos.configured and self.kairos.spec.supports_news_return):
            return
        targets = item["symbols"][:2]
        for sym in targets:
            if not self.news_budget.try_take():
                return
            published = item.get("published_at") or datetime.fromtimestamp(item["first_seen_ts"], timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            body = news_profile.build_request(
                news_text=f"{item['title']}. {item['summary'][:400]}".strip(), target_asset=sym.replace("USDT", ""),
                source_name=item["source"], published_at_utc=published, horizon="15m", round_trip_cost_bp=self.s.round_trip_cost_bp)
            row: dict[str, Any] = {"id": f"{item['id']}:{sym}:15m", "ts": time.time(), "symbol": sym, "source": item["source"],
                                   "title": item["title"], "published_at": published, "horizon": "15m",
                                   "cost_bp": self.s.round_trip_cost_bp, "entry_ts": item["first_seen_ts"]}
            try:
                async with self.kairos_sem:
                    res = await self.kairos.ask(body["state"], body["questions"], task_profile="news_return")
                probs, issues = news_profile.validate(res.answers)
                row.update(probs_json=probs, issues_json=issues, usage_json=res.usage, latency_ms=round(res.latency_ms, 1),
                           request_id=res.metadata.get("request_id"))
            except KairosError as exc:
                row.update(error_kind=exc.kind, error_msg=exc.message[:500], request_id=exc.request_id)
            self.db.insert("news", row)
            self.emit("news", {"id": row["id"]})

    # ------------------------------------------------------------------ views
    @staticmethod
    def _decision_view(d: dict[str, Any]) -> dict[str, Any]:
        dj = d.get("decision_json") or {}
        return {
            "id": d["id"], "ts": d["ts"], "symbol": d["symbol"], "action": d.get("action"), "reason": dj.get("reason"),
            "ev_long_r": dj.get("ev_long_r"), "ev_short_r": dj.get("ev_short_r"), "setup_quality": dj.get("setup_quality"),
            "data_sufficient": dj.get("data_sufficient"), "coherence": dj.get("coherence"), "expected_move_in_d": dj.get("expected_move_in_d"),
            "latency_ms": d.get("latency_ms"), "error_kind": d.get("error_kind"), "error_msg": d.get("error_msg"),
            "issues": d.get("issues_json"), "parsed": dj.get("parsed_used") or d.get("parsed_json"), "parsed_raw": d.get("parsed_json"),
            "price": d.get("price"), "sl_bp": d.get("sl_bp"),
            "tp_bp": d.get("tp_bp"), "label": d.get("label_json"), "probe": d.get("probe"),
            "exploration": dj.get("exploration", False), "strict_action": dj.get("strict_action"),
            "ev_long_raw_r": dj.get("ev_long_raw_r"), "ev_short_raw_r": dj.get("ev_short_raw_r"),
            "ev_long_base_r": dj.get("ev_long_base_r"), "ev_short_base_r": dj.get("ev_short_base_r"),
            "p_up": dj.get("p_up"), "p_up_raw": dj.get("p_up_raw"), "p_big_move": dj.get("p_big_move"),
            "weights": dj.get("weights"), "qset": d.get("qset_version"),
            "events": dj.get("events") or [], "regime": dj.get("regime"), "side_source": dj.get("side_source"),
            "flow_score": dj.get("flow_score"), "model_side": dj.get("model_side"), "symmetrized": dj.get("symmetrized"),
            "gate_kinds": dj.get("gate_kinds"), "horizon_min": d.get("horizon_min"),
            "costs": (dj.get("meta") or {}).get("costs"), "bracket": (dj.get("meta") or {}).get("bracket"),
            "p_big_move_base": dj.get("p_big_move_base"),
        }

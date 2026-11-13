"""Online calibration: blend the models' probabilities with the instrument's own base rates.

    p_used = sum_i w_i * p_i + (1 - sum_i w_i) * p_base        (w_i >= 0, sum_i w_i <= 1)

p_base is the empirical frequency of each outcome for the exact bracket and horizon, measured on
the instrument's recent history at decision time (features.base_rates), so it is known before the
outcome and involves no look-ahead. The sources i are the primary model (symmetrized with its mirror
when that is on) and any extra ensemble models. Weights are chosen on labelled live decisions to
minimise the Brier score: a source that adds nothing beyond the base rates gets weight 0.

Three rules keep the fit honest:
- Independent windows only. Decisions on the same symbol whose horizons overlap share most of their
  outcome, so the fit uses a thinned sample: per symbol, a decision is kept only if it starts after
  the previous kept decision's horizon has ended. MIN_FIT counts these independent windows.
- One weight for both brackets. The long and short bracket questions are fitted together, so the
  policy never compares a base-rate long EV against a model short EV.
- Per model and per timeframe, as before: Jev never inherits Kairos's weights, and a 1m/30 min
  question never uses weights fitted on 5m/2 h.

Until a question has MIN_FIT independent labels, w = PRIOR_W on the primary model.
"""

from __future__ import annotations

import itertools
import time
from typing import Any, Callable

from .db import DB
from .ensemble import norm_weights
from .kairos.questions import BRACKET_IDS, RETURN_BIN_IDS

MIN_FIT = 30  # independent (non-overlapping) labelled windows
PRIOR_W = 0.5
CALIBRATED = ("long_bracket", "short_bracket", "return_distribution", "direction", "big_move")
GROUPS = {"bracket": ("long_bracket", "short_bracket"), "return_distribution": ("return_distribution",),
          "direction": ("direction",), "big_move": ("big_move",)}
GROUP_OF = {q: g for g, qs in GROUPS.items() for q in qs}


def blend(p: dict[str, float], base: dict[str, float], w: float) -> dict[str, float]:
    out = {k: w * p[k] + (1 - w) * base[k] for k in p}
    s = sum(out.values())
    return {k: v / s for k, v in out.items()}


def blend_p(p: float, base: float, w: float) -> float:
    return w * p + (1 - w) * base


def _brier(vec: list[float], k: int) -> float:
    return sum((x - (1.0 if i == k else 0.0)) ** 2 for i, x in enumerate(vec))


def independent(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per symbol, keep a decision only if it starts after the previous kept decision's horizon ended."""
    kept: list[dict[str, Any]] = []
    next_free: dict[str, float] = {}
    for d in sorted(decisions, key=lambda x: x["ts"]):
        sym = d.get("symbol") or ""
        if d["ts"] >= next_free.get(sym, 0.0):
            kept.append(d)
            next_free[sym] = d["ts"] + (d.get("horizon_min") or 0) * 60
    return kept


def _members(d: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Answer sets by source: 'model' is the primary (as used by the policy), others are ensemble members."""
    dj = d.get("decision_json") or {}
    out = {"model": dj.get("parsed_used") or d.get("parsed_json") or {}}  # symmetrized answer when the mirror was asked
    for name, parsed in (dj.get("members") or {}).items():
        if parsed:
            out[name] = parsed
    return out


def rows_for(question: str, decisions: list[dict[str, Any]], sources: tuple[str, ...] = ("model",)) -> list[tuple[list[list[float]], list[float], int]]:
    """For every labelled decision answered by all `sources`: (vectors per source, base vector, realised index)."""
    out = []
    for d in decisions:
        lab = d.get("label_json") or {}
        base = ((d.get("decision_json") or {}).get("meta") or {}).get("base_rates")
        mem = _members(d)
        if not base or not lab or lab.get("unlabelable") or any(question not in mem.get(s, {}) for s in sources):
            continue
        if question in ("long_bracket", "short_bracket"):
            side = question.split("_")[0]
            ids = BRACKET_IDS
            vecs = [[mem[s][question]["probs"][i] for i in ids] for s in sources]
            out.append((vecs, [base[question][i] for i in ids], ids.index(lab[side]["outcome"])))
        elif question == "return_distribution":
            vecs = [[mem[s][question]["probs"][i] for i in RETURN_BIN_IDS] for s in sources]
            out.append((vecs, [base[question][i] for i in RETURN_BIN_IDS], lab["return_bin"]))
        elif question == "direction":
            b = base["price_higher_at_horizon"]
            out.append(([[mem[s][question]["p"], 1 - mem[s][question]["p"]] for s in sources], [b, 1 - b], 0 if lab["return_pct"] > 0 else 1))
        elif question == "big_move":
            b = base["moved_at_least_one_stop_either_way"]
            d_pct = d["sl_bp"] / 100
            moved = lab["max_up_pct"] >= d_pct or -lab["max_down_pct"] >= d_pct
            out.append(([[mem[s][question]["p"], 1 - mem[s][question]["p"]] for s in sources], [b, 1 - b], 0 if moved else 1))
    return out


def _simplex(k: int) -> list[tuple[float, ...]]:
    step = 20 if k == 1 else 10
    grid = [i / step for i in range(step + 1)]
    return [w for w in itertools.product(grid, repeat=k) if sum(w) <= 1 + 1e-9]


def fit(rows: list[tuple], sources: tuple[str, ...] = ("model",)) -> dict[str, Any]:
    """Grid-search source weights on the Brier score. Reports each source alone, the base rates alone
    and the best blend. Rows may be (vec, base, y) for one source or (vecs, base, y) for several."""
    n = len(rows)
    k = len(sources)
    if not n:
        return {"n": 0, "w": PRIOR_W, "weights": {sources[0]: PRIOR_W}, "fitted": False}
    norm = [(r[0] if isinstance(r[0][0], list) else [r[0]], r[1], r[2]) for r in rows]

    def score(w: tuple[float, ...]) -> float:
        wb = 1 - sum(w)
        tot = 0.0
        for vecs, base, y in norm:
            p = [wb * base[j] + sum(w[i] * vecs[i][j] for i in range(k)) for j in range(len(base))]
            tot += _brier(p, y)
        return tot / n

    best = min(_simplex(k), key=score)
    res: dict[str, Any] = {"n": n, "brier_base_rates": round(score(tuple(0.0 for _ in sources)), 4),
                           "brier_best_blend": round(score(best), 4)}
    for i, name in enumerate(sources):
        alone = tuple(1.0 if j == i else 0.0 for j in range(k))
        b = round(score(alone), 4)
        res[f"brier_{name}"] = b
        res[f"{name}_skill_vs_base"] = round(1 - b / res["brier_base_rates"], 4) if res["brier_base_rates"] else None
    # names kept for older readers of the report
    res["brier_kairos"] = res[f"brier_{sources[0]}"]
    res["kairos_skill_vs_base"] = res[f"{sources[0]}_skill_vs_base"]
    res["best_weights"] = dict(zip(sources, best))
    res["best_w"] = best[0]
    res["fitted"] = n >= MIN_FIT
    res["weights"] = res["best_weights"] if res["fitted"] else {sources[0]: PRIOR_W}
    res["w"] = res["weights"].get(sources[0], 0.0)
    return res


class Calibrator:
    def __init__(self, db: DB, qset_prefix: str = "perp-v2", tf_fn: Callable[[], str] | None = None,
                 provider_fn: Callable[[], str] | None = None, sources_fn: Callable[[], tuple[str, ...]] | None = None):
        self.db = db
        self.qset_prefix = qset_prefix
        self.tf_fn = tf_fn
        self.provider_fn = provider_fn  # weights are per model too: Jev never inherits Kairos's
        self.sources_fn = sources_fn or (lambda: ("model",))
        self.tf: str | None = None
        self.provider: str | None = None
        self.report: dict[str, Any] = {}
        self.weights: dict[str, Any] = {q: {"model": PRIOR_W} for q in CALIBRATED}
        self.overrides: dict[str, str] = {}  # question -> reason the model weight is forced to 0 (drift)
        self.fitted_at = 0.0

    def labelled(self) -> list[dict[str, Any]]:
        sql = ("SELECT symbol, ts, horizon_min, parsed_json, label_json, decision_json, sl_bp FROM decisions WHERE probe IS NULL "
               "AND error_kind IS NULL AND label_json IS NOT NULL AND qset_version LIKE ?")
        params: list[Any] = [self.qset_prefix + "%"]
        if self.tf:
            sql += " AND decision_tf=?"
            params.append(self.tf)
        if self.provider:
            sql += " AND model_provider=?"
            params.append(self.provider)
        return self.db.query(sql, params)

    def refit(self) -> dict[str, Any]:
        self.tf = self.tf_fn() if self.tf_fn else None
        self.provider = self.provider_fn() if self.provider_fn else None
        decs = independent(self.labelled())
        sources = self.sources_fn()
        report: dict[str, Any] = {}
        for group, qs in GROUPS.items():
            rows = [r for q in qs for r in rows_for(q, decs, sources)]
            if len(sources) > 1 and len(rows) < MIN_FIT:  # not enough answers from every member yet
                rows, srcs = [r for q in qs for r in rows_for(q, decs, ("model",))], ("model",)
            else:
                srcs = sources
            res = fit(rows, srcs)
            res["independent_windows"] = res["n"] // len(qs)
            for q in qs:
                report[q] = res
            report[group] = res
        self.report = report
        self.weights = {q: dict(report[q]["weights"]) for q in CALIBRATED}
        for q in self.overrides:
            if q in self.weights:
                self.weights[q] = {k: 0.0 for k in self.weights[q]}
        self.fitted_at = time.time()
        return self.weights

    def set_overrides(self, overrides: dict[str, str]) -> None:
        """Force the model weights of these questions to 0 (base rates only), e.g. after drift."""
        if overrides != self.overrides:
            self.overrides = dict(overrides)
            self.refit()

    def maybe_refit(self, every_s: float = 900) -> dict[str, Any]:
        tf_changed = self.tf_fn is not None and self.tf_fn() != self.tf
        model_changed = self.provider_fn is not None and self.provider_fn() != self.provider
        if tf_changed or model_changed or time.time() - self.fitted_at > every_s:
            self.refit()
        return self.weights

    def weights_view(self) -> dict[str, dict[str, float]]:
        return {q: norm_weights(w) for q, w in self.weights.items()}

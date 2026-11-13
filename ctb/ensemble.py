"""Combine answers: symmetrize one model with its own mirrored answer, and blend several sources.

Symmetrization. The mirrored state describes the same market upside down, so a model with no
built-in directional bias would answer it with long and short swapped. Averaging the real answer
with the swapped mirrored answer removes any constant lean toward one side:

    p_long_sym  = (p_long(state)  + p_short(mirror)) / 2
    p_short_sym = (p_short(state) + p_long(mirror))  / 2
    p_up_sym    = (p_up(state) + 1 - p_up(mirror)) / 2

Symmetric questions (big move, mean reversion, setup quality) are plain averages of the two reads,
which also halves answer noise. Anything genuinely directional in the data survives, because the
mirror flips the data too.

Blending. The probability the policy uses is a convex combination of sources:

    p = sum_i w_i * p_i + (1 - sum_i w_i) * p_base_rate,   w_i >= 0, sum_i w_i <= 1

with one weight per source (the primary model, optional extra models) fitted by the calibrator.
"""

from __future__ import annotations

import copy
from typing import Any

from .kairos.questions import BRACKET_IDS, RETURN_BIN_IDS


def _avg(a: dict[str, float], b: dict[str, float]) -> dict[str, float]:
    out = {k: (a[k] + b.get(k, a[k])) / 2 for k in a}
    s = sum(out.values())
    return {k: v / s for k, v in out.items()} if s > 0 else out


def _choice(probs: dict[str, float]) -> str:
    return max(probs, key=probs.get)


def symmetrize(real: dict[str, Any], mirror: dict[str, Any] | None) -> dict[str, Any]:
    if not mirror:
        return real
    out = copy.deepcopy(real)
    if "long_bracket" in real and "short_bracket" in mirror:
        p = _avg(real["long_bracket"]["probs"], mirror["short_bracket"]["probs"])
        out["long_bracket"] = {**real["long_bracket"], "probs": p, "choice": _choice(p)}
    if "short_bracket" in real and "long_bracket" in mirror:
        p = _avg(real["short_bracket"]["probs"], mirror["long_bracket"]["probs"])
        out["short_bracket"] = {**real["short_bracket"], "probs": p, "choice": _choice(p)}
    if "return_distribution" in real and "return_distribution" in mirror:
        rp, mp = real["return_distribution"]["probs"], mirror["return_distribution"]["probs"]
        flipped = {f"bin_{i}": mp[f"bin_{6 - i}"] for i in range(7)}
        p = _avg({k: rp[k] for k in RETURN_BIN_IDS}, flipped)
        out["return_distribution"] = {**real["return_distribution"], "probs": p, "choice": _choice(p)}
    if "direction" in real and "direction" in mirror:
        out["direction"] = {**real["direction"], "p": round((real["direction"]["p"] + 1 - mirror["direction"]["p"]) / 2, 4)}
    for q in ("big_move", "mean_reversion"):
        if q in real and q in mirror:
            out[q] = {**real[q], "p": round((real[q]["p"] + mirror[q]["p"]) / 2, 4)}
    if "setup_quality" in real and "setup_quality" in mirror:
        sq = dict(real["setup_quality"])
        sq["score"] = round((real["setup_quality"]["score"] + mirror["setup_quality"]["score"]) / 2, 3)
        if isinstance(sq.get("probs"), list) and isinstance(mirror["setup_quality"].get("probs"), list):
            sq["probs"] = [(a + b) / 2 for a, b in zip(sq["probs"], mirror["setup_quality"]["probs"])]
        out["setup_quality"] = sq
    out["_symmetrized"] = True
    return out


def lean(parsed: dict[str, Any]) -> float | None:
    """Directional lean of one answer set: P(long target first) - P(short target first)."""
    try:
        return parsed["long_bracket"]["probs"]["target_first"] - parsed["short_bracket"]["probs"]["target_first"]
    except (KeyError, TypeError):
        return None


def norm_weights(w: Any) -> dict[str, float]:
    """Weights may be stored as one float (the primary model only) or a dict per source."""
    if isinstance(w, dict):
        return {k: float(v) for k, v in w.items()}
    return {"model": float(w)}


def blend_sources(sources: dict[str, dict[str, float]], base: dict[str, float], weights: Any) -> dict[str, float]:
    w = norm_weights(weights)
    used = {k: v for k, v in w.items() if k in sources and v > 0}
    wb = max(0.0, 1 - sum(used.values()))
    out = {k: wb * base[k] + sum(wi * sources[name][k] for name, wi in used.items()) for k in base}
    s = sum(out.values())
    return {k: v / s for k, v in out.items()} if s > 0 else out


def blend_scalar(sources: dict[str, float], base: float, weights: Any) -> float:
    w = norm_weights(weights)
    used = {k: v for k, v in w.items() if k in sources and v > 0}
    return max(0.0, 1 - sum(used.values())) * base + sum(wi * sources[name] for name, wi in used.items())


def source_probs(question: str, parsed_by_source: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The probability vector (or scalar) each source gave for one calibrated question."""
    out: dict[str, Any] = {}
    for name, parsed in parsed_by_source.items():
        if not parsed or question not in parsed:
            continue
        a = parsed[question]
        if question in ("long_bracket", "short_bracket"):
            out[name] = {k: a["probs"][k] for k in BRACKET_IDS}
        elif question == "return_distribution":
            out[name] = {k: a["probs"][k] for k in RETURN_BIN_IDS}
        else:
            out[name] = a["p"]
    return out

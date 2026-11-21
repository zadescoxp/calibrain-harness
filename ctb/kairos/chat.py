"""Chat-LLM adapter: lets any OpenAI-compatible chat model (OpenRouter by default) stand in for a
System One classifier.

The model gets the state and the typed questions and must reply with one probability per option,
enforced by a strict JSON schema (response_format=json_schema, and provider.require_parameters so
OpenRouter only routes to providers that honour it). The reply is converted into the System One
answer shapes that ctb.kairos.parse.validate expects:

- noul   -> {"type": "noul", "noul": p, "confidence": max(p, 1-p)}
- choice -> {"type": "choice", "choice": argmax, "probabilities": {...}, "confidence": max}
- score  -> {"type": "score", "score": E[level], "probabilities": [...], "confidence": max}

Repairs (percentages instead of fractions, sums that drift from 1, negatives) are applied here,
because the adapter plays the role of the model, and each one is recorded in metadata so the
evaluation can still see how often the raw LLM output was off-contract.

An LLM's stated probabilities are usually worse calibrated than a trained classifier's. The
harness's calibration layer blends every model with the base rates and fits the weight on labelled
outcomes, so a model that adds nothing stops driving trades; read EVAL/VALID before trusting it.

Env:
- CTB_LLM_REASONING: off (default) | minimal | low | medium | high
- CTB_LLM_TEMPERATURE: default 0 (repeat-consistency probes expect stable answers)
- CTB_LLM_MAX_TOKENS: default 1500 without reasoning, 8000 with it
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from .client import KairosError, KairosResult, SystemOneClient

SYSTEM_PROMPT = """You are a forecasting engine. You answer typed questions about a market state with calibrated probabilities.

Rules:
- Read the STATE JSON. Every question is independent: answer each one from the state alone.
- A probability of 0.7 means you expect the outcome about 7 times in 10 across situations like this one. Avoid 0 and 1 unless the state makes the outcome certain.
- When the state has historical_base_rates for a question, start from those frequencies and move away from them only as far as specific evidence in the state justifies. Weak or mixed evidence means staying close to them.
- "noul" question: give p_true, the probability that the "true" criterion holds.
- "choice" question: give one probability per option. They must sum to 1.
- "score" question: give one probability per level (level_0 is the lowest). They must sum to 1.
- Reply with only the JSON object the schema asks for. No prose."""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def _levels(q: dict[str, Any]) -> list[str]:
    crit = q.get("criteria")
    if not isinstance(crit, list) or not crit:
        raise KairosError("invalid_request", "score questions need a non-empty criteria list")
    return [f"level_{i}" for i in range(len(crit))]


def _options(q: dict[str, Any]) -> list[str]:
    t = q.get("type")
    if t == "noul":
        return ["p_true"]
    if t == "choice":
        crit = q.get("criteria")
        if not isinstance(crit, dict) or not crit:
            raise KairosError("invalid_request", "choice questions need a criteria object")
        return list(crit)
    if t == "score":
        return _levels(q)
    raise KairosError("invalid_request", f"question type {t!r} is not supported by the chat adapter")


def build_schema(questions: dict[str, Any]) -> dict[str, Any]:
    """Strict JSON schema: one object per question, one number per option."""
    props = {}
    for qid, q in questions.items():
        opts = _options(q)
        props[qid] = {"type": "object", "properties": {o: {"type": "number"} for o in opts},
                      "required": opts, "additionalProperties": False}
    return {"type": "object", "properties": props, "required": list(questions), "additionalProperties": False}


def render_questions(questions: dict[str, Any]) -> dict[str, Any]:
    """Questions as the model sees them; score criteria are keyed by level name."""
    out = {}
    for qid, q in questions.items():
        r = {"type": q.get("type")}
        if q.get("instructions"):
            r["instructions"] = q["instructions"]
        crit = q.get("criteria")
        if q.get("type") == "score" and isinstance(crit, list):
            r["criteria"] = {f"level_{i}": c for i, c in enumerate(crit)}
        elif crit is not None:
            r["criteria"] = crit
        out[qid] = r
    return out


def build_messages(state: Any, questions: dict[str, Any]) -> list[dict[str, str]]:
    st = state if isinstance(state, str) else json.dumps(state, separators=(",", ":"), allow_nan=False, default=str)
    qs = json.dumps(render_questions(questions), indent=1, ensure_ascii=False)
    return [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"STATE:\n{st}\n\nQUESTIONS:\n{qs}"}]


def _num(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def _distribution(raw: dict[str, Any], opts: list[str], qid: str, repairs: list[str]) -> list[float]:
    vals = []
    for o in opts:
        v = _num(raw.get(o))
        if v is None:
            raise KairosError("malformed_response", f"{qid}.{o} is not a number: {raw.get(o)!r}")
        if v < 0:
            repairs.append(f"{qid}.{o}: negative {v} set to 0")
            v = 0.0
        vals.append(v)
    total = sum(vals)
    if total <= 0:
        repairs.append(f"{qid}: all zero, set uniform")
        return [1 / len(opts)] * len(opts)
    if abs(total - 1) > 0.005:
        repairs.append(f"{qid}: sum {total:.4f} renormalised")
    return [v / total for v in vals]


def to_answers(reply: dict[str, Any], questions: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Convert the LLM's per-option numbers into System One answers. Missing questions are left out
    (validate() reports them as missing answers, never as a judgment)."""
    answers: dict[str, Any] = {}
    repairs: list[str] = []
    for qid, q in questions.items():
        raw = reply.get(qid)
        if not isinstance(raw, dict):
            continue
        t = q.get("type")
        if t == "noul":
            p = _num(raw.get("p_true"))
            if p is None:
                raise KairosError("malformed_response", f"{qid}.p_true is not a number: {raw.get('p_true')!r}")
            if 1 < p <= 100:
                repairs.append(f"{qid}: percentage {p} read as {p / 100}")
                p /= 100
            if not 0 <= p <= 1:
                repairs.append(f"{qid}: {p} clamped to [0, 1]")
                p = min(1.0, max(0.0, p))
            answers[qid] = {"type": "noul", "noul": p, "confidence": max(p, 1 - p)}
        elif t == "choice":
            opts = _options(q)
            probs = _distribution(raw, opts, qid, repairs)
            best = max(range(len(opts)), key=lambda i: probs[i])  # ties -> first option, as the contract says
            answers[qid] = {"type": "choice", "choice": opts[best], "probabilities": dict(zip(opts, probs)),
                            "confidence": probs[best]}
        elif t == "score":
            probs = _distribution(raw, _levels(q), qid, repairs)
            answers[qid] = {"type": "score", "score": sum(i * p for i, p in enumerate(probs)), "probabilities": probs,
                            "confidence": max(probs)}
    return answers, repairs


def _reasoning() -> dict[str, Any]:
    mode = os.environ.get("CTB_LLM_REASONING", "off").strip().lower()
    if mode in ("", "off", "none", "0", "false"):
        return {"enabled": False}
    if mode in ("minimal", "low", "medium", "high"):
        return {"effort": mode}
    raise KairosError("invalid_request", f"CTB_LLM_REASONING={mode!r}; use off, minimal, low, medium or high")


def build_body(model: str, state: Any, questions: dict[str, Any]) -> dict[str, Any]:
    reasoning = _reasoning()
    default_max = 1500 if reasoning == {"enabled": False} else 8000
    try:
        temperature = float(os.environ.get("CTB_LLM_TEMPERATURE", "0"))
        max_tokens = int(os.environ.get("CTB_LLM_MAX_TOKENS", default_max))
    except ValueError as exc:
        raise KairosError("invalid_request", f"bad CTB_LLM_* setting: {exc}") from exc
    return {
        "model": model,
        "messages": build_messages(state, questions),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning": reasoning,
        "response_format": {"type": "json_schema",
                            "json_schema": {"name": "answers", "strict": True, "schema": build_schema(questions)}},
        # only route to providers that honour response_format; never silently drop the schema
        "provider": {"require_parameters": True},
    }


def parse_reply(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or not isinstance(data.get("choices"), list) or not data["choices"]:
        raise KairosError("malformed_response", f"response has no choices: {str(data)[:300]}")
    ch = data["choices"][0] or {}
    msg = ch.get("message") or {}
    if msg.get("refusal"):
        raise KairosError("refusal", str(msg["refusal"])[:300])
    if ch.get("finish_reason") == "length":
        raise KairosError("context_too_long", "reply hit max_tokens before the JSON was complete (raise CTB_LLM_MAX_TOKENS)")
    content = msg.get("content")
    if isinstance(content, list):  # some providers return content parts
        content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
    if not isinstance(content, str) or not content.strip():
        raise KairosError("malformed_response", f"empty reply (finish_reason={ch.get('finish_reason')!r})")
    text = _FENCE.sub("", content.strip())
    try:
        reply = json.loads(text)
    except json.JSONDecodeError as exc:
        raise KairosError("malformed_response", f"reply is not JSON: {text[:300]}") from exc
    if not isinstance(reply, dict):
        raise KairosError("malformed_response", f"reply is not a JSON object: {text[:300]}")
    return reply


class ChatDecisionClient(SystemOneClient):
    """Same interface as SystemOneClient (ask -> KairosResult with System One answers)."""

    async def ask(self, state: Any, questions: dict[str, Any], *, task_profile: str | None = None) -> KairosResult:
        key = self._key()
        spec = self.spec
        if not key:
            raise KairosError("not_configured", f"{spec.key_env} is not set")
        if task_profile:
            raise KairosError("invalid_request", f"{spec.label} does not support task_profile={task_profile!r}")
        if not questions:
            raise KairosError("invalid_request", "at least one question is required")
        body = build_body(self.model, state, questions)
        payload = json.dumps(body, separators=(",", ":"), allow_nan=False)
        if len(payload.encode()) > spec.max_payload_bytes:
            raise KairosError("payload_too_large", f"request exceeds the {spec.max_payload_bytes // 1024 // 1024} MiB limit of {spec.label}")
        data, attempt, t0 = await self._post(payload, key)
        latency = (time.perf_counter() - t0) * 1000
        if isinstance(data, dict) and isinstance(data.get("error"), dict):  # OpenRouter can send errors with HTTP 200
            err = data["error"]
            raise KairosError(f"upstream_{err.get('code', 'error')}", str(err.get("message", err))[:500])
        reply = parse_reply(data)
        answers, repairs = to_answers(reply, questions)
        meta: dict[str, Any] = {k: data.get(k) for k in ("id", "model", "provider") if k in data}
        meta["adapter"] = "chat-json-schema-v1"
        meta["llm_raw"] = reply
        if repairs:
            meta["adapter_repairs"] = repairs
        return KairosResult(answers=answers, usage=data.get("usage") or {}, metadata=meta, latency_ms=latency,
                            attempts=attempt, request_bytes=len(payload.encode()), raw=data)

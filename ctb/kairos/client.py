"""System One client for any model in ctb.models (Kairos on Minara Cloud, Jev on TypeSafe):

- POST {base_url}/v1/systemone, non-streaming, {"model", "state", "questions"}.
- Bearer key from the server environment only; never logged.
- Errors in either `detail{...}` or `error{...}` shape; keep request IDs when the API sends them.
- Bounded retries on the model's retryable statuses only (rate limits and overload: 429, 529,
  502/503/504). Invalid input is never retried unchanged.
- A failed request or missing answer is an ERROR, never a negative judgment.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from ..models import MODELS, ModelSpec


class KairosError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None, request_id: str | None = None, retryable: bool = False):
        super().__init__(f"[{kind}] {message}")
        self.kind = kind
        self.message = message
        self.status = status
        self.request_id = request_id
        self.retryable = retryable


@dataclass
class KairosResult:
    answers: dict[str, Any]
    usage: dict[str, Any]
    metadata: dict[str, Any]
    latency_ms: float
    attempts: int
    request_bytes: int
    raw: dict[str, Any] = field(default_factory=dict)


class RateBudget:
    """Sliding one-hour request budget so a bug can never burn through the account."""

    def __init__(self, per_hour: int):
        self.per_hour = per_hour
        self.calls: deque[float] = deque()

    def try_take(self) -> bool:
        now = time.time()
        while self.calls and now - self.calls[0] > 3600:
            self.calls.popleft()
        if len(self.calls) >= self.per_hour:
            return False
        self.calls.append(now)
        return True

    def used(self) -> int:
        now = time.time()
        return sum(1 for t in self.calls if now - t <= 3600)


def _error_from(status: int, body: Any, spec: ModelSpec | None = None) -> KairosError:
    spec = spec or MODELS["kairos"]
    msg, code, rid = f"HTTP {status}", None, None
    if isinstance(body, dict):
        d = body.get("detail")
        e = body.get("error")
        if isinstance(d, dict):
            msg = d.get("message") or msg
            code = d.get("code") or d.get("error_type")
            rid = d.get("request_id") or d.get("upstream_request_id")
        elif isinstance(d, (str, list)):
            msg = json.dumps(d)[:500] if isinstance(d, list) else d
        if isinstance(e, dict):
            msg = e.get("message") or msg
            code = e.get("code") or code
            rid = e.get("request_id") or rid
        elif isinstance(e, str):
            msg = e
    elif isinstance(body, str) and body:
        msg = body[:300]
    if status in (401, 403):
        kind = "auth"
        if spec.provider == "kairos" and (status == 403 or "model" in str(msg).lower() or "group" in str(msg).lower()):
            msg += " | This API key may not access DMIND/Kairos: check the key belongs to a Kairos-enabled group and its model permissions include the exact model ID."
        elif status == 401:
            msg += f" | Check {spec.key_env} ({spec.key_url})."
    elif status == 529:
        kind = "overloaded"
    elif status == 429:
        kind = "rate_limited"
    elif status in (400, 422):
        kind = "context_too_long" if "max_tokens_exceeded" in str(msg) else "invalid_request"
    elif status == 413:
        kind = "payload_too_large"
    elif status in (502, 503, 504):
        kind = "upstream_unavailable"
    else:
        kind = f"http_{status}"
    return KairosError(kind, f"{msg}" + (f" (code={code})" if code else ""), status, rid, retryable=status in spec.retry_statuses)


class SystemOneClient:
    """One client per model. The spec supplies endpoint, model id, key, limits and retry policy."""

    def __init__(self, spec: ModelSpec, session: aiohttp.ClientSession, *, timeout_s: float = 120, max_retries: int = 2,
                 base_url: str | None = None, api_key_getter=None, model: str | None = None):
        self.spec = spec
        self.provider = spec.provider
        self.url = (base_url or spec.base_url).rstrip("/") + spec.path
        self._key = api_key_getter or (lambda: spec.api_key)
        self.session = session
        self.model = model or spec.model
        self.timeout = aiohttp.ClientTimeout(total=timeout_s, connect=10)
        self.max_retries = max_retries

    @property
    def configured(self) -> bool:
        return bool(self._key())

    async def _post(self, payload: str, key: str) -> tuple[Any, int, float]:
        """POST with bounded retries on the model's retryable statuses. Returns (body, attempts, t0)."""
        spec = self.spec
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

        attempt = 0
        t0 = time.perf_counter()
        while True:
            attempt += 1
            try:
                async with self.session.post(self.url, data=payload, headers=headers, timeout=self.timeout) as r:
                    text = await r.text()
                    try:
                        data = json.loads(text) if text else {}
                    except json.JSONDecodeError:
                        data = text
                    if r.status != 200:
                        err = _error_from(r.status, data, spec)
                        if err.retryable and attempt <= self.max_retries:
                            retry_after = r.headers.get("Retry-After")
                            delay = float(retry_after) if retry_after and retry_after.replace(".", "").isdigit() else 2.0 * attempt
                            await asyncio.sleep(min(delay, 30))
                            continue
                        raise err
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                if attempt <= self.max_retries:
                    await asyncio.sleep(2.0 * attempt)
                    continue
                raise KairosError("transport", repr(exc)) from exc
            break
        return data, attempt, t0

    async def ask(self, state: Any, questions: dict[str, Any], *, task_profile: str | None = None) -> KairosResult:
        key = self._key()
        spec = self.spec
        if not key:
            raise KairosError("not_configured", f"{spec.key_env} is not set")
        if not questions or (spec.max_questions and len(questions) > spec.max_questions):
            raise KairosError("invalid_request", f"{spec.label} accepts 1-{spec.max_questions or 'n'} questions per request, got {len(questions)}")
        body: dict[str, Any] = {"model": self.model, "state": state, "questions": questions}
        if task_profile:
            if not spec.supports_news_return:
                raise KairosError("invalid_request", f"{spec.label} does not support task_profile={task_profile!r}")
            body["task_profile"] = task_profile
        payload = json.dumps(body, separators=(",", ":"), allow_nan=False)
        if len(payload.encode()) > spec.max_payload_bytes:
            raise KairosError("payload_too_large", f"request exceeds the {spec.max_payload_bytes // 1024 // 1024} MiB limit of {spec.label}")
        data, attempt, t0 = await self._post(payload, key)

        latency = (time.perf_counter() - t0) * 1000
        if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
            raise KairosError("malformed_response", f"response has no answers object: {str(data)[:300]}")
        meta = {k: data.get(k) for k in ("model", "model_version", "prompt_version", "calibration_version", "task_profile", "request_id", "timings_ms", "id", "provider") if k in data}
        if isinstance(data.get("metadata"), dict):
            meta.update(data["metadata"])
        return KairosResult(
            answers=data["answers"], usage=data.get("usage") or {}, metadata=meta,
            latency_ms=latency, attempts=attempt, request_bytes=len(payload.encode()), raw=data,
        )


# Backwards-compatible names (the client predates multi-model support).
ModelError = KairosError


def KairosClient(base_url: str, api_key_getter, session: aiohttp.ClientSession, *, model: str = "DMIND/Kairos",  # noqa: N802
                 timeout_s: float = 120, max_retries: int = 2) -> SystemOneClient:
    return SystemOneClient(MODELS["kairos"], session, timeout_s=timeout_s, max_retries=max_retries,
                           base_url=base_url, api_key_getter=api_key_getter, model=model)


def make_client(spec: ModelSpec, session: aiohttp.ClientSession, **kw: Any) -> SystemOneClient:
    """The right client for a model spec: System One APIs as-is, chat LLMs through the adapter."""
    if spec.kind == "chat":
        from .chat import ChatDecisionClient
        return ChatDecisionClient(spec, session, **kw)
    return SystemOneClient(spec, session, **kw)

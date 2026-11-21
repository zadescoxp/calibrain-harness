"""Classification models the harness can drive. Two kinds:

- kind="systemone": a System One style API, POST {base_url}{path} with {"model", "state", "questions"}
  and typed answers back (Kairos, Jev, and any decision model on OpenRouter's Decisions API).
- kind="chat": an ordinary chat LLM on an OpenAI-compatible endpoint (OpenRouter). The client asks it
  for per-option probabilities through a strict JSON schema and converts the reply into the same
  typed answers, so everything downstream stays unchanged. See ctb/kairos/chat.py.

Adding a model = adding a ModelSpec here (and its key in .env). Everything downstream (questions,
validation, calibration, policy, evaluation) is model-agnostic; decisions, calibration weights and
runs are kept separate per model so one model's results never leak into another's.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class ModelSpec:
    provider: str                 # stable id stored with every decision ("kairos", "jev")
    label: str                    # shown in the terminal
    vendor: str
    key_env: str                  # environment variable holding the API key
    base_url_env: str | None
    default_base_url: str
    model_env: str | None         # environment variable that may override the model id
    default_model: str
    max_questions: int | None     # hard limit per request, if the API has one
    max_payload_bytes: int
    retry_statuses: frozenset[int] = field(default_factory=frozenset)
    supports_news_return: bool = False  # Kairos-only task_profile for headline forecasts
    key_url: str = ""
    key_hint: str = ""
    kind: str = "systemone"       # "systemone" | "chat"
    path: str = "/v1/systemone"   # appended to base_url

    @property
    def api_key(self) -> str:
        return os.environ.get(self.key_env, "").strip()

    @property
    def base_url(self) -> str:
        raw = os.environ.get(self.base_url_env, "") if self.base_url_env else ""
        return (raw.strip() or self.default_base_url).rstrip("/")

    @property
    def model(self) -> str:
        raw = os.environ.get(self.model_env, "") if self.model_env else ""
        return raw.strip() or self.default_model


MODELS: dict[str, ModelSpec] = {
    "kairos": ModelSpec(
        provider="kairos", label="Kairos", vendor="DMind via Minara Cloud",
        key_env="KAIROS_API_KEY", base_url_env="KAIROS_BASE_URL", default_base_url="https://minara.cloud",
        model_env=None, default_model="DMIND/Kairos",
        max_questions=8, max_payload_bytes=1024 * 1024,
        retry_statuses=frozenset({429, 502, 503, 504}), supports_news_return=True,
        key_url="https://minara.cloud",
        key_hint="The key must belong to a Kairos-enabled group with access to DMIND/Kairos.",
    ),
    "jev": ModelSpec(
        provider="jev", label="Jev", vendor="TypeSafe",
        key_env="TYPESAFE_API_KEY", base_url_env="TYPESAFE_BASE_URL", default_base_url="https://api.typesafe.ai",
        # pinned version by default: the jev-latest alias moves with releases, which would mix model
        # versions inside one evaluation. Override with CTB_JEV_MODEL (e.g. jev-latest).
        model_env="CTB_JEV_MODEL", default_model="jev-1.14.0",
        max_questions=None, max_payload_bytes=32 * 1024 * 1024,
        retry_statuses=frozenset({429, 529, 502, 503, 504}), supports_news_return=False,
        key_url="https://console.typesafe.ai/keys",
        key_hint="Create a key in the TypeSafe console. Requests are billed per input token.",
    ),
    # Any decision model OpenRouter serves (Jev, Clef, D1, ...), same contract as above. OpenRouter's
    # chat API lives under /api/v1 but decisions sit at /api/alpha/decisions.
    "ordecide": ModelSpec(
        provider="ordecide", label="OR Decide", vendor="OpenRouter Decisions",
        key_env="OPENROUTER_API_KEY", base_url_env="OPENROUTER_BASE_URL", default_base_url="https://openrouter.ai/api",
        model_env="CTB_OR_DECIDE_MODEL", default_model="google/gemini-decision-1",
        max_questions=None, max_payload_bytes=4 * 1024 * 1024,
        retry_statuses=frozenset({429, 502, 503, 524, 529}),
        key_url="https://openrouter.ai/settings/keys",
        key_hint="An OpenRouter key with credits. Billed to your OpenRouter account.",
        path="/alpha/decisions",
    ),
    # A chat LLM forced to answer with probabilities (see ctb/kairos/chat.py). Pinned by default so
    # one provider id never mixes model versions; override with CTB_LLM_MODEL (starts a new run).
    "llm": ModelSpec(
        provider="llm", label="LLM", vendor="OpenRouter chat",
        key_env="OPENROUTER_API_KEY", base_url_env="OPENROUTER_BASE_URL", default_base_url="https://openrouter.ai/api",
        model_env="CTB_LLM_MODEL", default_model="deepseek/deepseek-v4.1-flash-1017",
        max_questions=None, max_payload_bytes=4 * 1024 * 1024,
        retry_statuses=frozenset({429, 502, 503, 524, 529}),
        key_url="https://openrouter.ai/settings/keys",
        key_hint="An OpenRouter key with credits. Billed to your OpenRouter account.",
        kind="chat", path="/v1/chat/completions",
    ),
}


OR_PREFIX = "or:"
_OR_ID = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._:-]*$")


def get(provider: str) -> ModelSpec:
    """A registered model, or "or:<openrouter-model-id>" for any OpenRouter decision model. The
    or: form gives each model its own provider id, so runs, calibration weights and evaluation never
    mix two models (use it in CTB_MODEL_PROVIDER and CTB_ENSEMBLE)."""
    if provider in MODELS:
        return MODELS[provider]
    if provider.startswith(OR_PREFIX) and _OR_ID.match(provider[len(OR_PREFIX):]):
        mid = provider[len(OR_PREFIX):]
        vendor, name = mid.split("/", 1)
        return replace(MODELS["ordecide"], provider=provider, label=name.split(":")[0], vendor=f"OpenRouter · {vendor}",
                       model_env=None, default_model=mid)
    raise ValueError(f"unknown model provider {provider!r}; choose one of {', '.join(MODELS)} or or:<openrouter-model-id>")


def listed(extra: list[str] | tuple[str, ...] = ()) -> dict[str, ModelSpec]:
    """Registered models plus any or: models in use (active model, ensemble members)."""
    out = dict(MODELS)
    for p in extra:
        if p.startswith(OR_PREFIX) and p not in out:
            try:
                out[p] = get(p)
            except ValueError:
                pass
    return out

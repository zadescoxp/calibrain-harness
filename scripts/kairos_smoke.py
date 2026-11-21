"""One live model request with a real market state; prints validation results (never the key).

    .venv/bin/python scripts/kairos_smoke.py SOLUSDT              # the active model (CTB_MODEL_PROVIDER)
    .venv/bin/python scripts/kairos_smoke.py SOLUSDT --model jev  # a specific model: kairos | jev | ordecide | llm
"""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import aiohttp  # noqa: E402

from ctb.config import load_settings  # noqa: E402
from ctb.features import build_state  # noqa: E402
from ctb.kairos.client import KairosError, make_client  # noqa: E402
from ctb.models import get as get_model  # noqa: E402
from ctb.kairos.parse import validate  # noqa: E402
from ctb.kairos.questions import build_questions  # noqa: E402
from ctb.market.bybit_public import BybitPublicFeed  # noqa: E402
from ctb.market.store import MarketStore  # noqa: E402
from ctb.policy import decide  # noqa: E402


async def main(symbol: str, provider: str | None) -> None:
    s = load_settings()
    spec = get_model(provider or s.model_provider)
    if not spec.api_key:
        sys.exit(f"{spec.key_env} is not set (add it to .env or under KEYS). Create a key at {spec.key_url}")
    syms = sorted({symbol, "BTCUSDT"})
    s.symbols = syms
    async with aiohttp.ClientSession() as session:
        store = MarketStore(syms)
        await BybitPublicFeed(store, session).warmup()
        state, meta = build_state(store, symbol, s)
        questions = build_questions(meta["sl_bp"], meta["tp_bp"], meta["horizon_min"])
        client = make_client(spec, session, timeout_s=s.kairos_timeout_s,
                             base_url=s.kairos_base_url if spec.provider == "kairos" else None)
        try:
            res = await client.ask(state, questions)
        except KairosError as exc:
            sys.exit(f"{spec.label} request failed: {exc} (status={exc.status}, request_id={exc.request_id})")
        print(f"model {spec.label} ({client.model})")
        parsed, issues = validate(res.answers, questions)
        print(f"latency {res.latency_ms:.0f} ms, attempts {res.attempts}, {res.request_bytes} bytes, usage {res.usage}, meta {res.metadata}")
        print("contract issues:", issues or "none")
        print(json.dumps(res.answers, indent=1))
        print("policy:", decide(parsed, meta, s, has_position=False).as_dict())


args = [a for a in sys.argv[1:] if not a.startswith("--")]
model = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else None
asyncio.run(main(args[0].upper() if args and args[0] != model else "BTCUSDT", model))

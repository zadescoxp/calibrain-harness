"""OpenRouter support: the Decisions API spec and the chat-LLM adapter. No network: a local aiohttp
server stands in for OpenRouter."""

from __future__ import annotations

import asyncio
import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from ctb.kairos.chat import ChatDecisionClient, build_body, build_schema, parse_reply, to_answers
from ctb.kairos.client import KairosError, SystemOneClient, make_client
from ctb.kairos.parse import validate
from ctb.kairos.questions import build_questions
from ctb.models import MODELS

QS = build_questions(60, 90, 120)


def _llm_reply(questions):
    """What a well-behaved LLM returns: uniform numbers per option."""
    out = {}
    for qid, q in questions.items():
        props = build_schema({qid: q})["properties"][qid]["required"]
        out[qid] = {"p_true": 0.4} if q["type"] == "noul" else {o: 1 / len(props) for o in props}
    return out


def test_specs_point_at_the_right_endpoints():
    assert MODELS["ordecide"].kind == "systemone"
    assert MODELS["ordecide"].base_url + MODELS["ordecide"].path == "https://openrouter.ai/api/alpha/decisions"
    assert MODELS["llm"].kind == "chat"
    assert MODELS["llm"].base_url + MODELS["llm"].path == "https://openrouter.ai/api/v1/chat/completions"
    assert MODELS["llm"].default_model == "deepseek/deepseek-v4.1-flash-1017"
    assert MODELS["kairos"].path == "/v1/systemone"


def test_schema_has_one_number_per_option_and_is_strict():
    schema = build_schema(QS)
    assert schema["required"] == list(QS)
    assert schema["properties"]["direction"]["required"] == ["p_true"]
    assert schema["properties"]["long_bracket"]["required"] == ["target_first", "stop_first", "neither"]
    assert schema["properties"]["return_distribution"]["required"] == [f"bin_{i}" for i in range(7)]
    assert schema["properties"]["setup_quality"]["required"] == [f"level_{i}" for i in range(5)]
    assert all(p["additionalProperties"] is False for p in schema["properties"].values())


def test_body_disables_reasoning_by_default_and_requires_schema_support(monkeypatch):
    monkeypatch.delenv("CTB_LLM_REASONING", raising=False)
    body = build_body("m", {"last_price": 1}, QS)
    assert body["reasoning"] == {"enabled": False}
    assert body["provider"] == {"require_parameters": True}
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["temperature"] == 0
    assert "last_price" in body["messages"][1]["content"]
    monkeypatch.setenv("CTB_LLM_REASONING", "low")
    assert build_body("m", {}, QS)["reasoning"] == {"effort": "low"}
    monkeypatch.setenv("CTB_LLM_REASONING", "turbo")
    with pytest.raises(KairosError):
        build_body("m", {}, QS)


def test_converted_answers_pass_the_strict_validator():
    answers, repairs = to_answers(_llm_reply(QS), QS)
    parsed, issues = validate(answers, QS)
    assert issues == []
    assert repairs == []
    assert set(parsed) == set(QS)
    assert parsed["direction"]["p"] == pytest.approx(0.4)
    assert parsed["setup_quality"]["score"] == pytest.approx(2.0)


def test_repairs_are_applied_and_recorded():
    reply = _llm_reply(QS)
    reply["long_bracket"] = {"target_first": 30, "stop_first": 50, "neither": 20}  # percentages
    reply["direction"] = {"p_true": 65}
    reply["short_bracket"] = {"target_first": 0.5, "stop_first": 0.5, "neither": -0.1}
    answers, repairs = to_answers(reply, QS)
    _, issues = validate(answers, QS)
    assert issues == []
    assert answers["long_bracket"]["choice"] == "stop_first"
    assert answers["long_bracket"]["probabilities"]["stop_first"] == pytest.approx(0.5)
    assert answers["direction"]["noul"] == pytest.approx(0.65)
    assert answers["short_bracket"]["choice"] == "target_first"  # tie -> first option
    assert len(repairs) == 3  # short_bracket sums to 1 once the negative is zeroed


def test_missing_question_is_reported_not_invented():
    reply = _llm_reply(QS)
    del reply["big_move"]
    answers, _ = to_answers(reply, QS)
    _, issues = validate(answers, QS)
    assert issues == ["big_move: missing answer"]


def test_parse_reply_handles_fences_truncation_and_refusals():
    ok = {"choices": [{"message": {"content": "```json\n{\"a\": {\"p_true\": 0.2}}\n```"}, "finish_reason": "stop"}]}
    assert parse_reply(ok) == {"a": {"p_true": 0.2}}
    with pytest.raises(KairosError, match="max_tokens"):
        parse_reply({"choices": [{"message": {"content": "{\"a\""}, "finish_reason": "length"}]})
    with pytest.raises(KairosError) as e:
        parse_reply({"choices": [{"message": {"refusal": "no"}}]})
    assert e.value.kind == "refusal"
    with pytest.raises(KairosError):
        parse_reply({"choices": [{"message": {"content": "sure! here you go"}}]})


def _run(coro):
    return asyncio.run(coro)


def test_clients_hit_openrouter_paths_end_to_end(monkeypatch):
    seen: dict[str, dict] = {}

    async def chat(req: web.Request) -> web.Response:
        body = await req.json()
        seen["chat"] = {"auth": req.headers.get("Authorization"), "body": body}
        qs = {k: {"type": "score" if "level_0" in v["required"] else ("noul" if v["required"] == ["p_true"] else "choice"),
                  "criteria": v["required"] if "level_0" in v["required"] else {o: "" for o in v["required"]}}
              for k, v in body["response_format"]["json_schema"]["schema"]["properties"].items()}
        content = json.dumps(_llm_reply(qs))
        return web.json_response({"id": "gen-1", "model": body["model"], "provider": "DeepSeek",
                                  "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                  "usage": {"prompt_tokens": 3000, "completion_tokens": 300, "cost": 0.0003}})

    async def decide(req: web.Request) -> web.Response:
        body = await req.json()
        seen["decide"] = body
        return web.json_response({"id": "d-1", "model": "typesafe/jev-1.14-x", "provider": "TypeSafe",
                                  "answers": {"is_check": {"type": "noul", "noul": 0.97}}, "usage": {"input_tokens": 10, "output_tokens": 0}})

    app = web.Application()
    app.router.add_post("/api/v1/chat/completions", chat)
    app.router.add_post("/api/alpha/decisions", decide)

    async def go():
        async with TestServer(app) as srv, aiohttp.ClientSession() as session:
            base = str(srv.make_url("/api")).rstrip("/")
            monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
            llm = make_client(MODELS["llm"], session, base_url=base)
            dec = make_client(MODELS["ordecide"], session, base_url=base)
            assert isinstance(llm, ChatDecisionClient) and type(dec) is SystemOneClient
            r1 = await llm.ask({"last_price": 100}, QS)
            r2 = await dec.ask({"message": "x"}, {"is_check": {"type": "noul", "instructions": "?"}})
            return r1, r2

    r1, r2 = _run(go())
    assert seen["chat"]["auth"] == "Bearer sk-test"
    assert seen["chat"]["body"]["model"] == "deepseek/deepseek-v4.1-flash-1017"
    _, issues = validate(r1.answers, QS)
    assert issues == []
    assert r1.metadata["provider"] == "DeepSeek" and r1.usage["cost"] == 0.0003
    assert seen["decide"]["model"] == "google/gemini-decision-1"
    assert r2.answers["is_check"]["noul"] == 0.97 and r2.metadata["provider"] == "TypeSafe"


def test_or_prefix_gives_each_openrouter_model_its_own_provider_id():
    from ctb.models import get, listed
    spec = get("or:google/gemini-decision-1")
    assert spec.provider == "or:google/gemini-decision-1"
    assert spec.model == "google/gemini-decision-1"
    assert spec.label == "gemini-decision-1"
    assert spec.base_url + spec.path == "https://openrouter.ai/api/alpha/decisions"
    assert get("or:perplexity/pplx-decider-v1.3:free").label == "pplx-decider-v1.3"
    for bad in ("or:", "or:noslash", "or:a/b c", "or:../x", "openrouter"):
        with pytest.raises(ValueError):
            get(bad)
    assert "or:liquid/d1.1" in listed(["or:liquid/d1.1", "kairos", "or:bad"])
    assert "or:bad" not in listed(["or:bad"])

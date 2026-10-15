import asyncio
import json
from types import SimpleNamespace

import pytest

from config import Settings
from core.llm import JSON_RETRY, Usage, complete_json, load_prompt, parse_json


@pytest.mark.parametrize("text", [
    '[{"story_id": 1}]',
    '```json\n[{"story_id": 1}]\n```',
    '```\n[{"story_id": 1}]\n```',
    '  ```json[{"story_id": 1}]```  ',
])
def test_parse_json_strips_code_fences(text):
    assert parse_json(text) == [{"story_id": 1}]


def test_parse_json_keeps_backticks_inside_strings():
    assert parse_json('{"try": "run `uv sync`"}') == {"try": "run `uv sync`"}


@pytest.mark.parametrize("text", ["", "Sure! Here is the JSON:", '[{"story_id": 1},]', "```json\n```"])
def test_parse_json_rejects_invalid(text):
    with pytest.raises(ValueError):
        parse_json(text)


class FakeClient:
    """responses.create returns the given outputs in turn and records the inputs it got."""

    def __init__(self, *outputs: str):
        self.outputs = list(outputs)
        self.inputs: list[str] = []
        self.responses = SimpleNamespace(create=self._create)

    async def _create(self, model, instructions, input):
        self.inputs.append(input)
        return SimpleNamespace(output_text=self.outputs.pop(0),
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=100))


def settings() -> Settings:
    return Settings(tg_api_id=1, tg_api_hash="x", database_url="postgresql://x",
                    llm_prices={"m": (2.0, 10.0)})


def test_complete_json_first_answer_valid():
    client, usage = FakeClient('```json\n{"a": 1}\n```'), Usage()
    assert asyncio.run(complete_json(client, settings(), "m", "sys", "user", usage)) == {"a": 1}
    assert usage.calls == 1 and client.inputs == ["user"]


def test_complete_json_retries_once_with_valid_json_only():
    client, usage = FakeClient("Here you go: {a: 1}", '{"a": 1}'), Usage()
    assert asyncio.run(complete_json(client, settings(), "m", "sys", "user", usage)) == {"a": 1}
    assert usage.calls == 2 and client.inputs[1].endswith(JSON_RETRY)
    assert usage.usd == pytest.approx(2 * (1000 * 2.0 + 100 * 10.0) / 1_000_000)


def test_complete_json_gives_up_after_one_retry():
    client = FakeClient("nope", "still nope", '{"a": 1}')
    with pytest.raises(json.JSONDecodeError):
        asyncio.run(complete_json(client, settings(), "m", "sys", "user", Usage()))
    assert len(client.inputs) == 2


@pytest.mark.parametrize("name,placeholders", [
    ("enrich", set()),
    ("score", {"profile_text"}),
    ("article_ru", {"n_sources", "source_kinds", "category", "tags", "sources"}),
    ("overview_ru", {"date", "stories"}),
    ("weekly_ru", {"week", "stories"}),
])
def test_step6_prompts_load_with_their_placeholders(name, placeholders):
    import re
    p = load_prompt(name)
    found = set(re.findall(r"\{(\w+)\}", p.system + p.user))
    assert found == placeholders

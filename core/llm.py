"""Prompts from prompts/*.md and LLM calls with token and cost accounting."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from openai import AsyncOpenAI

from config import Settings

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


@dataclass
class Prompt:
    system: str
    user: str


def load_prompt(name: str) -> Prompt:
    """A prompt file is `SYSTEM\\n…\\nUSER\\n…`, exactly as in the spec."""
    text = (PROMPTS / f"{name}.md").read_text()
    m = re.match(r"\s*SYSTEM\n(.*?)\nUSER\n(.*)", text, re.S)
    if not m:
        raise ValueError(f"prompts/{name}.md must have SYSTEM and USER sections")
    return Prompt(m.group(1).strip(), m.group(2).strip())


def render(template: str, **values: object) -> str:
    """Fills {name} placeholders only; other braces (JSON examples in prompts) stay as they are."""
    missing = set(re.findall(r"\{(\w+)\}", template)) - set(values)
    if missing:
        raise KeyError(f"no values for placeholders: {sorted(missing)}")
    return re.sub(r"\{(\w+)\}", lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), template)


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    by_model: dict = field(default_factory=dict)

    def as_stats(self) -> dict:
        return {"calls": self.calls, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
                "usd": round(self.usd, 4), "by_model": self.by_model}


def price(s: Settings, model: str) -> tuple[float, float]:
    """USD per 1M input / output tokens; unknown models count as 0 and show up in runs.stats by name."""
    return s.llm_prices.get(model, (0.0, 0.0))


async def complete(client: AsyncOpenAI, s: Settings, model: str, system: str, user: str, usage: Usage) -> str:
    r = await client.responses.create(model=model, instructions=system, input=user)
    p_in, p_out = price(s, model)
    usage.calls += 1
    usage.input_tokens += r.usage.input_tokens
    usage.output_tokens += r.usage.output_tokens
    usage.usd += (r.usage.input_tokens * p_in + r.usage.output_tokens * p_out) / 1_000_000
    usage.by_model[model] = usage.by_model.get(model, 0) + 1
    return r.output_text.strip()


JSON_RETRY = "Return valid JSON only."
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.S)


def parse_json(text: str):
    """A model's JSON answer; a ```json fence around it is cut off. Invalid JSON raises ValueError."""
    t = text.strip()
    if m := _FENCE.match(t):
        t = m.group(1)
    return json.loads(t)


async def complete_json(client: AsyncOpenAI, s: Settings, model: str, system: str, user: str, usage: Usage):
    """complete() parsed as JSON; on invalid JSON one more call with «Return valid JSON only», then it raises."""
    try:
        return parse_json(await complete(client, s, model, system, user, usage))
    except ValueError:
        return parse_json(await complete(client, s, model, system, f"{user}\n\n{JSON_RETRY}", usage))

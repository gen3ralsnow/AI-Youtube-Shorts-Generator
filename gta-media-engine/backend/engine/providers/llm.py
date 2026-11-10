"""Provider-independent language model interface.

Providers: Anthropic (Messages API), OpenAI (Chat Completions), and ``offline``.
``offline`` means *no model*: agents fall back to deterministic, source-bound
logic and mark their output ``generator=rule-based`` so it is never mistaken
for model output.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import costs
from ..config import get_settings
from .http import NotConfigured, ProviderError, request


@dataclass
class LLMResult:
    data: dict
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    raw_text: str = field(default="", repr=False)


class LLMProvider:
    name = "base"
    model = ""

    @property
    def available(self) -> bool:
        return False

    def complete_json(self, system: str, prompt: str, max_tokens: int = 2000) -> LLMResult:
        raise NotConfigured(f"LLM provider '{self.name}' is not configured")


_JSON_BLOCK = re.compile(r"\{.*\}", re.S)


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = _JSON_BLOCK.search(text)
        if not match:
            raise ProviderError("Model did not return JSON") from None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Model returned malformed JSON: {exc}") from exc


class AnthropicLLM(LLMProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete_json(self, system: str, prompt: str, max_tokens: int = 2000) -> LLMResult:
        if not self.api_key:
            raise NotConfigured("ANTHROPIC_API_KEY is not set")
        resp = request(
            "POST",
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "system": system + "\nRespond with a single JSON object and nothing else.",
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        body = resp.json()
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        usage = body.get("usage", {})
        return LLMResult(
            data=parse_json(text),
            provider=self.name,
            model=body.get("model", self.model),
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            raw_text=text,
        )


class OpenAILLM(LLMProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        self.api_key, self.model = api_key, model

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete_json(self, system: str, prompt: str, max_tokens: int = 2000) -> LLMResult:
        if not self.api_key:
            raise NotConfigured("OPENAI_API_KEY is not set")
        resp = request(
            "POST",
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={
                "model": self.model,
                "max_tokens": max_tokens,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
            },
            timeout=120,
        )
        body = resp.json()
        text = body["choices"][0]["message"]["content"] or ""
        usage = body.get("usage", {})
        return LLMResult(
            data=parse_json(text),
            provider=self.name,
            model=body.get("model", self.model),
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            raw_text=text,
        )


class OfflineLLM(LLMProvider):
    name = "offline"


def get_llm() -> LLMProvider:
    s = get_settings()
    if s.llm_provider == "anthropic":
        return AnthropicLLM(s.anthropic_api_key, s.anthropic_model)
    if s.llm_provider == "openai":
        return OpenAILLM(s.openai_api_key, s.openai_model)
    return OfflineLLM()


def metered_complete(
    session: Session,
    llm: LLMProvider,
    system: str,
    prompt: str,
    *,
    max_tokens: int = 2000,
    job_id: int | None = None,
    purpose: str = "llm",
) -> LLMResult:
    """Budget-checked model call that records estimated and actual cost."""
    est = costs.estimate_llm(costs.rough_tokens(system + prompt), max_tokens)
    costs.check_budget(session, est, job_id)
    result = llm.complete_json(system, prompt, max_tokens)
    actual = costs.estimate_llm(result.input_tokens, result.output_tokens)
    costs.record(
        session,
        category="language_model",
        provider=f"{result.provider}:{result.model}",
        units=result.input_tokens + result.output_tokens,
        unit="tokens",
        estimated_usd=est,
        actual_usd=actual,
        job_id=job_id,
        note=purpose,
    )
    return result

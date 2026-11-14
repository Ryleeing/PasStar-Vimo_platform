"""Vendor-neutral LLM access through OpenRouter (OpenAI-compatible chat completions).

Configuration (environment variables):
  OPENROUTER_API_KEY     required to call a model; without it every call returns None and
                         callers fall back to their rule-based path
  THE_PASS_LLM_TIER      "test" (default, cheap) or "demo" (stronger, for the real demo run)
  THE_PASS_LLM_MODEL     any OpenRouter model ID; overrides the tier preset

Only structured output is used: the model must return JSON matching a Pydantic schema,
and anything that fails validation is treated as no answer.
"""
from __future__ import annotations

import copy
import logging
import os
from typing import Optional, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
PRESETS = {
    "test": "google/gemini-2.5-flash-lite",   # cheap, for everyday development and test runs
    "demo": "google/gemini-2.5-flash",        # better quality, still fast enough for a live demo
}
T = TypeVar("T", bound=BaseModel)


def resolve_model(tier: Optional[str] = None, model: Optional[str] = None) -> str:
    if model or os.environ.get("THE_PASS_LLM_MODEL"):
        return model or os.environ["THE_PASS_LLM_MODEL"]
    tier = tier or os.environ.get("THE_PASS_LLM_TIER", "test")
    if tier not in PRESETS:
        raise ValueError(f"Unknown LLM tier {tier!r}; use one of {sorted(PRESETS)}")
    return PRESETS[tier]


def strict_schema(model: type[BaseModel]) -> dict:
    """Pydantic JSON schema made acceptable for strict structured output:
    every object closes additionalProperties and lists all its properties as required."""
    schema = copy.deepcopy(model.model_json_schema())

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(schema)
    return schema


class LlmClient:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None, tier: Optional[str] = None,
                 http: Optional[httpx.Client] = None, timeout_s: float = 20.0) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
        self.model = resolve_model(tier, model)
        self.http = http or httpx.Client(timeout=timeout_s)

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def structured(self, system: str, user: str, schema: type[T], name: str) -> Optional[T]:
        """One chat call constrained to `schema`. Returns None on any failure; never raises."""
        if not self.enabled:
            return None
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema",
                                "json_schema": {"name": name, "strict": True, "schema": strict_schema(schema)}},
            "provider": {"require_parameters": True},   # only route to providers that honour the schema
            "temperature": 0,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Title": "The Pass"}
        try:
            r = self.http.post(URL, json=body, headers=headers)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return schema.model_validate_json(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as e:
            log.warning("LLM call %s on %s failed, falling back: %s", name, self.model, e)
            return None

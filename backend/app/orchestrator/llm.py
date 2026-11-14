"""Vendor-neutral LLM access through OpenRouter (OpenAI-compatible chat completions).

Each task is routed to a model class:
  strong   tasks that turn a person's words into data the system acts on (parsing a purchase
           request, drafting a protocol); a mistake here becomes a wrong order
  light    everything else (wording explanations); cheap and fast

Configuration (environment variables):
  OPENROUTER_API_KEY     required to call a model; without it every call returns None and
                         callers fall back to their rule-based path
  THE_PASS_LLM_STRONG    model for strong tasks (default anthropic/claude-opus-5.5)
  THE_PASS_LLM_LIGHT     model for light tasks (default google/gemini-2.5-flash-lite)
  THE_PASS_LLM_MODEL     one model for every task, e.g. to compare models in tests/runner.py

Only structured output is used: the model must return JSON matching a Pydantic schema,
and anything that fails validation is treated as no answer.
"""
from __future__ import annotations

import copy
import logging
import os
from dataclasses import dataclass, field
from typing import Literal, Optional, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

URL = "https://openrouter.ai/api/v1/chat/completions"
ModelClass = Literal["strong", "light"]
TASKS: dict[str, ModelClass] = {
    "parse": "strong",
    "draft_protocol": "strong",
    "explain": "light",
}


@dataclass(frozen=True)
class Preset:
    model: str
    # Only parameters this model accepts: OpenRouter is asked to honour every parameter sent, so an
    # unsupported one (e.g. temperature on Claude Opus 5.5) makes the call fail.
    params: dict = field(default_factory=dict)
    timeout_s: float = 20.0


PRESETS: dict[ModelClass, Preset] = {
    "strong": Preset("anthropic/claude-opus-5.5", {"reasoning": {"effort": "low"}}, timeout_s=60.0),
    "light": Preset("google/gemini-2.5-flash-lite", {"temperature": 0}),
}
T = TypeVar("T", bound=BaseModel)


def resolve(task: str, model: Optional[str] = None) -> Preset:
    """The preset for a task. A model chosen by argument or environment gets no extra parameters."""
    cls = TASKS[task]
    override = model or os.environ.get("THE_PASS_LLM_MODEL") or os.environ.get(f"THE_PASS_LLM_{cls.upper()}")
    if override and override != PRESETS[cls].model:
        return Preset(override, {}, timeout_s=60.0)
    return PRESETS[cls]


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
    def __init__(self, task: str = "parse", api_key: Optional[str] = None, model: Optional[str] = None,
                 http: Optional[httpx.Client] = None) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("OPENROUTER_API_KEY", "")
        self.preset = resolve(task, model)
        self.model = self.preset.model
        self.http = http or httpx.Client(timeout=self.preset.timeout_s)

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
            **self.preset.params,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "X-Title": "PasStar"}
        try:
            r = self.http.post(URL, json=body, headers=headers)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
            return schema.model_validate_json(content)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError, ValidationError) as e:
            detail = e.response.text[:300] if isinstance(e, httpx.HTTPStatusError) else ""
            log.warning("LLM call %s on %s failed, falling back to rules: %s %s", name, self.model, e, detail)
            return None

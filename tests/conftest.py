"""Unit tests never call a real LLM, even when OPENROUTER_API_KEY is set in the shell.
Real-model evaluation is what tests/runner.py is for."""
import pytest

from app.orchestrator import explain_api, parser_api
from app.orchestrator.llm import LlmClient


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setattr(parser_api, "llm", LlmClient(task="parse", api_key="", model="offline"))
    monkeypatch.setattr(explain_api, "llm", LlmClient(task="explain", api_key="", model="offline"))

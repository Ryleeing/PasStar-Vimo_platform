"""Shared test setup.

- Unit tests never call a real LLM, even when OPENROUTER_API_KEY is set in the shell.
  Real-model evaluation is what tests/runner.py is for.
- Every test gets a fresh database with the BE1 and BE3 demo seed data, so HTTP tests run the real routes."""
import pytest

from app import db, deps
from app.channel import seed
from app.orchestrator import explain_api, parser_api, seed as be3_seed
from app.orchestrator.llm import LlmClient


@pytest.fixture(autouse=True)
def no_real_llm(monkeypatch):
    monkeypatch.setattr(parser_api, "llm", LlmClient(task="parse", api_key="", model="offline"))
    monkeypatch.setattr(explain_api, "llm", LlmClient(task="explain", api_key="", model="offline"))


@pytest.fixture(autouse=True)
def restore_deps(monkeypatch):
    """Some tests swap app.deps implementations by plain assignment; put the originals back afterwards."""
    for name in ("valio", "disclosure", "parser"):
        monkeypatch.setattr(deps, name, getattr(deps, name))


@pytest.fixture(autouse=True)
def fresh_database(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "the_pass.db")
    db.init()
    seed.reset()
    be3_seed.reset()

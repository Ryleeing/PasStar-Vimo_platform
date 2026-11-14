"""Shared test setup.

- Unit tests never call a real LLM, even when OPENROUTER_API_KEY is set in the shell.
  Real-model evaluation is what tests/runner.py is for.
- Every test gets a fresh database with the BE1 and BE3 demo seed data, so HTTP tests run the real routes.
- The pipeline runs on BE3's BE2 stubs, never the product catalogue file or the Valio API."""
import os

os.environ["THE_PASS_BE2"] = "stub"     # before the app is imported: orchestrator/be2.py reads it once

import pytest  # noqa: E402

from app import db, deps  # noqa: E402
from app.channel import seed  # noqa: E402
from app.orchestrator import explain_api, parser_api, seed as be3_seed  # noqa: E402
from app.orchestrator.llm import LlmClient  # noqa: E402


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
    # BE2's runtime database (Valio probe budgets): never the real backend/data/runtime.sqlite
    from app.valio import client as valio_client
    monkeypatch.setattr(valio_client, "DEFAULT_RUNTIME_DATABASE", tmp_path / "runtime.sqlite")
    db.init()
    seed.reset(onboarding=False)
    be3_seed.reset()

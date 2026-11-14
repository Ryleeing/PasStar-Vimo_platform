"""BE3 orchestration & AI. See sections 2 and 6 of the design doc.

Other modules get BE3's interface implementations from here:
    from app.orchestrator import parser_api, explain_api
Both are real (LLM via OpenRouter, rule / template fallback without a key). The routes run the pipeline
on the database through orchestrator.service. BE1 reaches the parser through app.deps, registered below."""
from .. import deps
from .explain import LlmExplain
from .parser import Parser
from .stubs import StubDisclosure, StubValio

parser_api = Parser()
explain_api = LlmExplain()

deps.parser = parser_api

# BE2 owns price/stock and the disclosure filter. Until BE2 registers its own, BE3's stubs fill the slots so the
# whole flow runs end to end; BE2's registration (at import, or later) replaces them.
for _name, _stub in (("valio", StubValio), ("disclosure", StubDisclosure)):
    if not hasattr(deps, _name):
        setattr(deps, _name, _stub())

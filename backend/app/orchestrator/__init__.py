"""BE3 orchestration & AI. See sections 2 and 6 of the design doc.

Other modules get BE3's interface implementations from here:
    from app.orchestrator import parser_api, explain_api
Both are real (LLM via OpenRouter, rule / template fallback without a key). The routes run the pipeline
on the database through orchestrator.service. BE1 reaches the parser through app.deps, registered below."""
from .. import deps
from .be2 import get as get_be2
from .explain import LlmExplain
from .parser import Parser
from .stubs import StubDisclosure

parser_api = Parser()
explain_api = LlmExplain()

deps.parser = parser_api

# Price and stock: BE2's Valio client when the catalogue and the key are present, else BE3's stub (be2.py).
# Disclosure: a pass-through for now. BE2's DisclosureFilter expects payloads wrapped by kind
# ({"order": ...}); BE1 hands it bare responses, which it would strip. Swap it in once that is agreed.
if not hasattr(deps, "valio"):
    deps.valio = get_be2().valio
if not hasattr(deps, "disclosure"):
    deps.disclosure = StubDisclosure()

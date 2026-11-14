"""BE3 orchestration & AI. See sections 2 and 6 of the design doc.

Other modules get BE3's interface implementations from here:
    from app.orchestrator import parser_api, pipeline_api, explain_api
parser_api is real (LLM via OpenRouter, rule-based fallback without a key).
pipeline_api and explain_api are still fakes until the database is wired in."""
from .fakes import FakeExplain, FakePipeline
from .parser import Parser

parser_api = Parser()
pipeline_api = FakePipeline()
explain_api = FakeExplain()

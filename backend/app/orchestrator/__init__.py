"""BE3 orchestration & AI. See sections 2 and 6 of the design doc.

Other modules get BE3's interface implementations from here:
    from app.orchestrator import parser_api, pipeline_api, explain_api
parser_api and explain_api are real (LLM via OpenRouter, rule / template fallback without a key).
pipeline_api is still a fake until the database is wired in."""
from .explain import LlmExplain
from .fakes import FakePipeline
from .parser import Parser
from .stubs import StubAudit

parser_api = Parser()
pipeline_api = FakePipeline()
explain_api = LlmExplain()
# Stand-in for BE1's AuditApi so BE3's hold events can be written and checked now; swap when it lands.
audit_api = StubAudit()

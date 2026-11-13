"""BE3 orchestration & AI. See sections 2 and 6 of the design doc.

Other modules get BE3's interface implementations from here:
    from app.orchestrator import parser_api, pipeline_api, explain_api
These are fakes for now; swap the exports below when the real ones are ready."""
from .fakes import FakeExplain, FakeParser, FakePipeline

parser_api = FakeParser()
pipeline_api = FakePipeline()
explain_api = FakeExplain()

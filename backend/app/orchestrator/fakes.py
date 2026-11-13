"""BE3 internal interfaces: M1 fake implementations.

Signatures match ParserApi / PipelineApi / ExplainApi in internal.py exactly.
Other modules wire against these now; once the real implementations land,
only the exports in orchestrator/__init__.py change.
"""
from __future__ import annotations

from typing import Optional

from .. import fixtures as fx
from ..internal import DisclosablePack
from ..schemas import Profile, ProtocolBody, RequestResult, StructuredRequest


class FakeParser:
    def parse(self, text: str, profile: Optional[Profile]) -> tuple[StructuredRequest, list[dict], list[str]]:
        """Return (structured request, ambiguities, applied profile items)."""
        ambiguities = [a.model_dump() for a in fx.PARSE_RESULT.ambiguities]
        applied = list(fx.PARSE_RESULT.profile_applied) if profile else []
        return fx.STRUCTURED.model_copy(deep=True), ambiguities, applied

    def draft_protocol(self, buyer_id: str, agent_id: str, description: str) -> tuple[ProtocolBody, list[dict]]:
        """Return (protocol draft, open questions). buyer_id and agent_id are copied into the draft."""
        body = fx.PROTOCOL_DRAFT.body.model_copy(update={"buyer_id": buyer_id, "agent_id": agent_id}, deep=True)
        questions = [q.model_dump() for q in fx.PROTOCOL_DRAFT.open_questions]
        return body, questions


class FakePipeline:
    """Keeps results in memory instead of the database; every run bumps the attempt."""

    def __init__(self) -> None:
        self._results: dict[str, RequestResult] = {}

    def run(self, request_id: str, from_node: int = 1, constraints: Optional[list[dict]] = None) -> None:
        prev = self._results.get(request_id)
        attempt = prev.attempt + 1 if prev else 1
        self._results[request_id] = fx.REQUEST_RESULT.model_copy(
            update={"request_id": request_id, "attempt": attempt, "chain": fx._chain(attempt)}, deep=True)

    def result(self, request_id: str) -> Optional[RequestResult]:
        """Fake-stage helper for routes; the real version reads requests / chain_nodes / candidates."""
        return self._results.get(request_id)


class FakeExplain:
    def node_summary(self, node: int, pack: DisclosablePack) -> str:
        return fx._chain()[node - 1].summary

    def candidate_explanation(self, pack: DisclosablePack) -> str:
        return fx.CAND_1.explanation

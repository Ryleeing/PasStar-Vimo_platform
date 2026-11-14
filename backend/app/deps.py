"""Implementations that BE1 needs from BE2 and BE3 are registered here (signatures in internal.py).

BE2 and BE3 assign them when their modules load, e.g. `deps.valio = ValioClient()`. BE1 reads them at call time only.
The interfaces BE1 provides need no registration; import the modules under app.channel directly:
protocols (ProtocolsApi), gate (GateApi), checkpoints (CheckpointsApi), quotes (QuotesApi),
orders (OrdersApi), audit (AuditApi).
"""
from .internal import DisclosureApi, ParserApi, ValioApi

valio: ValioApi            # BE2: prices and stock
disclosure: DisclosureApi  # BE2: outbound disclosure filter
parser: ParserApi          # BE3: draft_protocol

"""Classifying protocol changes: tightened (takes effect immediately) / expanded (needs the approver to re-sign) / forbidden (refused).
BE1's /v1/protocols/{id}/amend calls classify_changes() directly. Rules: see "Protocol rule contents" in the technical design document."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..schemas import FieldChange, ProtocolBody, Requirement

# Order of permission levels: further right is looser
_POLICY_ORDER = {"FORBIDDEN": 0, "APPROVAL": 1, "AUTO": 2}
_UNKNOWN_ORDER = {"REJECT": 0, "REQUIRE_APPROVAL": 1}
_MAY_CONTAIN_ORDER = {"EXCLUDE": 0, "REQUIRE_APPROVAL": 1}
MAX_VALIDITY = timedelta(days=365)
IMMUTABLE = {"buyer_id", "agent_id"}


def _req_key(r: Any) -> tuple:
    r = r if isinstance(r, Requirement) else Requirement.model_validate(r)
    return (r.kind, r.code)


def classify_changes(current: ProtocolBody, changes: dict[str, Any], actor: str, today: date) -> dict[str, list[FieldChange]]:
    """actor: "buyer" or "valio". Returns {"tightened": [...], "expanded": [...], "forbidden": [...]}.
    changes is a subset of ProtocolBody fields; nested fields are passed as whole objects, e.g. {"substitution": {...}}."""
    out: dict[str, list[FieldChange]] = {"tightened": [], "expanded": [], "forbidden": []}
    cur = current.model_dump(mode="json")

    def add(kind: str, field: str, before: Any, after: Any) -> None:
        out[kind].append(FieldChange(field=field, before=before, after=after))

    for field, new in changes.items():
        old = cur.get(field)
        if field not in cur:
            add("forbidden", field, None, new)                       # unknown fields are always refused
        elif field in IMMUTABLE:
            if new != old:
                add("forbidden", field, old, new)                    # only possible with a new protocol
        elif field == "auto_approve_limit_cents":
            limit = changes.get("max_order_cents", cur["max_order_cents"])
            if new > limit:
                add("forbidden", field, old, new)
            elif new < old:
                add("tightened", field, old, new)
            elif new > old:
                add("expanded", field, old, new)
        elif field == "max_order_cents":
            if new < old:
                add("tightened", field, old, new)
            elif new > old:
                add("expanded", field, old, new)
        elif field == "allowed_categories":
            removed, added = set(old) - set(new), set(new) - set(old)
            if removed:
                add("tightened", field, sorted(removed), [])
            if added:
                add("expanded", field, [], sorted(added))
        elif field == "hard_requirements":
            o, n = {_req_key(r) for r in old}, {_req_key(r) for r in new}
            if n - o:
                add("tightened", field, [], [list(k) for k in sorted(n - o)])
            if o - n:
                add("expanded", field, [list(k) for k in sorted(o - n)], [])
        elif field in ("unknown_data_policy", "may_contain_policy"):
            order = _UNKNOWN_ORDER if field == "unknown_data_policy" else _MAY_CONTAIN_ORDER
            if order[new] < order[old]:
                add("tightened", field, old, new)
            elif order[new] > order[old]:
                add("expanded", field, old, new)
        elif field == "substitution":
            for k, v in new.items():
                ov = old[k]
                if _POLICY_ORDER[v] < _POLICY_ORDER[ov]:
                    add("tightened", f"substitution.{k}", ov, v)
                elif _POLICY_ORDER[v] > _POLICY_ORDER[ov]:
                    add("expanded", f"substitution.{k}", ov, v)
        elif field == "valid_until":
            nd, od = date.fromisoformat(str(new)), date.fromisoformat(str(old))
            if nd - today > MAX_VALIDITY:
                add("forbidden", field, old, new)
            elif nd < od:
                add("tightened", field, old, new)
            elif nd > od:
                add("expanded", field, old, new)
        elif field == "approval_mode":
            if old == "SEPARATE" and new == "SELF":
                add("expanded", field, old, new)                     # allowing self-approval: loosening, needs re-signing
            elif old == "SELF" and new == "SEPARATE":
                add("tightened", field, old, new)
        elif field in ("approval_channel", "approver_public_key"):
            if new != old:
                add("expanded", field, old, new)                     # changing approval channel or approver key: both need re-signing
        elif field == "approver":
            if new != old:
                add("expanded", field, old, new)                     # changing the approver needs re-signing
        elif field == "valio_scope":
            for k, v in new.items():
                ov = old[k]
                if v == ov:
                    continue
                looser = (k in ("rate_limit_per_min", "max_probes_per_product_per_day") and v > ov) or \
                         (k == "sensitivity_ceiling" and v > ov) or k in ("prices", "stock")
                if actor != "valio" and looser:
                    add("forbidden", f"valio_scope.{k}", ov, v)      # only Valio can loosen this
                elif looser:
                    add("expanded", f"valio_scope.{k}", ov, v)
                else:
                    add("tightened", f"valio_scope.{k}", ov, v)
    return out


def apply_tightening(current: ProtocolBody, changes: dict[str, Any], result: dict[str, list[FieldChange]]) -> ProtocolBody:
    """Write only the tightened parts into the current protocol (expanded parts go to approval, forbidden parts are dropped).
    Simplification: a top-level field is applied as a whole only when all of its changes are "tightened"."""
    bad = {c.field.split(".")[0] for c in result["expanded"] + result["forbidden"]}
    body = current.model_dump(mode="json")
    for field, new in changes.items():
        if field in body and field not in bad and isinstance(body[field], dict) and isinstance(new, dict):
            body[field] = {**body[field], **new}
        elif field in body and field not in bad:
            body[field] = new
    return ProtocolBody.model_validate(body)

"""协议修改的判定：收紧（直接生效）/ 扩大（需要审批人重新签字）/ 越权（拒绝）。
BE1 的 /v1/protocols/{id}/amend 直接调用 classify_changes()。规则见技术设计文档“协议规则内容”。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..schemas import FieldChange, ProtocolBody, Requirement

# 越权级别的顺序：越往右越宽松
_POLICY_ORDER = {"FORBIDDEN": 0, "APPROVAL": 1, "AUTO": 2}
_UNKNOWN_ORDER = {"REJECT": 0, "REQUIRE_APPROVAL": 1}
_MAY_CONTAIN_ORDER = {"EXCLUDE": 0, "REQUIRE_APPROVAL": 1}
MAX_VALIDITY = timedelta(days=365)
IMMUTABLE = {"buyer_id", "agent_id"}


def _req_key(r: Any) -> tuple:
    r = r if isinstance(r, Requirement) else Requirement.model_validate(r)
    return (r.kind, r.code)


def classify_changes(current: ProtocolBody, changes: dict[str, Any], actor: str, today: date) -> dict[str, list[FieldChange]]:
    """actor: "buyer" 或 "valio"。返回 {"tightened": [...], "expanded": [...], "forbidden": [...]}。
    changes 是 ProtocolBody 的部分字段；嵌套字段用整个对象传，如 {"substitution": {...}}。"""
    out: dict[str, list[FieldChange]] = {"tightened": [], "expanded": [], "forbidden": []}
    cur = current.model_dump(mode="json")

    def add(kind: str, field: str, before: Any, after: Any) -> None:
        out[kind].append(FieldChange(field=field, before=before, after=after))

    for field, new in changes.items():
        old = cur.get(field)
        if field not in cur:
            add("forbidden", field, None, new)                       # 未知字段一律拒绝
        elif field in IMMUTABLE:
            if new != old:
                add("forbidden", field, old, new)                    # 只能新建协议
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
        elif field == "approver":
            if new != old:
                add("expanded", field, old, new)                     # 换审批人要重新签字
        elif field == "valio_scope":
            for k, v in new.items():
                ov = old[k]
                if v == ov:
                    continue
                looser = (k in ("rate_limit_per_min", "max_probes_per_product_per_day") and v > ov) or \
                         (k == "sensitivity_ceiling" and v > ov) or k in ("prices", "stock")
                if actor != "valio" and looser:
                    add("forbidden", f"valio_scope.{k}", ov, v)      # 只有 Valio 能放宽
                elif looser:
                    add("expanded", f"valio_scope.{k}", ov, v)
                else:
                    add("tightened", f"valio_scope.{k}", ov, v)
    return out


def apply_tightening(current: ProtocolBody, changes: dict[str, Any], result: dict[str, list[FieldChange]]) -> ProtocolBody:
    """只把收紧的部分写进当前协议（扩大的部分进审批，越权的部分丢弃）。
    简化做法：一个顶层字段只有在它的全部变化都属于“收紧”时才整体应用。"""
    bad = {c.field.split(".")[0] for c in result["expanded"] + result["forbidden"]}
    body = current.model_dump(mode="json")
    for field, new in changes.items():
        if field in body and field not in bad and isinstance(body[field], dict) and isinstance(new, dict):
            body[field] = {**body[field], **new}
        elif field in body and field not in bad:
            body[field] = new
    return ProtocolBody.model_validate(body)

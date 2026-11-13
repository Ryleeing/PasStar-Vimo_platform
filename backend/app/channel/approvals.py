"""审批人校验（BE1）。/v1/approvals/{id}/decision 在复核报价之前先调用 check_approver()。
规则见技术设计文档“协议规则内容”里的 approval_mode。"""
from __future__ import annotations

from typing import Literal, Optional

ApproverError = Literal["SELF_APPROVAL_FORBIDDEN", "NOT_APPROVER"]


def check_approver(approval_mode: str, approver: str, requested_by: Optional[str], decider: str) -> Optional[ApproverError]:
    """返回 None 表示可以审批，否则返回错误码（接口层转成 403）。
    - SEPARATE：提需求的人不能批自己的单，其余人必须是协议里的 approver；
    - SELF：只要是协议里的 approver 就可以，包括提需求的本人（审计日志记为 self-approved）。"""
    if approval_mode == "SEPARATE" and requested_by is not None and decider == requested_by:
        return "SELF_APPROVAL_FORBIDDEN"
    if decider != approver:
        return "NOT_APPROVER"
    return None


def is_self_approval(requested_by: Optional[str], decider: str) -> bool:
    """写审计日志时用：SELF 模式下本人审批，事件记为 ORDER_SELF_APPROVED。"""
    return requested_by is not None and requested_by == decider

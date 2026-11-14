"""Approver check: in SEPARATE mode the requester cannot approve their own order. HTTP flow tests are in test_channel.py."""
from app.channel.approvals import check_approver


def test_self_mode_owner_may_approve_own_order():
    assert check_approver("SELF", "usr_owner", "usr_owner", "usr_owner") is None


def test_self_mode_still_needs_named_approver():
    assert check_approver("SELF", "usr_owner", "usr_owner", "usr_chef") == "NOT_APPROVER"


def test_separate_mode_requester_blocked_even_if_named_approver():
    # 协议配置错误（审批人就是提需求的人）时，组织模式仍然拦住
    assert check_approver("SEPARATE", "usr_chef", "usr_chef", "usr_chef") == "SELF_APPROVAL_FORBIDDEN"

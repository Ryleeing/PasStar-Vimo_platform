"""审批人校验（M1 假服务器）：SEPARATE 模式下提需求的人不能批自己的单。"""
from fastapi.testclient import TestClient

from app.main import app

c = TestClient(app)
BODY = {"decision": "APPROVE"}


def test_requester_cannot_approve_own_order():
    r = c.post("/v1/approvals/apr_1/decision", json=BODY, headers={"X-Demo-User": "usr_chef"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL_FORBIDDEN"


def test_other_user_is_not_approver():
    r = c.post("/v1/approvals/apr_1/decision", json=BODY, headers={"X-Demo-User": "usr_owner"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "NOT_APPROVER"


def test_manager_can_approve():
    r = c.post("/v1/approvals/apr_1/decision", json=BODY, headers={"X-Demo-User": "usr_manager"})
    assert r.status_code == 200 and r.json()["approval"]["status"] == "APPROVED"


from app.channel.approvals import check_approver


def test_self_mode_owner_may_approve_own_order():
    assert check_approver("SELF", "usr_owner", "usr_owner", "usr_owner") is None


def test_self_mode_still_needs_named_approver():
    assert check_approver("SELF", "usr_owner", "usr_owner", "usr_chef") == "NOT_APPROVER"


def test_separate_mode_requester_blocked_even_if_named_approver():
    # 协议配置错误（审批人就是提需求的人）时，组织模式仍然拦住
    assert check_approver("SEPARATE", "usr_chef", "usr_chef", "usr_chef") == "SELF_APPROVAL_FORBIDDEN"

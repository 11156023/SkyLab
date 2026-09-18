"""建立時記錄登入密碼——只記錄「確定已生效」的那一組。

課程環境與快速練習的機器原本不會存密碼，學生的資源詳情頁因此沒有密碼欄位，
得先按一次重設密碼才看得到。這裡驗證補上之後的規則：生效才存，沒生效就不存，
否則畫面會顯示一組登不進去的密碼，比空白更誤導。
"""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.core.security import decrypt_value
from app.services.proxmox import provisioning_service as svc


@pytest.fixture
def captured(monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(
        svc.resource_repo, "create_resource", lambda **kwargs: calls.append(kwargs)
    )
    monkeypatch.setattr(svc, "plan_provision", lambda **_kwargs: PLAN)
    monkeypatch.setattr(svc, "execute_provision", lambda plan: (101, "pve"))
    return calls


PLAN: dict = {}


def _run(captured, plan: dict) -> dict:
    PLAN.clear()
    PLAN.update(plan)
    request = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        environment_type="快速練習｜n8n",
        os_info="n8n",
        expiry_date=None,
        template_id=None,
    )
    svc.provision_from_request(session=Mock(), db_request=request)
    return captured[-1]


def test_applied_password_is_stored_so_the_student_sees_it_immediately(captured):
    stored = _run(
        captured,
        {
            "password": "hunter2",
            "login_password_applied": True,
            "placement_strategy": "x",
        },
    )

    assert stored["login_password_encrypted"] is not None
    assert decrypt_value(stored["login_password_encrypted"]) == "hunter2"


def test_password_that_never_took_effect_is_not_stored(captured):
    # LXC 範本克隆在未啟動時就屬於這種：密碼產生了，但從未寫進機器
    stored = _run(
        captured,
        {
            "password": "hunter2",
            "login_password_applied": False,
            "placement_strategy": "x",
        },
    )

    assert stored["login_password_encrypted"] is None


def test_missing_flag_defaults_to_not_storing(captured):
    # 新增分支若忘了標記，寧可少存也不要存一組登不進去的密碼
    stored = _run(captured, {"password": "hunter2", "placement_strategy": "x"})

    assert stored["login_password_encrypted"] is None

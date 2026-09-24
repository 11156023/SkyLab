"""對外 port 自動配號：課程環境逐位學生發布時從管理員設定的池子挑號。

模板上不能寫死對外 port（全域唯一），只能在開課時配。這裡只挑號、不寫入，
真正的佔用靠 nat_rule 的唯一約束；同時開課撞號時呼叫端重挑。
"""

from types import SimpleNamespace

import pytest

from app.exceptions import BadRequestError
from app.repositories import nat_rule as nat_repo
from app.services.network import ip_management_service, nat_service


@pytest.fixture
def pool(monkeypatch):
    state = {"config": SimpleNamespace(forward_port_start=30000, forward_port_end=30004,
                                      forward_public_host="lab.example.edu"),
             "taken": set()}
    monkeypatch.setattr(
        ip_management_service, "get_subnet_config", lambda _session: state["config"]
    )
    monkeypatch.setattr(
        nat_repo,
        "taken_external_ports",
        lambda _session, protocol, start, end: {
            p for p in state["taken"] if start <= p <= end
        },
    )
    return state


def test_picks_the_lowest_free_port_in_the_pool(pool):
    pool["taken"] = {30000, 30001}

    assert nat_service.allocate_external_port(object(), "tcp") == 30002


def test_ports_handed_out_in_the_same_round_are_skipped(pool):
    """同一輪還沒寫進 DB 的 port 不能再發給下一台。"""
    assert nat_service.allocate_external_port(
        object(), "tcp", exclude=frozenset({30000, 30001})
    ) == 30002


def test_reserved_ports_are_never_allocated(pool, monkeypatch):
    monkeypatch.setattr(nat_service, "RESERVED_PORTS", frozenset({30000}))

    assert nat_service.allocate_external_port(object(), "tcp") == 30001


def test_an_exhausted_pool_is_reported(pool):
    pool["taken"] = set(range(30000, 30005))

    with pytest.raises(BadRequestError):
        nat_service.allocate_external_port(object(), "tcp")


def test_no_subnet_config_means_no_pool(pool):
    pool["config"] = None

    with pytest.raises(BadRequestError):
        nat_service.allocate_external_port(object(), "tcp")


# ── 管理員設定的驗證 ───────────────────────────────────────────────────────


def _subnet(**overrides):
    from app.schemas.ip_management import SubnetConfigCreate

    values = dict(cidr="10.10.0.0/24", gateway="10.10.0.1", bridge_name="vmbr1",
                  gateway_vm_ip="10.10.0.2")
    values.update(overrides)
    return SubnetConfigCreate(**values)


def test_pool_defaults_are_sane():
    body = _subnet()
    assert (body.forward_port_start, body.forward_port_end) == (30000, 39999)
    assert body.forward_public_host is None


def test_pool_must_stay_above_the_system_ports():
    with pytest.raises(ValueError):
        _subnet(forward_port_start=80, forward_port_end=1000)


def test_pool_start_cannot_exceed_end():
    with pytest.raises(ValueError):
        _subnet(forward_port_start=40000, forward_port_end=30000)


def test_entry_host_is_trimmed_and_blank_means_unset():
    assert _subnet(forward_public_host="  gw.example.edu ").forward_public_host == "gw.example.edu"
    assert _subnet(forward_public_host="   ").forward_public_host is None

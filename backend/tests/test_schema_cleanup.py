"""schemas 整理的回歸測試：搬移／合併後行為不變。"""

import pytest
from pydantic import ValidationError

import app.schemas.firewall
from app.schemas.firewall import (
    PortSpec,
    PublishedServiceCreate,
    PublishedServiceRef,
)
from app.schemas.resource import LXCCreateResponse, VMCreateResponse
from app.schemas.reverse_proxy import ReverseProxyRulePublic


def test_reverse_proxy_rule_public_lives_in_reverse_proxy_schema() -> None:
    assert not hasattr(app.schemas.firewall, "ReverseProxyRulePublic")
    assert ReverseProxyRulePublic.__module__ == "app.schemas.reverse_proxy"


@pytest.mark.parametrize("model", [PortSpec, PublishedServiceRef, PublishedServiceCreate])
def test_protocol_is_normalised_and_pattern_enforced(model: type) -> None:
    assert model(port=80, protocol=" TCP ").protocol == "tcp"
    with pytest.raises(ValidationError):
        model(port=80, protocol="tc p")
    with pytest.raises(ValidationError):
        model(port=80, protocol="x" * 17)


def test_create_responses_share_fields_without_task_id() -> None:
    assert issubclass(VMCreateResponse, LXCCreateResponse)
    assert set(VMCreateResponse.model_fields) == {"vmid", "upid", "message"}
    resp = VMCreateResponse(vmid=101, upid="UPID:x", message="ok")
    assert resp.vmid == 101


def test_openapi_keeps_reverse_proxy_rule_component() -> None:
    from app.main import app

    schemas = app.openapi()["components"]["schemas"]
    assert "ReverseProxyRulePublic" in schemas
    assert set(schemas["ReverseProxyRulePublic"]["properties"]) == {
        "id",
        "vmid",
        "vm_ip",
        "domain",
        "zone_id",
        "internal_port",
        "enable_https",
        "dns_provider",
        "created_at",
    }

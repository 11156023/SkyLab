"""app.schemas 各模組 validator 分支覆蓋測試（resource_settings、ip_management、resource、
course_environment、firewall、push、template、gateway、teaching_class）。"""

from typing import Any

import pytest
from pydantic import ValidationError

from app.schemas.course_environment import (
    MAX_DRAFT_BYTES,
    EnvironmentDraftIn,
    EnvironmentEdgeIn,
    EnvironmentNodeIn,
)
from app.schemas.firewall import PortSpec, PublishedServiceCreate
from app.schemas.gateway import GatewayInstallOptions
from app.schemas.ip_management import SubnetConfigCreate
from app.schemas.push import is_allowed_push_endpoint
from app.schemas.resource import BatchActionRequest, DirectSpecUpdateRequest
from app.schemas.resource_settings import (
    AuthorizedKeyRequest,
    BootOptionsUpdate,
    PasswordResetRequest,
)
from app.schemas.teaching_class import ClassPatch

# --- resource_settings -------------------------------------------------------


def test_boot_options_without_boot_order_keeps_none() -> None:
    body = BootOptionsUpdate(boot_order=None, eject_cdrom=True)
    assert body.boot_order is None


def test_boot_options_dedupes_and_rejects_bad_keys() -> None:
    body = BootOptionsUpdate(boot_order=[" scsi0 ", "net0", "scsi0"])
    assert body.boot_order == ["scsi0", "net0"]
    with pytest.raises(ValidationError, match="invalid boot device key"):
        BootOptionsUpdate(boot_order=["floppy0"])


def test_boot_options_needs_something_and_exclusive_cdrom() -> None:
    with pytest.raises(ValidationError, match="nothing to update"):
        BootOptionsUpdate()
    with pytest.raises(ValidationError, match="mutually exclusive"):
        BootOptionsUpdate(cdrom_iso="local:iso/a.iso", eject_cdrom=True)


def test_password_reset_rejects_whitespace() -> None:
    assert PasswordResetRequest(password="Secret123").password == "Secret123"
    assert PasswordResetRequest(password=None).password is None
    with pytest.raises(ValidationError, match="whitespace"):
        PasswordResetRequest(password="Secret 123")


_KEY = (
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
)


def test_authorized_key_is_normalized() -> None:
    body = AuthorizedKeyRequest(public_key=f"  {_KEY}   user@host  ")
    assert body.public_key == f"{_KEY} user@host"


def test_authorized_key_rejects_multiple_or_invalid_keys() -> None:
    with pytest.raises(ValidationError, match="one public key"):
        AuthorizedKeyRequest(public_key=f"{_KEY}\n{_KEY}")
    with pytest.raises(ValidationError, match="not a valid OpenSSH"):
        AuthorizedKeyRequest(public_key="not-a-key " + "A" * 30)


# --- ip_management -----------------------------------------------------------


def _subnet(**overrides: Any) -> SubnetConfigCreate:
    data: dict[str, Any] = {
        "cidr": "10.0.0.0/24",
        "gateway": "10.0.0.1",
        "bridge_name": "vmbr1",
        "gateway_vm_ip": "10.0.0.2",
    }
    data.update(overrides)
    return SubnetConfigCreate(**data)


def test_subnet_public_host_normalization() -> None:
    assert _subnet(forward_public_host=None).forward_public_host is None
    assert _subnet(forward_public_host="  ").forward_public_host is None
    assert _subnet(forward_public_host=" lab.example.com ").forward_public_host == (
        "lab.example.com"
    )
    with pytest.raises(ValidationError):
        _subnet(forward_public_host="a" * 256)


def test_subnet_rejects_invalid_cidr_and_ip() -> None:
    with pytest.raises(ValidationError):
        _subnet(cidr="not-a-network")
    with pytest.raises(ValidationError):
        _subnet(gateway="999.1.1.1")


def test_subnet_blocks_none_and_invalid() -> None:
    assert _subnet(extra_blocked_subnets=None).extra_blocked_subnets == []
    blocks = _subnet(
        extra_blocked_subnets="10.1.0.0/16\n10.2.0.5, 10.1.0.0/16"
    ).extra_blocked_subnets
    assert blocks == ["10.1.0.0/16", "10.2.0.5"]
    with pytest.raises(ValidationError):
        _subnet(extra_blocked_subnets=["10.1.0.0/40"])


def test_subnet_block_validator_skips_blank_items() -> None:
    # mode="before" 的正規化已經濾掉空白項目；直接呼叫 after validator 才走得到這個防禦分支
    assert SubnetConfigCreate.validate_blocks(["  ", "10.3.0.1"]) == ["10.3.0.1"]


# --- resource ----------------------------------------------------------------


def test_direct_spec_update_requires_a_field() -> None:
    assert DirectSpecUpdateRequest(cores=2).cores == 2
    with pytest.raises(ValidationError, match="At least one"):
        DirectSpecUpdateRequest()


def test_batch_action_validates_action() -> None:
    assert BatchActionRequest(vmids=[100], action="start").action == "start"
    with pytest.raises(ValidationError, match="Invalid action"):
        BatchActionRequest(vmids=[100], action="explode")


# --- course_environment ------------------------------------------------------


def _node(**overrides: Any) -> EnvironmentNodeIn:
    data: dict[str, Any] = {
        "node_key": "web",
        "name": "Web",
        "role": "server",
        "resource_type": "qemu",
        "cpu": 1,
        "memory_mb": 1024,
        "disk_gb": 10,
    }
    data.update(overrides)
    return EnvironmentNodeIn(**data)


def test_node_template_source_requires_template_id() -> None:
    with pytest.raises(ValidationError):
        _node(source_type="template")


def test_node_custom_source_requires_image() -> None:
    with pytest.raises(ValidationError):
        _node(source_type="custom", custom_image_ref="  ")


@pytest.mark.parametrize("ref", ["abc", "0"])
def test_node_custom_qemu_requires_positive_vmid(ref: str) -> None:
    with pytest.raises(ValidationError):
        _node(source_type="custom", custom_image_ref=ref)


def test_edge_rejects_same_node() -> None:
    with pytest.raises(ValidationError):
        EnvironmentEdgeIn(source_node_key="a", target_node_key="a")


def test_draft_size_limit() -> None:
    with pytest.raises(ValidationError, match="256 KiB"):
        EnvironmentDraftIn(configuration={"blob": "x" * MAX_DRAFT_BYTES}, editor={})


# --- firewall ----------------------------------------------------------------


def test_protocol_non_string_passes_through_to_type_check() -> None:
    with pytest.raises(ValidationError):
        PortSpec(port=22, protocol=6)


def test_published_service_domain_non_string_kept() -> None:
    body = PublishedServiceCreate(port=22, domain=None)
    assert body.domain is None
    assert body.mode == "firewall_only"


# --- push --------------------------------------------------------------------


def test_push_endpoint_unparseable_by_sender_is_rejected() -> None:
    # urlsplit 接受、urllib3 卻解析失敗：兩邊結果不一致就不能放行
    assert is_allowed_push_endpoint("https://a%zz.push.apple.com/x") is False
    assert is_allowed_push_endpoint("https://fcm.googleapis.com/fcm/send/abc") is True


# --- template ----------------------------------------------------------------


# --- gateway -----------------------------------------------------------------


def test_gateway_monitoring_sources_skip_blank() -> None:
    options = GatewayInstallOptions(
        monitoring_allow_from=["  ", "10.0.0.5/24", "10.0.0.0/24"]
    )
    assert options.monitoring_allow_from == ["10.0.0.0/24"]


# --- teaching_class ----------------------------------------------------------


def test_class_patch_timezone_none_is_allowed() -> None:
    assert ClassPatch(timezone=None).timezone is None

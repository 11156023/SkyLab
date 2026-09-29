"""schema 修正的回歸測試（純 schema，不需要 DB）。"""

import uuid

import pytest
from pydantic import ValidationError

from app.core.i18n import t
from app.exceptions import BadRequestError
from app.models.ai_api_request import AIAPIRequestStatus
from app.schemas.ai_api import AIAPIRequestCreate, AIAPIRequestReview
from app.schemas.classroom import ClassroomStudent
from app.schemas.ip_management import SubnetConfigCreate
from app.schemas.push import PushSubscriptionCreate
from app.schemas.resource import LXCCreateRequest, VMCreateRequest
from app.schemas.setup import SetupAdminResult
from app.schemas.template import TemplateCloneRequest
from app.schemas.user import UpdatePassword, UserPublic
from app.schemas.vm_request import VMRequestCreate
from app.services.vm import vm_request_availability_service

# ---- 回應 schema 不重驗信箱格式 ----


@pytest.mark.parametrize("email", ["alice@school.local", "jdoe"])
def test_response_schemas_accept_stored_non_rfc_email(email: str) -> None:
    user = UserPublic(
        id=uuid.uuid4(), email=email, is_active=True, role="student", is_superuser=False
    )
    assert user.email == email
    assert ClassroomStudent(user_id=uuid.uuid4(), email=email).email == email
    result = SetupAdminResult(
        email=email, full_name=None, created=True, default_admin_disabled=False
    )
    assert result.email == email


# ---- 背景建機 schema 至少收得和課程環境節點一樣寬 ----


def test_internal_create_schemas_accept_environment_node_bounds() -> None:
    lxc = LXCCreateRequest(
        hostname="lab",
        ostemplate="local:vztmpl/debian.tar.zst",
        cores=64,
        memory=131072,
        rootfs_size=2000,
        password="secret1",
        environment_type="Custom",
    )
    assert (lxc.cores, lxc.memory, lxc.rootfs_size) == (64, 131072, 2000)

    vm = VMCreateRequest(
        hostname="lab",
        template_id=9000,
        username="student",
        password="secret1",
        cores=48,
        memory=256,
        disk_size=8,
        environment_type="Custom",
    )
    assert (vm.cores, vm.memory, vm.disk_size) == (48, 256, 8)

    with pytest.raises(ValidationError):
        VMCreateRequest(
            hostname="lab",
            template_id=9000,
            username="student",
            password="secret1",
            cores=65,
            environment_type="Custom",
        )


def test_vm_request_create_bounds_unchanged() -> None:
    req = VMRequestCreate(
        reason="for the course lab",
        resource_type="qemu",
        hostname="lab",
        cores=64,
        memory=128,
        disk_size=2000,
    )
    assert (req.cores, req.memory, req.disk_size) == (64, 128, 2000)
    with pytest.raises(ValidationError):
        VMRequestCreate(
            reason="for the course lab", resource_type="qemu", hostname="lab", cores=65
        )


# ---- 克隆 hostname 在 schema 就擋掉不合法名稱 ----


@pytest.mark.parametrize(
    "hostname", ["a..b", "lab.", ".lab", "Ubuntu 22.04 Lab", "lab_1", "-lab", "a" * 64]
)
def test_template_clone_rejects_invalid_hostname(hostname: str) -> None:
    with pytest.raises(ValidationError):
        TemplateCloneRequest(hostname=hostname)


@pytest.mark.parametrize("hostname", ["web", "web.lab", "web-01", "開發機", None])
def test_template_clone_accepts_valid_hostname(hostname: str | None) -> None:
    assert TemplateCloneRequest(hostname=hostname).hostname == hostname


# ---- 非標準 IPv4 寫法不能繞過推播 endpoint 的私有位址檢查 ----


def _push(endpoint: str) -> PushSubscriptionCreate:
    return PushSubscriptionCreate(
        endpoint=endpoint, keys={"p256dh": "k", "auth": "a"}
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://127.1/x",
        "https://2130706433/x",
        "https://0x7f.0.0.1/x",
        "https://0x7f.1/x",
        "https://127.0.0.1./x",
        "https://localhost./x",
        "https://[::ffff:10.0.0.5]/x",
        "https://10.0.0.5/x",
        # 公網 IP 字面值也不是推播服務網域，一樣拒絕
        "https://8.8.8.8/x",
    ],
)
def test_push_endpoint_rejects_private_numeric_hosts(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        _push(endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://fcm.googleapis.com/fcm/send/abc",
        "https://updates.push.services.mozilla.com/wpush/v2/abc",
    ],
)
def test_push_endpoint_accepts_public_hosts(endpoint: str) -> None:
    assert _push(endpoint).endpoint == endpoint


# ---- 拒絕 /0～/7 這種不合理的超大子網，/8 照收 ----


def _subnet(cidr: str) -> SubnetConfigCreate:
    return SubnetConfigCreate(
        cidr=cidr, gateway="10.0.0.1", bridge_name="vmbr1", gateway_vm_ip="10.0.0.2"
    )


@pytest.mark.parametrize("cidr", ["0.0.0.0/0", "10.0.0.0/7", "10.0.0.0/32"])
def test_subnet_rejects_unreasonable_prefix(cidr: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        _subnet(cidr)
    message = exc_info.value.errors()[0]["msg"]
    # locale key 必須解析成文字；缺翻譯時 translate() 會原樣回傳 key
    for key in ("cidr_no_slash32", "cidr_prefix_too_short", "invalid_cidr"):
        assert key not in message
    if cidr.endswith("/32"):
        assert t("ip.cidr_no_slash32") in message
    else:
        # 過大的網段要用專屬、已翻譯的訊息，不能夾帶英文片段
        assert t("ip.cidr_prefix_too_short", min_prefix=8) in message
        assert "prefix must be" not in message


@pytest.mark.parametrize("cidr", ["10.0.0.0/8", "172.16.0.0/16", "192.168.1.0/24"])
def test_subnet_accepts_common_prefix(cidr: str) -> None:
    assert _subnet(cidr).cidr == cidr


# ---- AI API 期限與審核結果只收已知值 ----


@pytest.mark.parametrize("duration", ["1h", "1d", "7d", "30d", "never"])
def test_ai_api_request_accepts_known_durations(duration: str) -> None:
    req = AIAPIRequestCreate(purpose="x" * 10, duration=duration)
    assert req.duration == duration


def test_ai_api_request_defaults_to_never() -> None:
    assert AIAPIRequestCreate(purpose="x" * 10).duration == "never"


@pytest.mark.parametrize("duration", ["90d", "30D", "1w", "7 days", ""])
def test_ai_api_request_rejects_unknown_duration(duration: str) -> None:
    with pytest.raises(ValidationError):
        AIAPIRequestCreate(purpose="x" * 10, duration=duration)


def test_ai_api_review_rejects_pending() -> None:
    with pytest.raises(ValidationError):
        AIAPIRequestReview(status="pending")
    for status in (AIAPIRequestStatus.approved, AIAPIRequestStatus.rejected):
        assert AIAPIRequestReview(status=status.value).status == status


# ---- 不合法時區回 400，不是 500 ----


@pytest.mark.parametrize("tz", ["/UTC", "../etc", "zone.tab", "Not/AZone"])
def test_availability_invalid_timezone_is_bad_request(tz: str) -> None:
    with pytest.raises(BadRequestError):
        vm_request_availability_service._resolve_timezone(tz)


# ---- 目前密碼不套新密碼的長度規則 ----


def test_update_password_accepts_short_current_password() -> None:
    body = UpdatePassword(current_password="abc12", new_password="newpassword1")
    assert body.current_password == "abc12"
    with pytest.raises(ValidationError):
        UpdatePassword(current_password="", new_password="newpassword1")
    with pytest.raises(ValidationError):
        UpdatePassword(current_password="abc12", new_password="short")

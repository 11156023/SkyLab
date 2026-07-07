"""
Schemas 模組

API 請求/回應 schemas，按領域分檔：
- common: 通用 schemas（Message, Token, NewPassword 等）
- user: 使用者相關 schemas
- resource: 資源與 Proxmox 相關 schemas
- vm_request: 虛擬機申請 schemas
- spec_change_request: 規格調整 schemas
- audit_log: 審計日誌 schemas
"""

from .ai_api import (
    AIAPICredentialAdminPublic,
    AIAPICredentialPublic,
    AIAPICredentialsAdminPublic,
    AIAPICredentialsPublic,
    AIAPICredentialUpdate,
    AIAPIRequestCreate,
    AIAPIRequestPublic,
    AIAPIRequestReview,
    AIAPIRequestsPublic,
)
from .ai_proxy import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ModelsResponse,
    RateLimitStatusResponse,
    TemplateUsageStatsResponse,
    UsageStatsResponse,
)
from .audit_log import (
    AuditActionMeta,
    AuditLogPublic,
    AuditLogsPublic,
    AuditLogStats,
    AuditUserOption,
)
from .cloudflare import (
    CloudflareConfigPublic,
    CloudflareConfigUpdate,
    CloudflareConnectionTestResult,
    CloudflareDNSRecordCreate,
    CloudflareDNSRecordPublic,
    CloudflareDNSRecordsPublic,
    CloudflareDNSRecordUpdate,
    CloudflarePageInfoPublic,
    CloudflareZoneCreate,
    CloudflareZonePublic,
    CloudflareZonesPublic,
)
from .common import Message, NewPassword, Token, TokenPayload
from .deletion_request import (
    DeletionRequestCreated,
    DeletionRequestPublic,
    DeletionRequestsPublic,
)
from .firewall import (
    ConnectionCreate,
    ConnectionDelete,
    FirewallOptionsPublic,
    FirewallRuleCreate,
    FirewallRulePublic,
    FirewallRuleUpdate,
    LayoutUpdate,
    NATRulePublic,
    PortSpec,
    TopologyEdge,
    TopologyNode,
    TopologyResponse,
)
from .gpu import (
    GPUMappingCreate,
    GPUMappingDetail,
    GPUMappingPublic,
    GPUMappingsPublic,
    GPUMappingUpdate,
    GPUSummary,
    GPUUsageInfo,
)
from .group import (
    GroupCreate,
    GroupDetailPublic,
    GroupMemberAdd,
    GroupMemberPublic,
    GroupPublic,
    GroupsPublic,
)
from .ldap import (
    LdapConfigPublic,
    LdapConfigUpdate,
    LdapLoginRequest,
    LdapTestResult,
    LoginMethodsPublic,
)
from .mining import (
    MiningDismissRequest,
    MiningIncidentPublic,
)
from .monitoring import (
    AlertEventPublic,
    GovernanceConfigPublic,
    GovernanceConfigUpdate,
    MonitoringOverview,
    NodeMetrics,
    VMTopEntry,
)
from .pair_session import (
    PairSessionCreate,
    PairSessionPublic,
)
from .proxmox_config import (
    ProxmoxConfigPublic,
    ProxmoxConfigUpdate,
    ProxmoxConnectionTestResult,
)
from .quota import (
    EffectiveQuotaPublic,
    QuotaUsagePublic,
    ResourceQuotaCreate,
    ResourceQuotaPublic,
    ResourceQuotaUpdate,
)
from .resource import (
    BatchActionRequest,
    BatchActionResponse,
    BatchActionResultItem,
    CurrentStatsResponse,
    DirectSpecUpdateRequest,
    LXCCreateRequest,
    LXCCreateResponse,
    NextVMIDSchema,
    NodeSchema,
    ResetAcceptedResponse,
    ResourcePublic,
    ResourceStatus,
    RRDDataPoint,
    RRDDataResponse,
    SnapshotCreateRequest,
    SnapshotInfo,
    SnapshotResponse,
    SSHKeyResponse,
    TemplateSchema,
    TerminalInfoSchema,
    VMCreateRequest,
    VMCreateResponse,
    VMSchema,
    VMTemplateSchema,
    VNCInfoSchema,
)
from .script_deploy import (
    ScriptDeployRequest,
    ScriptDeployResponse,
    ScriptDeployStatus,
)
from .spec_change_request import (
    SpecChangeRequestCreate,
    SpecChangeRequestPublic,
    SpecChangeRequestReview,
    SpecChangeRequestsPublic,
)
from .teaching import (
    BatchSpecAccepted,
    BatchSpecItemPublic,
    BatchSpecRequest,
    BatchSpecStatusPublic,
    ConfigPushAccepted,
    ConfigPushItemPublic,
    ConfigPushStatusPublic,
    HeatmapEntry,
)
from .user import (
    UpdatePassword,
    UserCreate,
    UserPublic,
    UserRegister,
    UsersPublic,
    UserUpdate,
    UserUpdateMe,
)
from .vm_request import (
    VMRequestAvailabilityDay,
    VMRequestAvailabilityNodeSnapshot,
    VMRequestAvailabilityRequest,
    VMRequestAvailabilityResponse,
    VMRequestAvailabilitySlot,
    VMRequestAvailabilityStackItem,
    VMRequestAvailabilitySummary,
    VMRequestCreate,
    VMRequestPlacementPreview,
    VMRequestPublic,
    VMRequestReview,
    VMRequestReviewContext,
    VMRequestReviewNodeScore,
    VMRequestReviewOverlapItem,
    VMRequestReviewProjectedNode,
    VMRequestReviewRuntimeResource,
    VMRequestsPublic,
    VMRequestWindowAvailabilityRequest,
    VMRequestWindowAvailabilityResponse,
    WorkloadAdviceResponse,
    WorkloadAdviseRequest,
)

__all__ = [
    # Common
    "Message",
    "Token",
    "TokenPayload",
    "NewPassword",
    # Deletion Request
    "DeletionRequestCreated",
    "DeletionRequestPublic",
    "DeletionRequestsPublic",
    # AI API
    "AIAPIRequestCreate",
    "AIAPIRequestReview",
    "AIAPIRequestPublic",
    "AIAPIRequestsPublic",
    "AIAPICredentialAdminPublic",
    "AIAPICredentialPublic",
    "AIAPICredentialsAdminPublic",
    "AIAPICredentialsPublic",
    "AIAPICredentialUpdate",
    # AI Proxy
    "ChatMessage",
    "ChatCompletionRequest",
    "ChatCompletionResponse",
    "ModelsResponse",
    "UsageStatsResponse",
    "TemplateUsageStatsResponse",
    "RateLimitStatusResponse",
    # User
    "UserCreate",
    "UserRegister",
    "UserUpdate",
    "UserUpdateMe",
    "UpdatePassword",
    "UserPublic",
    "UsersPublic",
    # Batch Operations
    "BatchActionRequest",
    "BatchActionResponse",
    "BatchActionResultItem",
    # Resource / Proxmox
    "NodeSchema",
    "VMSchema",
    "VNCInfoSchema",
    "TerminalInfoSchema",
    "TemplateSchema",
    "VMTemplateSchema",
    "NextVMIDSchema",
    "LXCCreateRequest",
    "LXCCreateResponse",
    "ResourcePublic",
    "ResourceStatus",
    "SSHKeyResponse",
    "VMCreateRequest",
    "VMCreateResponse",
    "CurrentStatsResponse",
    "RRDDataPoint",
    "RRDDataResponse",
    "SnapshotInfo",
    "SnapshotCreateRequest",
    "SnapshotResponse",
    "DirectSpecUpdateRequest",
    "ResetAcceptedResponse",
    # VM Request
    "VMRequestAvailabilityRequest",
    "VMRequestAvailabilitySlot",
    "VMRequestAvailabilityStackItem",
    "VMRequestAvailabilityNodeSnapshot",
    "VMRequestAvailabilityDay",
    "VMRequestAvailabilitySummary",
    "VMRequestAvailabilityResponse",
    "VMRequestWindowAvailabilityRequest",
    "VMRequestWindowAvailabilityResponse",
    "VMRequestCreate",
    "VMRequestPlacementPreview",
    "VMRequestReview",
    "VMRequestPublic",
    "VMRequestReviewContext",
    "VMRequestReviewNodeScore",
    "VMRequestReviewOverlapItem",
    "VMRequestReviewProjectedNode",
    "VMRequestReviewRuntimeResource",
    "VMRequestsPublic",
    "WorkloadAdviceResponse",
    "WorkloadAdviseRequest",
    # Audit Log
    "AuditLogPublic",
    "AuditLogsPublic",
    "AuditLogStats",
    "AuditActionMeta",
    "AuditUserOption",
    # Cloudflare
    "CloudflareConfigPublic",
    "CloudflareConfigUpdate",
    "CloudflareConnectionTestResult",
    "CloudflarePageInfoPublic",
    "CloudflareZonePublic",
    "CloudflareZonesPublic",
    "CloudflareZoneCreate",
    "CloudflareDNSRecordPublic",
    "CloudflareDNSRecordsPublic",
    "CloudflareDNSRecordCreate",
    "CloudflareDNSRecordUpdate",
    # Spec Change Request
    "SpecChangeRequestCreate",
    "SpecChangeRequestReview",
    "SpecChangeRequestPublic",
    "SpecChangeRequestsPublic",
    # Groups
    "GroupCreate",
    "GroupPublic",
    "GroupsPublic",
    "GroupDetailPublic",
    "GroupMemberAdd",
    "GroupMemberPublic",
    # Firewall
    "PortSpec",
    "ConnectionCreate",
    "ConnectionDelete",
    "FirewallRuleCreate",
    "FirewallRuleUpdate",
    "FirewallRulePublic",
    "FirewallOptionsPublic",
    "LayoutUpdate",
    "TopologyNode",
    "TopologyEdge",
    "TopologyResponse",
    "NATRulePublic",
    # LDAP
    "LdapConfigPublic",
    "LdapConfigUpdate",
    "LdapLoginRequest",
    "LdapTestResult",
    "LoginMethodsPublic",
    # Monitoring / Governance
    "AlertEventPublic",
    "GovernanceConfigPublic",
    "GovernanceConfigUpdate",
    "MonitoringOverview",
    "NodeMetrics",
    "VMTopEntry",
    # Mining (module D)
    "MiningDismissRequest",
    "MiningIncidentPublic",
    # Proxmox Config
    "ProxmoxConfigPublic",
    "ProxmoxConfigUpdate",
    "ProxmoxConnectionTestResult",
    # Pair Mode (module E)
    "PairSessionCreate",
    "PairSessionPublic",
    # Quota (module E)
    "EffectiveQuotaPublic",
    "QuotaUsagePublic",
    "ResourceQuotaCreate",
    "ResourceQuotaPublic",
    "ResourceQuotaUpdate",
    # Script Deploy
    "ScriptDeployRequest",
    "ScriptDeployResponse",
    "ScriptDeployStatus",
    # Teaching (module E)
    "ConfigPushAccepted",
    "ConfigPushItemPublic",
    "ConfigPushStatusPublic",
    "HeatmapEntry",
    "BatchSpecRequest",
    "BatchSpecAccepted",
    "BatchSpecItemPublic",
    "BatchSpecStatusPublic",
]

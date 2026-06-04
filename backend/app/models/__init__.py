"""
Models 模組

此模組包含所有資料庫模型定義（DB tables + enums）。
API schemas 已移至 app.schemas 模組。
"""

from sqlmodel import SQLModel

from .ai_api_credential import AIAPICredential
from .ai_api_rate_limit import AIAPIRateLimit
from .ai_api_request import AIAPIRequest, AIAPIRequestStatus
from .ai_api_usage import AIAPIUsage
from .ai_template_call_log import AITemplateCallLog
from .audit_log import AuditAction, AuditLog
from .base import get_datetime_utc
from .batch_provision import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
)
from .cloudflare_config import CloudflareConfig
from .deletion_request import DeletionRequest, DeletionRequestStatus
from .firewall_layout import FirewallLayout
from .gateway_config import GatewayConfig
from .group import Group
from .group_member import GroupMember
from .ip_allocation import IpAllocation
from .nat_rule import NatRule
from .proxmox_config import (
    ProxmoxConfig,
    ProxmoxConnectionConfig,
    ProxmoxMigrationConfig,
    ProxmoxPlacementConfig,
    ProxmoxRebalanceConfig,
    ProxmoxSchedulerConfig,
)
from .proxmox_node import ProxmoxNode
from .proxmox_storage import ProxmoxStorage
from .resource import Resource
from .resource_network import ResourceNetwork
from .reverse_proxy_rule import ReverseProxyRule
from .script_deploy_log import ScriptDeployLog
from .spec_change_request import (
    SpecChangeRequest,
    SpecChangeRequestStatus,
    SpecChangeType,
)
from .subnet_config import SubnetConfig
from .teacher_judge_file import TeacherJudgeFile, TeacherJudgeFileStatus
from .teacher_judge_script_artifact import (
    TeacherJudgeScriptArtifact,
    TeacherJudgeScriptLanguage,
    TeacherJudgeScriptSource,
    TeacherJudgeScriptStatus,
)
from .teacher_judge_script_run import (
    TeacherJudgeScriptRun,
    TeacherJudgeScriptRunStatus,
    TeacherJudgeScriptRunTargetScope,
)
from .teacher_judge_template_command import TeacherJudgeTemplateCommand
from .tunnel_proxy import TunnelProxy
from .user import User, UserBase, UserRole
from .vm_migration_job import VMMigrationJob, VMMigrationJobStatus
from .vm_request import (
    VMMigrationStatus,
    VMRequest,
    VMRequestMigrationState,
    VMRequestProvisioningState,
    VMRequestReviewState,
    VMRequestScheduleState,
    VMRequestStatus,
)

__all__ = [
    # Base
    "SQLModel",
    "get_datetime_utc",
    # User
    "UserBase",
    "User",
    "UserRole",
    # AI API
    "AIAPICredential",
    "AIAPIRequest",
    "AIAPIRequestStatus",
    "AIAPIUsage",
    "AIAPIRateLimit",
    "AITemplateCallLog",
    # Resource
    "Resource",
    "ResourceNetwork",
    "VMMigrationJob",
    "VMMigrationJobStatus",
    # VM Request
    "VMMigrationStatus",
    "VMRequest",
    "VMRequestStatus",
    "VMRequestMigrationState",
    "VMRequestProvisioningState",
    "VMRequestReviewState",
    "VMRequestScheduleState",
    # Audit Log
    "AuditAction",
    "AuditLog",
    # Spec Change Request
    "SpecChangeRequest",
    "SpecChangeRequestStatus",
    "SpecChangeType",
    # Groups
    "Group",
    "GroupMember",
    # Proxmox Config
    "ProxmoxConfig",
    "ProxmoxConnectionConfig",
    "ProxmoxMigrationConfig",
    "ProxmoxPlacementConfig",
    "ProxmoxRebalanceConfig",
    "ProxmoxSchedulerConfig",
    # Proxmox Nodes
    "ProxmoxNode",
    # Proxmox Storages
    "ProxmoxStorage",
    # Firewall Layout
    "FirewallLayout",
    # NAT Rules
    "NatRule",
    # Gateway Config
    "GatewayConfig",
    # Cloudflare Config
    "CloudflareConfig",
    # Reverse Proxy Rules
    "ReverseProxyRule",
    # Batch Provision
    "BatchProvisionJob",
    "BatchProvisionJobStatus",
    "BatchProvisionTask",
    "BatchProvisionTaskStatus",
    # Tunnel Proxies
    "TunnelProxy",
    # Subnet & IP Management
    "SubnetConfig",
    "IpAllocation",
    "ScriptDeployLog",
    "TeacherJudgeFile",
    "TeacherJudgeFileStatus",
    "TeacherJudgeScriptArtifact",
    "TeacherJudgeScriptLanguage",
    "TeacherJudgeScriptRun",
    "TeacherJudgeScriptRunStatus",
    "TeacherJudgeScriptRunTargetScope",
    "TeacherJudgeScriptSource",
    "TeacherJudgeScriptStatus",
    "TeacherJudgeTemplateCommand",
    # Deletion Request
    "DeletionRequest",
    "DeletionRequestStatus",
]

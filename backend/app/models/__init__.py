"""
Models 模組

此模組包含所有資料庫模型定義（DB tables + enums）。
API schemas 已移至 app.schemas 模組。
"""

from sqlmodel import SQLModel

from .ai_api_credential import AIAPICredential
from .ai_api_request import AIAPIRequest, AIAPIRequestStatus
from .ai_api_usage import AIAPIUsage
from .ai_pve_template import AIPVETemplate
from .ai_template_call_log import AITemplateCallLog
from .alert_event import AlertEvent, AlertMetric, AlertScope
from .audit_log import AuditAction, AuditLog
from .base import get_datetime_utc
from .batch_provision import (
    BatchProvisionJob,
    BatchProvisionJobStatus,
    BatchProvisionTask,
    BatchProvisionTaskStatus,
)
from .cloudflare_config import CloudflareConfig
from .course import (
    CourseDeployment,
    CourseDifficulty,
    CoursePath,
    CoursePathStatus,
    CourseQuestion,
    CourseQuestionType,
    CourseRoom,
    CourseTask,
    UserCourseProgress,
)
from .course_environment import (
    ClassCapacityReservation,
    CourseEnvironment,
    CourseEnvironmentAudience,
    CourseEnvironmentEdge,
    CourseEnvironmentNode,
    CourseEnvironmentPublication,
    CourseEnvironmentVersion,
    CourseEnvironmentVersionStatus,
)
from .deletion_request import DeletionRequest, DeletionRequestStatus
from .firewall_layout import FirewallLayout
from .gateway_config import GatewayConfig
from .governance_config import GovernanceConfig
from .ip_allocation import IpAllocation
from .ldap_config import LdapConfig
from .mining_incident import MiningIncident, MiningIncidentStatus
from .nat_rule import NatRule
from .proxmox_config import ProxmoxConfig
from .proxmox_connection import ProxmoxConnection
from .proxmox_node import ProxmoxNode
from .proxmox_storage import ProxmoxStorage
from .push_subscription import PushSubscription, WebPushConfig
from .quick_practice import QuickPracticeSession, QuickPracticeSessionMachine
from .quota_config import QuotaConfig
from .resource import Resource
from .resource_network import ResourceNetwork
from .resource_quota import ResourceQuota
from .resource_share import ResourceShare
from .reverse_proxy_rule import ReverseProxyRule
from .spec_change_request import (
    SpecChangeRequest,
    SpecChangeRequestStatus,
    SpecChangeType,
)
from .subnet_config import SubnetConfig
from .task_record import TaskRecord, TaskRecordStatus
from .teacher_judge_attachment import (
    TeacherJudgeAttachmentStatus,
    TeacherJudgeSessionAttachment,
)
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
from .teacher_judge_session import (
    TeacherJudgeMessageRole,
    TeacherJudgeMessageType,
    TeacherJudgeSession,
    TeacherJudgeSessionMessage,
    TeacherJudgeSessionStatus,
)
from .teacher_judge_student_submission import TeacherJudgeStudentSubmission
from .teacher_judge_template_command import TeacherJudgeTemplateCommand
from .teaching_class import (
    TeachingClass,
    TeachingClassMachineNode,
    TeachingClassStatus,
    TeachingClassStudent,
    TeachingClassStudentMachine,
    TeachingClassTaskFile,
    TeachingClassWeek,
)
from .tunnel_proxy import TunnelProxy
from .user import User, UserBase, UserRole
from .vm_request import VMProvisioningStatus, VMRequest, VMRequestStatus
from .vm_template import (
    TemplateAttachment,
    VMTemplate,
    VMTemplateStatus,
    VMTemplateVisibility,
)
from .wireguard_peer import WireGuardPeer

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
    "AIPVETemplate",
    "AITemplateCallLog",
    # Resource
    "Resource",
    "ResourceNetwork",
    "ResourceQuota",
    "ResourceShare",
    "QuotaConfig",
    # VM Request
    "VMProvisioningStatus",
    "VMRequest",
    "VMRequestStatus",
    # Audit Log
    "AuditAction",
    "AuditLog",
    # Governance / Alerts
    "AlertEvent",
    "AlertMetric",
    "AlertScope",
    "GovernanceConfig",
    "MiningIncident",
    "MiningIncidentStatus",
    # LDAP
    "LdapConfig",
    # Web Push
    "PushSubscription",
    "WebPushConfig",
    # Spec Change Request
    "SpecChangeRequest",
    "SpecChangeRequestStatus",
    "SpecChangeType",
    # Proxmox Config
    "ProxmoxConfig",
    "ProxmoxConnection",
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
    "CourseEnvironment",
    "CourseEnvironmentAudience",
    "CourseEnvironmentEdge",
    "CourseEnvironmentPublication",
    "CourseEnvironmentVersion",
    "CourseEnvironmentVersionStatus",
    "CourseEnvironmentNode",
    "ClassCapacityReservation",
    "QuickPracticeSession",
    "QuickPracticeSessionMachine",
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
    "TeacherJudgeFile",
    "TeacherJudgeFileStatus",
    "TeacherJudgeAttachmentStatus",
    "TeacherJudgeSessionAttachment",
    "TeacherJudgeMessageRole",
    "TeacherJudgeMessageType",
    "TeacherJudgeSession",
    "TeacherJudgeSessionMessage",
    "TeacherJudgeSessionStatus",
    "TeacherJudgeScriptArtifact",
    "TeacherJudgeScriptLanguage",
    "TeacherJudgeScriptRun",
    "TeacherJudgeScriptRunStatus",
    "TeacherJudgeScriptRunTargetScope",
    "TeacherJudgeScriptSource",
    "TeacherJudgeScriptStatus",
    "TeacherJudgeStudentSubmission",
    "TeacherJudgeTemplateCommand",
    # Deletion Request
    "DeletionRequest",
    "DeletionRequestStatus",
    # VM Template (範本系統 2.0)
    "TemplateAttachment",
    "VMTemplate",
    "VMTemplateStatus",
    "VMTemplateVisibility",
    # Task Record (背景任務)
    "TaskRecord",
    "TaskRecordStatus",
    "TeachingClass",
    "TeachingClassStatus",
    "TeachingClassMachineNode",
    "TeachingClassWeek",
    "TeachingClassTaskFile",
    "TeachingClassStudent",
    "TeachingClassStudentMachine",
    "WireGuardPeer",
    # Course Lab (互動式實作教學)
    "CoursePath",
    "CoursePathStatus",
    "CourseRoom",
    "CourseDifficulty",
    "CourseTask",
    "CourseQuestion",
    "CourseQuestionType",
    "UserCourseProgress",
    "CourseDeployment",
]

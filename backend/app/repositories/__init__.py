from . import cloudflare_config, gateway_config, reverse_proxy
from .audit_log import (
    create_audit_log,
    get_audit_logs,
    get_audit_logs_by_user,
    get_audit_logs_by_vmid,
)
from .resource import (
    create_resource,
    delete_resource,
    get_all_resources,
    get_resource_by_vmid,
    get_resources_by_user,
    update_resource,
)
from .spec_change_request import (
    cancel_open_spec_change_requests_for_vmid,
    create_spec_change_request,
    get_all_spec_change_requests,
    get_open_spec_change_request_by_vmid,
    get_spec_change_request_by_id,
    get_spec_change_requests_by_user,
    mark_spec_change_applied,
    mark_spec_change_apply_failed,
    mark_spec_change_apply_started,
    update_spec_change_current_specs,
    update_spec_change_request_status,
)
from .user import (
    DUMMY_HASH,
    authenticate,
    create_user,
    get_user_by_email,
    update_user,
)
from .vm_request import (
    create_vm_request,
    get_all_vm_requests,
    get_vm_request_by_id,
    get_vm_requests_by_user,
    update_vm_request_status,
)

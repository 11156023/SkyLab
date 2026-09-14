from fastapi import APIRouter

from app.api.routes import (
    ai,
    audit_logs,
    batch_provision,
    classroom,
    cloudflare,
    course_admin,
    course_environments,
    courses,
    deletion_requests,
    desktop_client,
    firewall,
    gateway,
    governance,
    gpu,
    ip_management,
    jobs,
    ldap_config,
    login,
    lxc,
    mining_incidents,
    monitoring,
    private,
    proxmox_config,
    push,
    quick_practice,
    quotas,
    resource_details,
    resource_settings,
    resources,
    reverse_proxy,
    rubric,
    spec_change_requests,
    teacher_judge_files,
    teacher_judge_scripts,
    teacher_judge_sessions,
    teaching_classes,
    templates,
    tunnel,
    users,
    utils,
    vm,
    vm_requests,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(resources.router)
api_router.include_router(resource_details.router)
api_router.include_router(resource_settings.router)
api_router.include_router(vm.router)
api_router.include_router(lxc.router)
api_router.include_router(vm_requests.router)
api_router.include_router(deletion_requests.router)
api_router.include_router(monitoring.router)
api_router.include_router(governance.router)
api_router.include_router(quotas.router)
api_router.include_router(teaching_classes.router)
api_router.include_router(courses.router)
api_router.include_router(course_admin.router)
api_router.include_router(course_environments.router)
api_router.include_router(quick_practice.router)
api_router.include_router(ldap_config.router)
api_router.include_router(mining_incidents.router)
api_router.include_router(ai.router)
api_router.include_router(spec_change_requests.router)
api_router.include_router(audit_logs.router)
api_router.include_router(classroom.router)
api_router.include_router(batch_provision.router)
api_router.include_router(proxmox_config.router)
api_router.include_router(cloudflare.router)
api_router.include_router(firewall.router)
api_router.include_router(reverse_proxy.router)
api_router.include_router(gateway.router)
api_router.include_router(gpu.router)
api_router.include_router(ip_management.router)
api_router.include_router(jobs.router)
api_router.include_router(push.router)
api_router.include_router(rubric.router)
api_router.include_router(teacher_judge_files.router)
api_router.include_router(teacher_judge_scripts.router)
api_router.include_router(teacher_judge_sessions.router)
api_router.include_router(templates.router)
api_router.include_router(tunnel.router)
api_router.include_router(desktop_client.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)

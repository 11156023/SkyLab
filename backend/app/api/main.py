from fastapi import APIRouter

from app.api.routes import (
    ai,
    audit_logs,
    batch_provision,
    cloudflare,
    deletion_requests,
    desktop_client,
    firewall,
    gateway,
    gpu,
    groups,
    ip_management,
    jobs,
    login,
    lxc,
    migration_jobs,
    private,
    proxmox_config,
    resource_details,
    resources,
    reverse_proxy,
    rubric,
    script_deploy,
    spec_change_requests,
    teacher_judge_files,
    teacher_judge_scripts,
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
api_router.include_router(vm.router)
api_router.include_router(lxc.router)
api_router.include_router(vm_requests.router)
api_router.include_router(deletion_requests.router)
api_router.include_router(migration_jobs.router)
api_router.include_router(ai.router)
api_router.include_router(spec_change_requests.router)
api_router.include_router(audit_logs.router)
api_router.include_router(groups.router)
api_router.include_router(batch_provision.router)
api_router.include_router(proxmox_config.router)
api_router.include_router(cloudflare.router)
api_router.include_router(firewall.router)
api_router.include_router(reverse_proxy.router)
api_router.include_router(gateway.router)
api_router.include_router(gpu.router)
api_router.include_router(ip_management.router)
api_router.include_router(script_deploy.router)
api_router.include_router(jobs.router)
api_router.include_router(rubric.router)
api_router.include_router(teacher_judge_files.router)
api_router.include_router(teacher_judge_scripts.router)
api_router.include_router(tunnel.router)
api_router.include_router(desktop_client.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)

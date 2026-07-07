/**
 * Legacy SDK facade.
 *
 * The generated SDK (`sdk.gen.ts`) exposes:
 *   - service-prefixed methods (`UsersService.usersReadUserMe(...)`)
 *   - `{ path, body, query }` argument shape
 *   - `Promise<{ data, error, response }>` return shape
 *
 * This file re-exports a thin facade that mirrors the OLDER API style used
 * throughout the codebase:
 *   - short method names (`UsersService.readUserMe(...)`)
 *   - `{ requestBody, formData, <pathParam>, ... }` argument shape
 *   - `Promise<T>` return shape (auto-unwraps `data` and re-throws `error`)
 *
 * Hand-maintained. Re-add the `legacy-services` re-export in `index.ts` after
 * regenerating the client.
 */

import * as Sdk from "./sdk.gen"
import type * as T from "./types.gen"

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

type SdkResult<R> = { data?: R; error?: unknown; response?: unknown }

async function unwrap<R>(p: Promise<SdkResult<R>>): Promise<R> {
  const r = (await p) as SdkResult<R>
  if (r.error !== undefined && r.error !== null) {
    throw r.error
  }
  return r.data as R
}

// ---------------------------------------------------------------------------
// Login
// ---------------------------------------------------------------------------

export const LoginService = {
  loginAccessToken: (opts: { formData: T.BodyLoginLoginAccessToken }) =>
    unwrap<T.Token>(Sdk.LoginService.loginLoginAccessToken({ body: opts.formData })),

  recoverPassword: (opts: { email: string }) =>
    unwrap<T.Message>(Sdk.LoginService.loginRecoverPassword({ path: { email: opts.email } })),

  resetPassword: (opts: { requestBody: T.NewPassword }) =>
    unwrap<T.Message>(Sdk.LoginService.loginResetPassword({ body: opts.requestBody })),

  // Pass-through for new-style callers
  loginGoogle: (opts: { requestBody: T.GoogleLoginRequest }) =>
    unwrap<T.Token>(Sdk.LoginService.loginLoginGoogle({ body: opts.requestBody })),

  refreshToken: (opts: { requestBody: T.RefreshTokenRequest }) =>
    unwrap<T.Token>(Sdk.LoginService.loginRefreshToken({ body: opts.requestBody })),

  testToken: () => unwrap<T.UserPublic>(Sdk.LoginService.loginTestToken()),

  loginLdap: (opts: { requestBody: T.LdapLoginRequest }) =>
    unwrap<T.Token>(Sdk.LoginService.loginLoginLdap({ body: opts.requestBody })),

  loginMethods: () =>
    unwrap<T.LoginMethodsPublic>(Sdk.LoginService.loginLoginMethods()),
}

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

export const UsersService = {
  readUserMe: () => unwrap<T.UserPublic>(Sdk.UsersService.usersReadUserMe()),

  readUsers: (opts: { skip?: number; limit?: number } = {}) =>
    unwrap<T.UsersPublic>(Sdk.UsersService.usersReadUsers({ query: opts })),

  readUserById: (opts: { userId: string }) =>
    unwrap<T.UserPublic>(
      Sdk.UsersService.usersReadUserById({ path: { user_id: opts.userId } }),
    ),

  createUser: (opts: { requestBody: T.UserCreate }) =>
    unwrap<T.UserPublic>(Sdk.UsersService.usersCreateUser({ body: opts.requestBody })),

  updateUser: (opts: { userId: string; requestBody: T.UserUpdate }) =>
    unwrap<T.UserPublic>(
      Sdk.UsersService.usersUpdateUser({
        path: { user_id: opts.userId },
        body: opts.requestBody,
      }),
    ),

  deleteUser: (opts: { userId: string }) =>
    unwrap<T.Message>(
      Sdk.UsersService.usersDeleteUser({ path: { user_id: opts.userId } }),
    ),

  updateUserMe: (opts: { requestBody: T.UserUpdateMe }) =>
    unwrap<T.UserPublic>(Sdk.UsersService.usersUpdateUserMe({ body: opts.requestBody })),

  updatePasswordMe: (opts: { requestBody: T.UpdatePassword }) =>
    unwrap<T.Message>(
      Sdk.UsersService.usersUpdatePasswordMe({ body: opts.requestBody }),
    ),

  deleteUserMe: () => unwrap<T.Message>(Sdk.UsersService.usersDeleteUserMe()),

  registerUser: (opts: { requestBody: T.UserRegister }) =>
    unwrap<T.UserPublic>(Sdk.UsersService.usersRegisterUser({ body: opts.requestBody })),
}

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

export const ResourcesService = {
  listResources: (opts: { node?: string | null } = {}) =>
    unwrap<Array<T.ResourcePublic>>(Sdk.ResourcesService.resourcesListResources({ query: opts })),

  listMyResources: () =>
    unwrap<Array<T.ResourcePublic>>(Sdk.ResourcesService.resourcesListMyResources()),

  listNodes: () =>
    unwrap<Array<T.NodeSchema>>(Sdk.ResourcesService.resourcesListNodes()),

  getResource: (opts: { vmid: number }) =>
    unwrap<T.ResourcePublic>(
      Sdk.ResourcesService.resourcesGetResource({ path: { vmid: opts.vmid } }),
    ),

  getSshKey: (opts: { vmid: number }) =>
    unwrap<T.SshKeyResponse>(
      Sdk.ResourcesService.resourcesGetSshKey({ path: { vmid: opts.vmid } }),
    ),

  getResourceConfig: (opts: { vmid: number }) =>
    unwrap<T.ResourceConfig>(
      Sdk.ResourcesService.resourcesGetResourceConfig({ path: { vmid: opts.vmid } }) as unknown as Promise<SdkResult<T.ResourceConfig>>,
    ),

  startResource: (opts: { vmid: number }) =>
    unwrap<unknown>(
      Sdk.ResourcesService.resourcesStartResource({ path: { vmid: opts.vmid } }),
    ),

  stopResource: (opts: { vmid: number }) =>
    unwrap<unknown>(
      Sdk.ResourcesService.resourcesStopResource({ path: { vmid: opts.vmid } }),
    ),

  rebootResource: (opts: { vmid: number }) =>
    unwrap<unknown>(
      Sdk.ResourcesService.resourcesRebootResource({ path: { vmid: opts.vmid } }),
    ),

  shutdownResource: (opts: { vmid: number }) =>
    unwrap<unknown>(
      Sdk.ResourcesService.resourcesShutdownResource({ path: { vmid: opts.vmid } }),
    ),

  resetResource: (opts: { vmid: number }) =>
    unwrap<unknown>(
      Sdk.ResourcesService.resourcesResetResource({ path: { vmid: opts.vmid } }),
    ),

  deleteResource: (opts: { vmid: number; purge?: boolean; force?: boolean }) =>
    unwrap<T.DeletionRequestCreated>(
      Sdk.ResourcesService.resourcesDeleteResource({
        path: { vmid: opts.vmid },
        query: { purge: opts.purge, force: opts.force },
      }),
    ),

  batchAction: (opts: { requestBody: T.BatchActionRequest }) =>
    unwrap<T.BatchActionResponse>(
      Sdk.ResourcesService.resourcesBatchAction({ body: opts.requestBody }),
    ),
}

// ---------------------------------------------------------------------------
// Resource details (stats / snapshots / spec)
// ---------------------------------------------------------------------------

export const ResourceDetailsService = {
  getCurrentStats: (opts: { vmid: number }) =>
    unwrap<T.CurrentStatsResponse>(
      Sdk.ResourceDetailsService.resourceDetailsGetCurrentStats({
        path: { vmid: opts.vmid },
      }),
    ),

  getRrdStats: (opts: { vmid: number; timeframe?: string }) =>
    unwrap<T.RrdDataResponse>(
      Sdk.ResourceDetailsService.resourceDetailsGetRrdStats({
        path: { vmid: opts.vmid },
        query: { timeframe: opts.timeframe },
      }),
    ),

  listSnapshots: (opts: { vmid: number }) =>
    unwrap<Array<T.SnapshotInfo>>(
      Sdk.ResourceDetailsService.resourceDetailsListSnapshots({
        path: { vmid: opts.vmid },
      }),
    ),

  createSnapshot: (opts: { vmid: number; requestBody: T.SnapshotCreateRequest }) =>
    unwrap<T.SnapshotResponse>(
      Sdk.ResourceDetailsService.resourceDetailsCreateSnapshot({
        path: { vmid: opts.vmid },
        body: opts.requestBody,
      }),
    ),

  deleteSnapshot: (opts: { vmid: number; snapname: string }) =>
    unwrap<T.SnapshotResponse>(
      Sdk.ResourceDetailsService.resourceDetailsDeleteSnapshot({
        path: { vmid: opts.vmid, snapname: opts.snapname },
      }),
    ),

  rollbackSnapshot: (opts: { vmid: number; snapname: string }) =>
    unwrap<T.SnapshotResponse>(
      Sdk.ResourceDetailsService.resourceDetailsRollbackSnapshot({
        path: { vmid: opts.vmid, snapname: opts.snapname },
      }),
    ),

  resetToInit: (opts: { vmid: number }) =>
    unwrap<T.ResetAcceptedResponse>(
      Sdk.ResourceDetailsService.resourceDetailsResetToInit({
        path: { vmid: opts.vmid },
      }),
    ),

  createInitSnapshot: (opts: { vmid: number }) =>
    unwrap<unknown>(
      Sdk.ResourceDetailsService.resourceDetailsCreateInitSnapshot({
        path: { vmid: opts.vmid },
      }),
    ),

  directUpdateSpec: (opts: { vmid: number; requestBody: T.DirectSpecUpdateRequest }) =>
    unwrap<unknown>(
      Sdk.ResourceDetailsService.resourceDetailsDirectUpdateSpec({
        path: { vmid: opts.vmid },
        body: opts.requestBody,
      }),
    ),
}

// ---------------------------------------------------------------------------
// Spec change requests
// ---------------------------------------------------------------------------

export const SpecChangeRequestsService = {
  createSpecChangeRequest: (opts: { requestBody: T.SpecChangeRequestCreate }) =>
    unwrap<T.SpecChangeRequestPublic>(
      Sdk.SpecChangeRequestsService.specChangeRequestsCreateSpecChangeRequest({
        body: opts.requestBody,
      }),
    ),
}

// ---------------------------------------------------------------------------
// Audit logs
// ---------------------------------------------------------------------------

export const AuditLogsService = {
  getResourceAuditLogs: (opts: { vmid: number; skip?: number; limit?: number }) =>
    unwrap<T.AuditLogsPublic>(
      Sdk.AuditLogsService.auditLogsGetResourceAuditLogs({
        path: { vmid: opts.vmid },
        query: { skip: opts.skip, limit: opts.limit },
      }),
    ),
}

// ---------------------------------------------------------------------------
// LXC
// ---------------------------------------------------------------------------

export const LxcService = {
  getTemplates: () =>
    unwrap<Array<T.TemplateSchema>>(Sdk.LxcService.lxcGetTemplates()),

  createLxc: (opts: { requestBody: T.LxcCreateRequest }) =>
    unwrap<T.LxcCreateResponse>(Sdk.LxcService.lxcCreateLxc({ body: opts.requestBody })),

  getLxcTerminal: (opts: { vmid: number }) =>
    unwrap<T.TerminalInfoSchema>(
      Sdk.LxcService.lxcGetLxcTerminal({ path: { vmid: opts.vmid } }),
    ),
}

// ---------------------------------------------------------------------------
// VM
// ---------------------------------------------------------------------------

export const VmService = {
  getVmTemplates: () =>
    unwrap<Array<T.VmTemplateSchema>>(Sdk.VmService.vmGetVmTemplates()),

  createVm: (opts: { requestBody: T.VmCreateRequest }) =>
    unwrap<T.VmCreateResponse>(Sdk.VmService.vmCreateVm({ body: opts.requestBody })),

  getVmConsole: (opts: { vmid: number }) =>
    unwrap<T.VncInfoSchema>(
      Sdk.VmService.vmGetVmConsole({ path: { vmid: opts.vmid } }),
    ),
}

// VNC alias — historical name still used by VNCConsoleDialog
export const VncConsoleService = {
  getVmConsole: VmService.getVmConsole,
}

// ---------------------------------------------------------------------------
// VM requests
// ---------------------------------------------------------------------------

export const VmRequestsService = {
  listMyVmRequests: (opts: { skip?: number; limit?: number } = {}) =>
    unwrap<T.VmRequestsPublic>(
      Sdk.VmRequestsService.vmRequestsListMyVmRequests({ query: opts }),
    ),

  listAllVmRequests: (
    opts: { status?: T.VmRequestStatus | null; skip?: number; limit?: number } = {},
  ) =>
    unwrap<T.VmRequestsPublic>(
      Sdk.VmRequestsService.vmRequestsListAllVmRequests({ query: opts }),
    ),

  getVmRequest: (opts: { requestId: string }) =>
    unwrap<T.VmRequestPublic>(
      Sdk.VmRequestsService.vmRequestsGetVmRequest({
        path: { request_id: opts.requestId },
      }),
    ),

  createVmRequest: (opts: { requestBody: T.VmRequestCreate }) =>
    unwrap<T.VmRequestPublic>(
      Sdk.VmRequestsService.vmRequestsCreateVmRequest({ body: opts.requestBody }),
    ),

  cancelVmRequest: (opts: { requestId: string }) =>
    unwrap<T.VmRequestPublic>(
      Sdk.VmRequestsService.vmRequestsCancelVmRequest({
        path: { request_id: opts.requestId },
      }),
    ),

  retryVmRequest: (opts: { requestId: string }) =>
    unwrap<T.VmRequestPublic>(
      Sdk.VmRequestsService.vmRequestsRetryVmRequest({
        path: { request_id: opts.requestId },
      }),
    ),

  reviewVmRequest: (opts: { requestId: string; requestBody: T.VmRequestReview }) =>
    unwrap<T.VmRequestPublic>(
      Sdk.VmRequestsService.vmRequestsReviewVmRequest({
        path: { request_id: opts.requestId },
        body: opts.requestBody,
      }),
    ),

  adviseWorkload: (opts: { requestBody: T.WorkloadAdviseRequest }) =>
    unwrap<T.WorkloadAdviceResponse>(
      Sdk.VmRequestsService.vmRequestsAdviseWorkload({ body: opts.requestBody }),
    ),
}

// ---------------------------------------------------------------------------
// Monitoring (module C)
// ---------------------------------------------------------------------------

export const MonitoringService = {
  getOverview: () =>
    unwrap<T.MonitoringOverview>(Sdk.MonitoringService.monitoringGetOverview()),

  getNodeRrd: (opts: { node: string; timeframe?: string }) =>
    unwrap<Array<{ [key: string]: unknown }>>(
      Sdk.MonitoringService.monitoringGetNodeRrd({
        path: { node: opts.node },
        query: { timeframe: opts.timeframe },
      }),
    ),

  getVmRrd: (opts: { vmid: number; timeframe?: string }) =>
    unwrap<Array<{ [key: string]: unknown }>>(
      Sdk.MonitoringService.monitoringGetVmRrd({
        path: { vmid: opts.vmid },
        query: { timeframe: opts.timeframe },
      }),
    ),

  listAlerts: (opts: { active?: boolean; limit?: number } = {}) =>
    unwrap<Array<T.AlertEventPublic>>(
      Sdk.MonitoringService.monitoringListAlerts({ query: opts }),
    ),

  acknowledgeAlert: (opts: { alertId: string }) =>
    unwrap<T.AlertEventPublic>(
      Sdk.MonitoringService.monitoringAcknowledgeAlert({
        path: { alert_id: opts.alertId },
      }),
    ),
}

// ---------------------------------------------------------------------------
// Mining incidents (module D)
// ---------------------------------------------------------------------------

export const MiningIncidentsService = {
  listIncidents: (
    opts: { status?: T.MiningIncidentStatus | null; limit?: number } = {},
  ) =>
    unwrap<Array<T.MiningIncidentPublic>>(
      Sdk.MiningService.miningListIncidents({ query: opts }),
    ),

  banIncident: (opts: { incidentId: string }) =>
    unwrap<T.MiningIncidentPublic>(
      Sdk.MiningService.miningBanIncident({
        path: { incident_id: opts.incidentId },
      }),
    ),

  dismissIncident: (opts: {
    incidentId: string
    requestBody: T.MiningDismissRequest
  }) =>
    unwrap<T.MiningIncidentPublic>(
      Sdk.MiningService.miningDismissIncident({
        path: { incident_id: opts.incidentId },
        body: opts.requestBody,
      }),
    ),

  setExemption: (opts: { vmid: number; exempt: boolean }) =>
    unwrap<T.MiningExemptResponse>(
      Sdk.MiningService.miningSetExemption({
        path: { vmid: opts.vmid },
        body: { exempt: opts.exempt },
      }),
    ),
}

// ---------------------------------------------------------------------------
// Governance config (module C)
// ---------------------------------------------------------------------------

export const GovernanceService = {
  getConfig: () =>
    unwrap<T.GovernanceConfigPublic>(Sdk.GovernanceService.governanceGetConfig()),

  updateConfig: (opts: { requestBody: T.GovernanceConfigUpdate }) =>
    unwrap<T.GovernanceConfigPublic>(
      Sdk.GovernanceService.governanceUpdateConfig({ body: opts.requestBody }),
    ),
}

// ---------------------------------------------------------------------------
// LDAP config (module C)
// ---------------------------------------------------------------------------

export const LdapConfigService = {
  getConfig: () =>
    unwrap<T.LdapConfigPublic>(Sdk.LdapConfigService.ldapConfigGetConfig()),

  updateConfig: (opts: { requestBody: T.LdapConfigUpdate }) =>
    unwrap<T.LdapConfigPublic>(
      Sdk.LdapConfigService.ldapConfigUpdateConfig({ body: opts.requestBody }),
    ),

  testConnection: (opts: { requestBody?: T.LdapConfigUpdate | null } = {}) =>
    unwrap<T.LdapTestResult>(
      Sdk.LdapConfigService.ldapConfigTestConnection({
        body: opts.requestBody ?? undefined,
      }),
    ),
}

// ---------------------------------------------------------------------------
// Groups
// ---------------------------------------------------------------------------

export const GroupsService = {
  listGroups: () =>
    unwrap<T.GroupsPublic>(Sdk.GroupsService.groupsListGroups()),

  getGroup: (opts: { groupId: string }) =>
    unwrap<T.GroupDetailPublic>(
      Sdk.GroupsService.groupsGetGroup({ path: { group_id: opts.groupId } }),
    ),

  createGroup: (opts: { requestBody: T.GroupCreate }) =>
    unwrap<T.GroupPublic>(Sdk.GroupsService.groupsCreateGroup({ body: opts.requestBody })),

  deleteGroup: (opts: { groupId: string }) =>
    unwrap<T.Message>(
      Sdk.GroupsService.groupsDeleteGroup({ path: { group_id: opts.groupId } }),
    ),

  addMembers: (opts: { groupId: string; requestBody: T.GroupMemberAdd }) =>
    unwrap<T.Message>(
      Sdk.GroupsService.groupsAddMembers({
        path: { group_id: opts.groupId },
        body: opts.requestBody,
      }),
    ),

  removeMember: (opts: { groupId: string; userId: string }) =>
    unwrap<T.Message>(
      Sdk.GroupsService.groupsRemoveMember({
        path: { group_id: opts.groupId, user_id: opts.userId },
      }),
    ),

  importMembersFromCsv: (opts: { groupId: string; formData: T.BodyGroupsImportMembersFromCsv }) =>
    unwrap<T.CsvImportResult>(
      Sdk.GroupsService.groupsImportMembersFromCsv({
        path: { group_id: opts.groupId },
        body: opts.formData,
      }),
    ),
}

// ---------------------------------------------------------------------------
// Deletion requests
// ---------------------------------------------------------------------------

export const DeletionRequestsService = {
  listMyDeletionRequests: (opts: { skip?: number; limit?: number } = {}) =>
    unwrap<T.DeletionRequestsPublic>(
      Sdk.DeletionRequestsService.deletionRequestsListMyDeletionRequests({ query: opts }),
    ),

  listAllDeletionRequests: (
    opts: { status?: T.DeletionRequestStatus | null; skip?: number; limit?: number } = {},
  ) =>
    unwrap<T.DeletionRequestsPublic>(
      Sdk.DeletionRequestsService.deletionRequestsListAllDeletionRequests({ query: opts }),
    ),

  cancelDeletionRequest: (opts: { requestId: string }) =>
    unwrap<T.DeletionRequestPublic>(
      Sdk.DeletionRequestsService.deletionRequestsCancelDeletionRequest({
        path: { request_id: opts.requestId },
      }),
    ),

  retryDeletionRequest: (opts: { requestId: string }) =>
    unwrap<T.DeletionRequestPublic>(
      Sdk.DeletionRequestsService.deletionRequestsRetryDeletionRequest({
        path: { request_id: opts.requestId },
      }),
    ),
}

// ---------------------------------------------------------------------------
// Script deploy
// ---------------------------------------------------------------------------

export const ScriptDeployService = {
  deployServiceTemplate: (opts: { requestBody: T.ScriptDeployRequest }) =>
    unwrap<T.ScriptDeployResponse>(
      Sdk.ScriptDeployService.scriptDeployDeployServiceTemplate({ body: opts.requestBody }),
    ),

  getDeployStatus: (opts: { taskId: string }) =>
    unwrap<T.ScriptDeployStatus>(
      Sdk.ScriptDeployService.scriptDeployGetDeployStatus({ path: { task_id: opts.taskId } }),
    ),

  cancelDeployment: (opts: { taskId: string }) =>
    unwrap<{ [key: string]: unknown }>(
      Sdk.ScriptDeployService.scriptDeployCancelDeployment({ path: { task_id: opts.taskId } }),
    ),

  registerDeployedResource: (opts: { taskId: string }) =>
    unwrap<{ [key: string]: unknown }>(
      Sdk.ScriptDeployService.scriptDeployRegisterDeployedResource({ path: { task_id: opts.taskId } }),
    ),
}

// ---------------------------------------------------------------------------
// AI template recommendation
// ---------------------------------------------------------------------------

export const AiTemplateRecommendationService = {
  chat: (opts: { requestBody: T.AppAiTemplateRecommendationSchemasChatRequest }) =>
    unwrap<T.AppAiTemplateRecommendationSchemasChatResponse>(
      Sdk.AiTemplateRecommendationService.aiTemplateRecommendationChat({ body: opts.requestBody }),
    ),

  recommend: (opts: { requestBody: T.AppAiTemplateRecommendationSchemasChatRequest }) =>
    unwrap<unknown>(
      Sdk.AiTemplateRecommendationService.aiTemplateRecommendationRecommend({ body: opts.requestBody }),
    ),
}

// ---------------------------------------------------------------------------
// Private (internal-only)
// ---------------------------------------------------------------------------

export const PrivateService = {
  createUser: (opts: { requestBody: T.PrivateUserCreate }) =>
    unwrap<T.UserPublic>(Sdk.PrivateService.privateCreateUser({ body: opts.requestBody })),
}

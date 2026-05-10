export const queryKeys = {
  auth: {
    currentUser: ["currentUser"] as const,
  },
  groups: {
    all: ["groups"] as const,
    detail: (groupId: string) => ["group", groupId] as const,
    batchJob: (jobId: string) => ["batch-job", jobId] as const,
  },
  users: {
    all: ["users"] as const,
  },
  resources: {
    all: ["resources"] as const,
    my: ["my-resources"] as const,
    templates: {
      lxc: ["lxc-templates"] as const,
      vm: ["vm-templates"] as const,
    },
    detail: (vmid: number) => ["resource", vmid] as const,
    config: (vmid: number) => ["resourceConfig", vmid] as const,
    snapshots: (vmid: number) => ["snapshots", vmid] as const,
    currentStats: (vmid: number) => ["currentStats", vmid] as const,
    rrdStats: (vmid: number, timeframe: string) =>
      ["rrdStats", vmid, timeframe] as const,
    auditLogs: (vmid: number) => ["auditLogs", vmid] as const,
    console: (vmid: number) => ["resource-console", vmid] as const,
  },
  auditLogs: {
    adminList: (query: unknown) => ["admin-audit-logs", query] as const,
    adminStats: (startDate: string, endDate: string) =>
      ["admin-audit-stats", startDate, endDate] as const,
    adminActions: ["admin-audit-actions"] as const,
    adminUsers: ["admin-audit-users"] as const,
  },
  vmRequests: {
    all: ["vm-requests"] as const,
    admin: ["vm-requests-admin"] as const,
    adminList: (status: string) => ["vm-requests-admin", status] as const,
    pendingCount: ["vm-requests-admin", "pending-count"] as const,
    detail: (requestId: string) => ["vm-request", requestId] as const,
    reviewContext: (requestId: string) =>
      ["vm-request-review-context", requestId] as const,
    availability: {
      all: ["vm-request-availability"] as const,
      draft: (draftKey: unknown) =>
        ["vm-request-availability", draftKey] as const,
      byRequest: (requestId: string) =>
        ["vm-request-availability", requestId] as const,
      window: (windowKey: unknown) =>
        ["vm-request-window-availability", windowKey] as const,
    },
  },
  aiApi: {
    all: ["ai-api"] as const,
    myRequests: ["ai-api", "my-requests"] as const,
    myCredentials: ["ai-api", "my-credentials"] as const,
    adminRequests: ["ai-api", "admin-requests"] as const,
    adminRequestsList: (status: string) =>
      ["ai-api", "admin-requests", status] as const,
    adminCredentials: ["ai-api", "admin-credentials"] as const,
    adminCredentialsList: (query: unknown) =>
      ["ai-api", "admin-credentials", query] as const,
    adminCredentialsCount: (status: string) =>
      ["ai-api", "admin-credentials", "count", status] as const,
  },
  gpu: {
    mappings: ["gpu-mappings"] as const,
    mapping: (id: string) => ["gpu-mapping", id] as const,
    options: (params?: { startAt?: string; endAt?: string }) =>
      ["gpu-options", params ?? {}] as const,
  },
  aiMonitoring: {
    stats: (params: unknown) => ["ai-monitoring", "stats", params] as const,
    apiCalls: (params: unknown) =>
      ["ai-monitoring", "api-calls", params] as const,
    templateCalls: (params: unknown) =>
      ["ai-monitoring", "template-calls", params] as const,
    usersUsage: (params: unknown) =>
      ["ai-monitoring", "users", params] as const,
    myProxyUsage: (params: unknown) =>
      ["ai-monitoring", "my-proxy", params] as const,
    myTemplateUsage: (params: unknown) =>
      ["ai-monitoring", "my-template", params] as const,
  },
} as const

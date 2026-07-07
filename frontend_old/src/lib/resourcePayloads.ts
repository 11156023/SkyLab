import type {
  LXCCreateRequest,
  VMCreateRequest,
  VMRequestCreate,
} from "@/client"

type ResourceType = "lxc" | "vm"
type RequestMode = "quick_template" | "immediate" | "scheduled"

type ResourceValidationMessages = {
  lxcRequirements: string
  vmRequirements: string
}

type ResourceEnvironmentOptions = {
  lxcEnvironmentType: string
  vmEnvironmentType: string
}

type ResourcePayloadOptions = ResourceEnvironmentOptions & {
  validationMessages: ResourceValidationMessages
}

type SharedResourceFormInput = {
  resource_type: ResourceType
  hostname: string
  cores: number
  memory: number
  password: string
  storage?: string
  os_info?: string
  ostemplate?: string
  rootfs_size?: number
  template_id?: number
  disk_size?: number
  username?: string
  gpu_mapping_id?: string
  expiry_date?: string
  service_template_slug?: string
  service_template_script_path?: string
}

type ApplicationRequestFormInput = SharedResourceFormInput & {
  reason: string
  mode?: RequestMode
  start_at?: string
  end_at?: string
  immediate_no_end?: boolean
}

export type VmRequestCreateRequestBody = Omit<
  VMRequestCreate,
  "mode" | "start_at" | "end_at"
> & {
  mode?: RequestMode
  start_at?: string
  end_at?: string
  gpu_mapping_id?: string
  service_template_slug?: string
  service_template_script_path?: string
}

type NormalizedLxcPayload = {
  resource_type: "lxc"
  hostname: string
  ostemplate: string
  cores: number
  memory: number
  rootfs_size: number
  password: string
  storage: string
  environment_type: string
  os_info?: string | null
  expiry_date?: string | null
  service_template_slug?: string | null
}

type NormalizedVmPayload = {
  resource_type: "vm"
  hostname: string
  template_id: number
  username: string
  password: string
  cores: number
  memory: number
  disk_size: number
  storage: string
  environment_type: string
  os_info?: string | null
  expiry_date?: string | null
  service_template_slug?: string | null
}

type NormalizedResourcePayload = NormalizedLxcPayload | NormalizedVmPayload

function trimToNull(value?: string) {
  const trimmed = value?.trim()
  return trimmed ? trimmed : null
}

function trimToUndefined(value?: string) {
  const trimmed = value?.trim()
  return trimmed ? trimmed : undefined
}

function getNormalizedResourcePayload(
  values: SharedResourceFormInput,
  options: ResourcePayloadOptions,
): NormalizedResourcePayload {
  const hostname = values.hostname.trim()
  const storage = values.storage?.trim() || "local-lvm"
  const os_info = trimToNull(values.os_info)
  const expiry_date = trimToNull(values.expiry_date)
  const service_template_slug = trimToNull(values.service_template_slug)

  if (values.resource_type === "lxc") {
    if (!values.ostemplate || !values.rootfs_size) {
      throw new Error(options.validationMessages.lxcRequirements)
    }

    return {
      resource_type: "lxc",
      hostname,
      ostemplate: values.ostemplate,
      cores: values.cores,
      memory: values.memory,
      rootfs_size: values.rootfs_size,
      password: values.password,
      storage,
      environment_type: options.lxcEnvironmentType,
      os_info,
      expiry_date,
      service_template_slug,
    }
  }

  const username = trimToUndefined(values.username)
  if (!values.template_id || !values.disk_size || !username) {
    throw new Error(options.validationMessages.vmRequirements)
  }

  return {
    resource_type: "vm",
    hostname,
    template_id: values.template_id,
    username,
    password: values.password,
    cores: values.cores,
    memory: values.memory,
    disk_size: values.disk_size,
    storage,
    environment_type: options.vmEnvironmentType,
    os_info,
    expiry_date,
    service_template_slug,
  }
}

function getRequestWindow(values: ApplicationRequestFormInput) {
  const mode = values.mode ?? "scheduled"

  if (mode === "quick_template") {
    return {
      mode,
      start_at: undefined,
      end_at: undefined,
    }
  }

  if (mode === "immediate") {
    return {
      mode,
      start_at: undefined,
      end_at: values.immediate_no_end
        ? undefined
        : trimToUndefined(values.end_at),
    }
  }

  return {
    mode,
    start_at: trimToUndefined(values.start_at),
    end_at: trimToUndefined(values.end_at),
  }
}

export function normalizeHostname(value: string) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}-]/gu, "-")
    .replace(/-+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 63)
}

export function toLxcCreateRequestBody(
  values: SharedResourceFormInput,
  options: ResourcePayloadOptions,
): LXCCreateRequest {
  const payload = getNormalizedResourcePayload(values, options)
  if (payload.resource_type !== "lxc") {
    throw new Error(options.validationMessages.lxcRequirements)
  }

  return {
    hostname: payload.hostname,
    ostemplate: payload.ostemplate,
    cores: payload.cores,
    memory: payload.memory,
    rootfs_size: payload.rootfs_size,
    password: payload.password,
    storage: payload.storage,
    environment_type: payload.environment_type,
    os_info: payload.os_info,
    expiry_date: payload.expiry_date,
    start: true,
    unprivileged: true,
    service_template_slug: payload.service_template_slug,
  }
}

export function toVmCreateRequestBody(
  values: SharedResourceFormInput,
  options: ResourcePayloadOptions,
): VMCreateRequest {
  const payload = getNormalizedResourcePayload(values, options)
  if (payload.resource_type !== "vm") {
    throw new Error(options.validationMessages.vmRequirements)
  }

  return {
    hostname: payload.hostname,
    template_id: payload.template_id,
    username: payload.username,
    password: payload.password,
    cores: payload.cores,
    memory: payload.memory,
    disk_size: payload.disk_size,
    storage: payload.storage,
    environment_type: payload.environment_type,
    os_info: payload.os_info,
    expiry_date: payload.expiry_date,
    start: true,
    service_template_slug: payload.service_template_slug,
  }
}

export function toVmRequestCreateRequestBody(
  values: ApplicationRequestFormInput,
  options: ResourcePayloadOptions,
): VmRequestCreateRequestBody {
  const payload = getNormalizedResourcePayload(values, options)
  const window = getRequestWindow(values)
  const common = {
    reason: values.reason.trim(),
    resource_type: payload.resource_type,
    hostname: payload.hostname,
    cores: payload.cores,
    memory: payload.memory,
    password: payload.password,
    storage: payload.storage,
    environment_type: payload.environment_type,
    os_info: payload.os_info,
    mode: window.mode,
    start_at: window.start_at,
    end_at: window.end_at,
    gpu_mapping_id: trimToUndefined(values.gpu_mapping_id),
    service_template_slug: trimToUndefined(values.service_template_slug),
    service_template_script_path: trimToUndefined(
      values.service_template_script_path,
    ),
  }

  if (payload.resource_type === "lxc") {
    return {
      ...common,
      ostemplate: payload.ostemplate,
      rootfs_size: payload.rootfs_size,
    }
  }

  return {
    ...common,
    template_id: payload.template_id,
    username: payload.username,
    disk_size: payload.disk_size,
  }
}

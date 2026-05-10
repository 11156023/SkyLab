import { describe, expect, it } from "vitest"

import {
  normalizeHostname,
  toLxcCreateRequestBody,
  toVmRequestCreateRequestBody,
} from "@/lib/resourcePayloads"

const options = {
  lxcEnvironmentType: "teaching",
  vmEnvironmentType: "vm-teaching",
  validationMessages: {
    lxcRequirements: "lxc required",
    vmRequirements: "vm required",
  },
}

describe("resource payload mappers", () => {
  it("normalizes hostnames safely", () => {
    expect(normalizeHostname("My App__Demo")).toBe("my-app-demo")
  })

  it("builds an lxc create payload without changing API field names", () => {
    expect(
      toLxcCreateRequestBody(
        {
          resource_type: "lxc",
          hostname: "demo-lxc",
          ostemplate: "ubuntu-24.04",
          cores: 2,
          memory: 2048,
          rootfs_size: 16,
          password: "password123",
          os_info: "Ubuntu",
        },
        options,
      ),
    ).toMatchObject({
      hostname: "demo-lxc",
      ostemplate: "ubuntu-24.04",
      rootfs_size: 16,
      environment_type: "teaching",
      start: true,
      unprivileged: true,
    })
  })

  it("builds a vm request payload with trimmed scheduled fields", () => {
    expect(
      toVmRequestCreateRequestBody(
        {
          resource_type: "vm",
          hostname: "demo-vm",
          template_id: 101,
          username: " student ",
          password: "password123",
          cores: 4,
          memory: 4096,
          disk_size: 64,
          reason: "Need a VM for software engineering coursework",
          mode: "scheduled",
          start_at: " 2026-04-12T08:00:00+08:00 ",
          end_at: " 2026-04-12T12:00:00+08:00 ",
        },
        options,
      ),
    ).toMatchObject({
      resource_type: "vm",
      template_id: 101,
      username: "student",
      mode: "scheduled",
      start_at: "2026-04-12T08:00:00+08:00",
      end_at: "2026-04-12T12:00:00+08:00",
      environment_type: "vm-teaching",
    })
  })

  it("builds a quick template request without client-controlled dates", () => {
    expect(
      toVmRequestCreateRequestBody(
        {
          resource_type: "lxc",
          hostname: "quick-pg",
          ostemplate: "ubuntu-24.04",
          cores: 2,
          memory: 2048,
          rootfs_size: 16,
          password: "password123",
          reason: "Need a short PostgreSQL lab environment",
          mode: "quick_template",
          start_at: "2026-04-12T08:00:00+08:00",
          end_at: "2026-04-12T12:00:00+08:00",
          service_template_slug: "postgresql",
        },
        options,
      ),
    ).toMatchObject({
      resource_type: "lxc",
      mode: "quick_template",
      start_at: undefined,
      end_at: undefined,
      service_template_slug: "postgresql",
    })
  })
})

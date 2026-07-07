import { describe, expect, it } from "vitest"

import { buildBatchProvisionRequestBody } from "@/features/groups/api"

describe("buildBatchProvisionRequestBody", () => {
  it("builds an lxc batch request body", () => {
    expect(
      buildBatchProvisionRequestBody({
        resourceType: "lxc",
        hostnamePrefix: " web-lab ",
        password: "password123",
        cores: 2,
        memory: 2048,
        rootfsSize: 16,
        diskSize: 0,
        ostemplate: "ubuntu-24.04",
        templateId: null,
        username: "",
        expiryDate: "2026-05-01",
      }),
    ).toEqual({
      resource_type: "lxc",
      hostname_prefix: "web-lab",
      password: "password123",
      cores: 2,
      memory: 2048,
      environment_type: "教學環境",
      expiry_date: "2026-05-01",
      ostemplate: "ubuntu-24.04",
      rootfs_size: 16,
    })
  })

  it("builds a vm batch request body", () => {
    expect(
      buildBatchProvisionRequestBody({
        resourceType: "qemu",
        hostnamePrefix: " vm-lab ",
        password: "password123",
        cores: 4,
        memory: 4096,
        rootfsSize: 0,
        diskSize: 64,
        ostemplate: "",
        templateId: 200,
        username: " student ",
        expiryDate: "",
      }),
    ).toEqual({
      resource_type: "qemu",
      hostname_prefix: "vm-lab",
      password: "password123",
      cores: 4,
      memory: 4096,
      environment_type: "教學環境",
      template_id: 200,
      username: "student",
      disk_size: 64,
    })
  })
})

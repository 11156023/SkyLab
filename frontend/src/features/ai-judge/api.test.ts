import { afterEach, describe, expect, it, vi } from "vitest"

import { OpenAPI } from "@/client"
import { request as requestMock } from "@/client/core/request"

import { AiJudgeService } from "./api"

vi.mock("@/client/core/request", () => ({
  request: vi.fn(),
}))

describe("AiJudgeService.downloadExcel", () => {
  const originalToken = OpenAPI.TOKEN
  const originalBase = OpenAPI.BASE

  afterEach(() => {
    OpenAPI.TOKEN = originalToken
    OpenAPI.BASE = originalBase
    vi.clearAllMocks()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it("sends template_key with rubric upload", async () => {
    const file = new File(["rubric"], "rubric.pdf", { type: "application/pdf" })
    vi.mocked(requestMock).mockReturnValue(Promise.resolve({}) as any)

    await AiJudgeService.uploadRubric(file, "n8n")

    expect(requestMock).toHaveBeenCalledWith(
      OpenAPI,
      expect.objectContaining({
        method: "POST",
        url: "/api/v1/rubric/upload",
        formData: { file, template_key: "n8n" },
      }),
    )
  })

  it("sends template_key with rubric chat", async () => {
    vi.mocked(requestMock).mockReturnValue(Promise.resolve({}) as any)

    await AiJudgeService.chat({
      messages: [{ role: "user", content: "請潤飾" }],
      rubric_context: "{}",
      template_key: "python",
    })

    expect(requestMock).toHaveBeenCalledWith(
      OpenAPI,
      expect.objectContaining({
        method: "POST",
        url: "/api/v1/rubric/chat",
        body: expect.objectContaining({ template_key: "python" }),
      }),
    )
  })

  it("creates teacher judge script artifacts under a group", async () => {
    vi.mocked(requestMock).mockReturnValue(Promise.resolve({}) as any)

    await AiJudgeService.createScript({
      groupId: "group-1",
      name: "rubric.pdf",
      template_key: "n8n",
      rubric_snapshot: {
        items: [],
        total_items: 0,
        checked_count: 0,
        auto_count: 0,
        partial_count: 0,
        manual_count: 0,
        summary: "",
        raw_text: "",
      },
    })

    expect(requestMock).toHaveBeenCalledWith(
      OpenAPI,
      expect.objectContaining({
        method: "POST",
        url: "/api/v1/groups/{groupId}/judge/scripts/",
        path: { groupId: "group-1" },
        body: expect.objectContaining({
          name: "rubric.pdf",
          template_key: "n8n",
        }),
      }),
    )
  })

  it("approves teacher judge script artifacts", async () => {
    vi.mocked(requestMock).mockReturnValue(Promise.resolve({}) as any)

    await AiJudgeService.approveScript({
      groupId: "group-1",
      scriptId: "script-1",
    })

    expect(requestMock).toHaveBeenCalledWith(
      OpenAPI,
      expect.objectContaining({
        method: "POST",
        url: "/api/v1/groups/{groupId}/judge/scripts/{scriptId}/approve",
        path: { groupId: "group-1", scriptId: "script-1" },
      }),
    )
  })

  it("creates teacher judge script runs with manual target vmids", async () => {
    vi.mocked(requestMock).mockReturnValue(Promise.resolve({}) as any)

    await AiJudgeService.createScriptRun({
      groupId: "group-1",
      scriptId: "script-1",
      target_vmids: [101, 102],
    })

    expect(requestMock).toHaveBeenCalledWith(
      OpenAPI,
      expect.objectContaining({
        method: "POST",
        url: "/api/v1/groups/{groupId}/judge/scripts/{scriptId}/runs",
        path: { groupId: "group-1", scriptId: "script-1" },
        body: {
          target_scope: "manual",
          target_vmids: [101, 102],
        },
      }),
    )
  })

  it("deletes teacher judge script artifacts", async () => {
    vi.mocked(requestMock).mockReturnValue(Promise.resolve(undefined) as any)

    await AiJudgeService.deleteScript({
      groupId: "group-1",
      scriptId: "script-1",
    })

    expect(requestMock).toHaveBeenCalledWith(
      OpenAPI,
      expect.objectContaining({
        method: "DELETE",
        url: "/api/v1/groups/{groupId}/judge/scripts/{scriptId}",
        path: { groupId: "group-1", scriptId: "script-1" },
      }),
    )
  })

  it("passes endpoint url to token resolver", async () => {
    const tokenResolver = vi.fn(async (options: { url: string }) => {
      expect(options.url).toBe("/api/v1/rubric/download-excel")
      return "token-123"
    })

    const expectedBlob = new Blob(["excel-content"])
    const blobFn = vi.fn().mockResolvedValue(expectedBlob)
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      blob: blobFn,
    })

    OpenAPI.TOKEN = tokenResolver as typeof OpenAPI.TOKEN
    OpenAPI.BASE = ""
    vi.stubGlobal("fetch", fetchMock)

    const result = await AiJudgeService.downloadExcel({
      items: [],
      summary: "test",
    })

    expect(result).toBe(expectedBlob)
    expect(tokenResolver).toHaveBeenCalledTimes(1)
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/rubric/download-excel",
      expect.objectContaining({
        method: "POST",
      }),
    )
  })
})

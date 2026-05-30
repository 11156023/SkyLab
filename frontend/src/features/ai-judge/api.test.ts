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

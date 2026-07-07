import {
  type ScriptDeployRequest,
  type ScriptDeployResponse,
  ScriptDeployService,
  type ScriptDeployStatus,
} from "@/client"

export const ScriptDeployApi = {
  deploy(data: {
    requestBody: ScriptDeployRequest
  }): Promise<ScriptDeployResponse> {
    return ScriptDeployService.deployServiceTemplate({
      requestBody: data.requestBody,
    })
  },

  getStatus(data: { taskId: string }): Promise<ScriptDeployStatus> {
    return ScriptDeployService.getDeployStatus({
      taskId: data.taskId,
    })
  },

  register(data: { taskId: string }): Promise<Record<string, unknown>> {
    return ScriptDeployService.registerDeployedResource({
      taskId: data.taskId,
    }) as Promise<Record<string, unknown>>
  },

  cancel(data: { taskId: string }): Promise<Record<string, unknown>> {
    // Cast: cancel endpoint may not yet exist on the regenerated client.
    const svc = ScriptDeployService as unknown as {
      cancelDeployment?: (args: { taskId: string }) => Promise<unknown>
    }
    if (typeof svc.cancelDeployment === "function") {
      return svc.cancelDeployment({ taskId: data.taskId }) as Promise<
        Record<string, unknown>
      >
    }
    // Fallback: hit the endpoint directly via fetch using the OpenAPI client base URL
    return fetch(`/api/v1/script-deploy/cancel/${data.taskId}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${localStorage.getItem("access_token") ?? ""}`,
      },
    }).then(async (r) => {
      if (!r.ok) throw new Error(`Cancel failed: ${r.status} ${await r.text()}`)
      return r.json()
    })
  },
}

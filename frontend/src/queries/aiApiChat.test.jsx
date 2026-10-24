// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { createTestQueryClient, withQueryClient } from "./testing";
import { aiApiChatKeys, apiKeyFingerprint, useAiModelsQuery } from "./aiApiChat";

const mocks = vi.hoisted(() => ({ listModels: vi.fn() }));
vi.mock("../services/aiApiChat", () => ({ AiApiChatService: { listModels: mocks.listModels } }));

let host;
let root;
let client;
let seen;

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

function render(ui) {
  return act(async () => { root.render(withQueryClient(ui, client)); });
}

/* useQuery 的結果是存取追蹤的：欄位要在 render 期間讀過，資料變了才會重繪 */
function Probe({ apiKey }) {
  const query = useAiModelsQuery(apiKey);
  seen = { data: query.data, isError: query.isError, error: query.error };
  return null;
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = createTestQueryClient();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("apiKeyFingerprint", () => {
  test("is stable per key, differs between keys and never contains the key", () => {
    const key = "ccai_super_secret_value";
    expect(apiKeyFingerprint(key)).toBe(apiKeyFingerprint(key));
    expect(apiKeyFingerprint(key)).not.toBe(apiKeyFingerprint("ccai_super_secret_valuf"));
    expect(apiKeyFingerprint(key)).not.toContain("secret");
    expect(JSON.stringify(aiApiChatKeys.models(key))).not.toContain(key);
    expect(aiApiChatKeys.models(null)).toEqual(["aiApiChat", "models", null]);
  });
});

describe("useAiModelsQuery", () => {
  test("does not ask for models without a key", async () => {
    await render(<Probe apiKey={null} />);
    await flush();
    expect(mocks.listModels).not.toHaveBeenCalled();
  });

  test("lists models with the given key and an abort signal, per key", async () => {
    mocks.listModels.mockImplementation(async ({ apiKey }) => (apiKey === "ccai_a" ? ["model-a"] : ["model-b"]));
    await render(<Probe apiKey="ccai_a" />);
    await flush();
    expect(mocks.listModels).toHaveBeenCalledWith({ signal: expect.any(AbortSignal), apiKey: "ccai_a" });
    expect(seen.data).toEqual(["model-a"]);

    await render(<Probe apiKey="ccai_b" />);
    await flush();
    expect(mocks.listModels).toHaveBeenLastCalledWith({ signal: expect.any(AbortSignal), apiKey: "ccai_b" });
    expect(seen.data).toEqual(["model-b"]);
  });

  test("unmounting aborts the request and drops the cached entry", async () => {
    mocks.listModels.mockImplementation(() => new Promise(() => {}));
    await render(<Probe apiKey="ccai_a" />);
    const { signal } = mocks.listModels.mock.calls[0][0];

    await render(<span />);
    await flush();
    expect(signal.aborted).toBe(true);
    expect(client.getQueryCache().find({ queryKey: aiApiChatKeys.models("ccai_a") })).toBeUndefined();
  });

  test("a failure keeps the service error for the caller to map", async () => {
    mocks.listModels.mockRejectedValue({ status: 503 });
    await render(<Probe apiKey="ccai_a" />);
    await flush();
    expect(seen.isError).toBe(true);
    expect(seen.error).toEqual({ status: 503 });
  });
});

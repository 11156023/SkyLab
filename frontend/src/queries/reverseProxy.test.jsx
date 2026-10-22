// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { withQueryClient } from "./testing";
import { usableZones, useReverseProxySetupContextQuery } from "./reverseProxy";

const mocks = vi.hoisted(() => ({ setupContext: vi.fn() }));
vi.mock("../services/reverseProxy", () => ({ ReverseProxyService: { setupContext: mocks.setupContext } }));

let host;
let root;
let seen;

function Probe({ enabled }) {
  const query = useReverseProxySetupContextQuery({ enabled });
  seen = { data: query.data, isError: query.isError };
  return null;
}

async function flush() {
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 0));
    for (let i = 0; i < 5; i += 1) await Promise.resolve();
  });
}

beforeEach(() => {
  vi.resetAllMocks();
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

describe("usableZones", () => {
  test("only an enabled context exposes its zones", () => {
    expect(usableZones({ enabled: true, zones: [{ id: "z1" }] })).toEqual([{ id: "z1" }]);
    expect(usableZones({ enabled: false, zones: [{ id: "z1" }] })).toEqual([]);
    expect(usableZones({ enabled: true })).toEqual([]);
    expect(usableZones(undefined)).toEqual([]);
  });
});

describe("useReverseProxySetupContextQuery", () => {
  test("loads the setup context", async () => {
    mocks.setupContext.mockResolvedValue({ enabled: true, zones: [] });
    await act(async () => { root.render(withQueryClient(<Probe />)); });
    await flush();
    expect(seen.data).toEqual({ enabled: true, zones: [] });
  });

  test("disabled does not call the API", async () => {
    await act(async () => { root.render(withQueryClient(<Probe enabled={false} />)); });
    await flush();
    expect(mocks.setupContext).not.toHaveBeenCalled();
    expect(seen.data).toBeUndefined();
  });
});

// @vitest-environment happy-dom

/*
 * 回歸：克隆對話框載不到 GPU 選項時，新版每次切回視窗（refetchOnWindowFocus 重抓）又失敗，就再跳一次錯誤 toast；
 * 舊版只在對話框開啟時抓一次、提示一次。
 *
 * 新版（pages/resource/templates/TemplateCloneDialog.jsx:49-56）：
 *   gpuFailure = needsGpu && gpuData === undefined && gpuQuery.isError ? gpuQuery.error : null
 *   useEffect(() => { if (gpuFailure) toast.error(...) }, [gpuFailure, ...])
 * 沒有資料時切回視窗會重抓（全站預設 refetchOnWindowFocus: true），重抓時 isError 變 false、失敗後是新的錯誤物件，
 * effect 又 toast 一次。TemplatesPage.jsx:46-52 的「使用手冊」對話框（附件清單）是同一個寫法。
 * 舊版（ce7675b9^ TemplateCloneDialog.jsx:60-71）只在 mount 的 effect 裡抓一次。
 */

import { act } from "react";
import { createRoot } from "react-dom/client";
import { focusManager } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import TemplateCloneDialog from "./TemplateCloneDialog";
import { createQueryClient } from "../../../queries/queryClient";
import { withQueryClient } from "../../../queries/testing";

const mocks = vi.hoisted(() => ({
  clone: vi.fn(),
  listOptions: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock("../../../services/templates", () => ({ TemplatesService: { clone: mocks.clone } }));
vi.mock("../../../services/gpu", () => ({ GpuService: { listOptions: mocks.listOptions } }));
vi.mock("../../../hooks/useToast", () => ({ useToast: () => mocks.toast }));
vi.mock("react-i18next", async (importOriginal) => ({
  ...await importOriginal(),
  useTranslation: () => ({ t: (key) => key }),
}));

const GPU_TEMPLATE = Object.freeze({
  id: "tpl-g",
  name: "CUDA",
  requires_gpu: true,
  resource_type: "qemu",
  node: "pve-a",
});

let host;
let root;

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

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
  focusManager.setFocused(undefined);
});

describe("TemplateCloneDialog：GPU 選項載不到", () => {
  test("只在開啟時提示一次，切回視窗不會再跳", async () => {
    /* 真實 API 每次失敗都是新的錯誤物件 */
    mocks.listOptions.mockImplementation(() => Promise.reject(new Error("PVE 連線失敗")));
    /* 用全站預設（切回視窗會重抓）的 QueryClient，與正式環境相同 */
    const client = createQueryClient({ queries: { gcTime: 0 } });

    await act(async () => {
      root.render(withQueryClient(<TemplateCloneDialog template={GPU_TEMPLATE} onClose={vi.fn()} />, client));
    });
    await flush();
    await flush();
    expect(mocks.listOptions).toHaveBeenCalledTimes(1);
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);

    /* 對話框開著，使用者切到別的視窗再切回來兩次 */
    for (let i = 0; i < 2; i += 1) {
      await act(async () => { focusManager.setFocused(false); });
      await act(async () => { focusManager.setFocused(true); });
      await flush();
      await flush();
    }
    expect(mocks.listOptions.mock.calls.length).toBeGreaterThanOrEqual(2);

    /* 舊版：只在開啟時抓一次、提示一次 */
    expect(mocks.toast.error).toHaveBeenCalledTimes(1);
  });
});

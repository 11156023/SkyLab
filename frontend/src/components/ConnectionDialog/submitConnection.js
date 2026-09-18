/**
 * submitConnection.js
 * ConnectionDialog 的送出流程：呼叫 API 並整理結果，不碰 UI。
 *
 * 成功與失敗都回傳同一種結構，呼叫端據此決定顯示什麼、要不要通知外部重新載入：
 *   { ok: true,  result: {...} }
 *   { ok: false, error: { key, text?, params? }, partialDone?: number }
 *
 * partialDone 是入站多筆發布途中失敗時「已經成功幾筆」，
 * 呼叫端要據此先通知外部刷新，否則畫面會看不到已經生效的那幾條。
 */

import {
  createConnection,
  createVmRule,
  publishService,
  replacePublishedService,
} from "../../services/firewall";

/** API 失敗一律優先顯示後端訊息，沒有才退回通用文案 */
const apiError = (err) => ({
  ok: false,
  error: { key: "ConnectionDialog.createFailed", text: err?.message },
});

export async function submitRule({ vmid, body }) {
  try {
    await createVmRule(vmid, body);
    return { ok: true, result: { kind: "rule", vmid } };
  } catch (err) {
    return apiError(err);
  }
}

export async function submitInbound({ vmid, publish, raw = [], service = null }) {
  /* 編輯既有發布：換掉那一條，不是新增 */
  if (service) {
    try {
      await replacePublishedService(
        vmid,
        { port: service.port, protocol: service.protocol },
        publish[0],
      );
      return { ok: true, result: { kind: "replace", vmid } };
    } catch (err) {
      return apiError(err);
    }
  }

  let done = 0;
  for (const payload of publish) {
    try {
      await publishService(vmid, payload);
    } catch (err) {
      return {
        ok: false,
        partialDone: done,
        error: {
          key: "ConnectionDialog.partialFailed",
          params: {
            done,
            port: `${payload.port}/${payload.protocol}`,
            message: err?.message ?? null,
          },
        },
      };
    }
    done += 1;
  }

  if (raw.length > 0) {
    try {
      await createConnection({
        source_vmid: null,
        target_vmid: vmid,
        ports: raw,
        direction: "one_way",
      });
    } catch (err) {
      return { ...apiError(err), partialDone: done };
    }
  }

  return { ok: true, result: { kind: "publish", vmid, count: done + raw.length } };
}

export async function submitEdge({ sourceVmid, targetVmid, ports, direction }) {
  try {
    await createConnection({
      source_vmid: sourceVmid,
      target_vmid: targetVmid,
      ports,
      direction,
    });
    return {
      ok: true,
      result: { kind: "connection", source_vmid: sourceVmid, target_vmid: targetVmid },
    };
  } catch (err) {
    return apiError(err);
  }
}

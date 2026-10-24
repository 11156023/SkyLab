import { useQuery, useQueryClient } from "@tanstack/react-query";

import { TemplatesService } from "../services/templates";
import { LIST_REFRESH_INTERVAL } from "./queryClient";

/** 有範本建立中／更新循環中時 4 秒追一次（原 TemplatesPage 的輪詢） */
export const TEMPLATE_BUSY_POLL_INTERVAL = 4_000;

export const templateKeys = Object.freeze({
  all: ["templates"],
  list: ["templates", "list"],
  catalog: ["templates", "catalog"],
  attachments: (templateId) => ["templates", "attachments", String(templateId)],
});

export function isTemplateBusy(template) {
  return template?.status === "creating" || template?.status === "updating";
}

/** 有 creating／updating 中的範本時 4 秒輪詢，否則 30 秒 */
export function templatesPollInterval(list) {
  return (Array.isArray(list) ? list : []).some(isTemplateBusy)
    ? TEMPLATE_BUSY_POLL_INTERVAL
    : LIST_REFRESH_INTERVAL;
}

/* 預設參數用模組層的函式（不寫成行內箭頭），React Compiler 才編得動這支 hook。
   最近一次失敗時退回 30 秒：失敗時快取仍是舊清單（有建立中的範本），照 4 秒重試會每 4 秒跳一次錯誤 */
function templatesRefetchInterval(query) {
  if (query.state.status === "error") return LIST_REFRESH_INTERVAL;
  return templatesPollInterval(query.state.data);
}

/** 快取存範本陣列（不是 API 的 `{ data }` 外殼），useTemplateCacheUpdater 才能就地改 */
async function fetchTemplates({ signal }) {
  const res = await TemplatesService.list({ signal });
  return res?.data ?? [];
}

/**
 * 老師的範本清單（範本頁）；data 是範本陣列。
 * 預設依清單內容調整輪詢：有建立中／更新中的 4 秒一次，否則 30 秒。
 */
export function useTemplatesQuery({ enabled = true, refetchInterval = templatesRefetchInterval, select } = {}) {
  return useQuery({
    queryKey: templateKeys.list,
    queryFn: fetchTemplates,
    enabled,
    refetchInterval,
    select,
  });
}

/** 申請表單可選的範本目錄 */
export function useTemplateCatalogQuery({ enabled = true } = {}) {
  return useQuery({
    queryKey: templateKeys.catalog,
    queryFn: ({ signal }) => TemplatesService.catalog({ signal }),
    enabled,
  });
}

async function fetchTemplateAttachments(templateId) {
  const res = await TemplatesService.listAttachments(templateId);
  return res?.data ?? [];
}

/** 範本附件（操作手冊）；data 是附件陣列，編輯對話框與操作手冊對話框共用同一份 */
export function useTemplateAttachmentsQuery(templateId, { enabled } = {}) {
  return useQuery({
    queryKey: templateKeys.attachments(templateId),
    queryFn: () => fetchTemplateAttachments(templateId),
    /* 沒指定時有 templateId 才抓（預設值不寫在參數裡，React Compiler 才編得動） */
    enabled: enabled ?? templateId != null,
  });
}

export function useTemplateCacheUpdater() {
  const queryClient = useQueryClient();

  /* 附件上傳／刪除後就地改快取。cancelQueries 要等它完成再寫：取消會把 query 還原成
     「這次 fetch 開始前」的狀態，沒等就先寫的話會被還原蓋掉。首載還沒回來時在途的那次
     可能沒有剛上傳的檔，取消後整份重抓 */
  const patchAttachments = async (templateId, mapper) => {
    const queryKey = templateKeys.attachments(templateId);
    await queryClient.cancelQueries({ queryKey });
    if (queryClient.getQueryData(queryKey) === undefined) {
      await queryClient.invalidateQueries({ queryKey });
      return;
    }
    queryClient.setQueryData(queryKey, (prev) => (Array.isArray(prev) ? mapper(prev) : prev));
  };

  return {
    update(updated) {
      queryClient.setQueryData(templateKeys.list, (prev) => (
        Array.isArray(prev) ? prev.map((t) => (t.id === updated.id ? updated : t)) : prev
      ));
    },
    /** 範本操作（更新循環、重試、刪除、建立／編輯存檔）後：清單、目錄與附件都重抓 */
    invalidate() {
      return queryClient.invalidateQueries({ queryKey: templateKeys.all });
    },
    invalidateAttachments(templateId) {
      return queryClient.invalidateQueries({ queryKey: templateKeys.attachments(templateId) });
    },
    addAttachment(templateId, created) {
      return patchAttachments(templateId, (list) => [...list, created]);
    },
    removeAttachment(templateId, attachmentId) {
      return patchAttachments(templateId, (list) => list.filter((a) => a.id !== attachmentId));
    },
  };
}

/**
 * AdvancedSettingsTab — 進階設定
 * 生命週期、防火牆、開機選項、登入憑證、標籤、共享轉移。
 * 對外發布（網址／對外 port／僅開放防火牆）走防火牆卡片的「新增規則」對話框裡的「連線」分頁，
 * 或拓撲頁；這裡不再有獨立的「對外服務」卡片。
 * 被分享的使用者只看得到生命週期與防火牆（唯讀）；擁有者層級的卡片要 can_manage。
 * 「轉成範本」不在這裡：老師／管理員從資源列表每列的「更多」選單操作。
 */

import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./ResourceDetailPage.module.scss";
import LoadingState from "../../../../components/LoadingState/LoadingState";
import ErrorState from "../../../../components/ErrorState/ErrorState";
import NotFoundState from "../../../../components/ErrorState/NotFoundState";
import { isNotFound } from "../../../../services/api";
import { ResourcesService } from "../../../../services/resources";
import LifecycleCard from "./advanced/LifecycleCard";
import FirewallCard from "./advanced/FirewallCard";
import BootOptionsCard from "./advanced/BootOptionsCard";
import CredentialsCard from "./advanced/CredentialsCard";
import SharingCard from "./advanced/SharingCard";

export default function AdvancedSettingsTab({ vmid, backTo }) {
  const { t } = useTranslation("personal");

  const [resource, setResource] = useState(null);
  const [error, setError] = useState(false);

  const loadResource = useCallback(async () => {
    try {
      setResource(await ResourcesService.get(vmid));
    } catch (e) {
      setError(e ?? true);
    }
  }, [vmid]);

  useEffect(() => {
    loadResource();
  }, [loadResource]);

  if (error) {
    /* 資源不存在時重試沒有意義，顯示「找不到」；其餘錯誤保留重試 */
    if (isNotFound(error)) return <NotFoundState />;
    return <ErrorState onRetry={() => { setError(false); loadResource(); }} />;
  }
  if (!resource) return <LoadingState />;

  const canManage = resource.can_manage !== false;
  const isShared = resource.access_role === "shared";

  return (
    <div className={styles.tabStack}>
      <div data-guide="resource-setting-lifecycle"><LifecycleCard vmid={vmid} resource={resource} canManage={canManage} onChanged={loadResource} /></div>

      <div data-guide="resource-setting-firewall">
        <FirewallCard
          vmid={vmid}
          canManage={canManage}
          publicUrls={resource.public_urls ?? []}
          onChanged={loadResource}
        />
      </div>

      {!isShared && <div data-guide="resource-setting-boot"><BootOptionsCard vmid={vmid} canManage={canManage} /></div>}

      {canManage && <div data-guide="resource-setting-credentials"><CredentialsCard vmid={vmid} canManage={canManage} /></div>}

      {canManage && resource.allocation_scope !== "teaching_class" && (
        <div data-guide="resource-setting-sharing"><SharingCard vmid={vmid} resource={resource} canManage={canManage} backTo={backTo} /></div>
      )}
    </div>
  );
}

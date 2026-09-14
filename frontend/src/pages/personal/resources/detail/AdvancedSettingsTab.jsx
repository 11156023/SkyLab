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
import { ResourcesService } from "../../../../services/resources";
import LifecycleCard from "./advanced/LifecycleCard";
import FirewallCard from "./advanced/FirewallCard";
import BootOptionsCard from "./advanced/BootOptionsCard";
import CredentialsCard from "./advanced/CredentialsCard";
import MetadataCard from "./advanced/MetadataCard";
import SharingCard from "./advanced/SharingCard";

export default function AdvancedSettingsTab({ vmid, backTo }) {
  const { t } = useTranslation("personal");

  const [resource, setResource] = useState(null);
  const [error, setError] = useState(false);

  const loadResource = useCallback(async () => {
    try {
      setResource(await ResourcesService.get(vmid));
    } catch {
      setError(true);
    }
  }, [vmid]);

  useEffect(() => {
    loadResource();
  }, [loadResource]);

  if (error) return <p className={styles.stateText}>{t("AdvancedSettingsTab.loadFailed")}</p>;
  if (!resource) return <LoadingState />;

  const canManage = resource.can_manage !== false;
  const isShared = resource.access_role === "shared";

  return (
    <div className={styles.tabStack}>
      <LifecycleCard vmid={vmid} resource={resource} canManage={canManage} onChanged={loadResource} />

      <FirewallCard vmid={vmid} canManage={canManage} />

      {!isShared && <BootOptionsCard vmid={vmid} canManage={canManage} />}

      {canManage && <CredentialsCard vmid={vmid} canManage={canManage} />}

      {!isShared && <MetadataCard vmid={vmid} canManage={canManage} onChanged={loadResource} />}

      {canManage && resource.allocation_scope !== "teaching_class" && (
        <SharingCard vmid={vmid} resource={resource} canManage={canManage} backTo={backTo} />
      )}
    </div>
  );
}

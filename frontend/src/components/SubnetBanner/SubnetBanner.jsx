/**
 * SubnetBanner
 * 子網尚未配置時的全站警告橫幅：VM/LXC 建立功能已停用，
 * 管理員附「前往設定」連結。已配置或尚未取得狀態時不顯示。
 */
import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { useAuth } from "../../contexts/AuthContext";
import { ipManagementKeys, useIpStatusQuery } from "../../queries/ipManagement";
import { isAdminUser } from "../../utils/roles";
import styles from "./SubnetBanner.module.scss";

/** 子網設定存檔／刪除成功後廣播的視窗事件，橫幅收到就立即重查狀態。
 *  字串必須與寫入端（services/ipManagement.js 的 upsertSubnet／deleteSubnet）發出的一致。 */
export const SUBNET_CHANGED_EVENT = "skylab:subnet-changed";

/** 換頁時距上次查詢不足這個間隔就不重查（每個使用者每次換頁都打一次太多） */
const ROUTE_REFRESH_MIN_GAP_MS = 60_000;

export default function SubnetBanner() {
  const { t } = useTranslation("common");
  const { user } = useAuth();
  const { pathname } = useLocation();
  const isAdmin = isAdminUser(user);
  const queryClient = useQueryClient();
  /* 狀態走 TanStack Query（與 IP 管理頁共用快取）：不背景輪詢；60 秒內的結果視為新鮮，
     切回分頁也只在超過 60 秒才重查。查詢失敗時沿用上一次的狀態，從沒取到就不顯示（避免誤報） */
  const statusQuery = useIpStatusQuery({ refetchInterval: false, staleTime: ROUTE_REFRESH_MIN_GAP_MS });
  const status = statusQuery.data;
  const { refetch } = statusQuery;

  /* 橫幅掛在 DashboardLayout、跨頁不重掛；只在掛載時查一次的話，
     管理員設定或刪除子網後要整頁重新整理才會更新。
     因此換頁時重查（距上次不足 60 秒則略過；其他人改了子網也跟得上）。
     查詢進行中就不重發：換頁時進行中的查詢照樣收下（例如 / 立刻轉址到 /dashboard）。 */
  useEffect(() => {
    const state = queryClient.getQueryState(ipManagementKeys.status);
    if (!state || state.fetchStatus === "fetching") return;
    const lastCheckedAt = Math.max(state.dataUpdatedAt, state.errorUpdatedAt);
    if (Date.now() - lastCheckedAt >= ROUTE_REFRESH_MIN_GAP_MS) refetch();
  }, [pathname, queryClient, refetch]);

  /* 收到 SUBNET_CHANGED_EVENT 時立即重查（管理員在設定頁存檔／刪除當下）；
     refetch 會取消還在路上的舊查詢，只讓較新的結果蓋過去 */
  useEffect(() => {
    const onSubnetChanged = () => {
      refetch();
    };
    window.addEventListener(SUBNET_CHANGED_EVENT, onSubnetChanged);
    return () => window.removeEventListener(SUBNET_CHANGED_EVENT, onSubnetChanged);
  }, [refetch]);

  if (!status || status.configured) return null;

  return (
    <div className={styles.banner}>
      <MIcon name="warning_amber" size={16} />
      <span className={styles.text}>
        {isAdmin ? t("SubnetBanner.adminMessage") : t("SubnetBanner.userMessage")}
        {isAdmin && (
          <Link to="/ip-management" className={styles.link}>
            {t("SubnetBanner.goToSettings")}
          </Link>
        )}
      </span>
    </div>
  );
}

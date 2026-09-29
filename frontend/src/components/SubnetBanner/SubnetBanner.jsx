/**
 * SubnetBanner
 * 子網尚未配置時的全站警告橫幅：VM/LXC 建立功能已停用，
 * 管理員附「前往設定」連結。已配置或尚未取得狀態時不顯示。
 */
import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { useAuth } from "../../contexts/AuthContext";
import { IpManagementService } from "../../services/ipManagement";
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
  const [status, setStatus] = useState(null);
  const isAdmin = isAdminUser(user);
  const lastFetchedAtRef = useRef(0);

  /* 橫幅掛在 DashboardLayout、跨頁不重掛；只在掛載時查一次的話，
     管理員設定或刪除子網後要整頁重新整理才會更新。
     因此換頁時重查（距上次不足 60 秒則略過；其他人改了子網也跟得上），
     另外收到 SUBNET_CHANGED_EVENT 時立即重查（管理員在設定頁存檔／刪除當下）。 */
  useEffect(() => {
    let cancelled = false;
    let seq = 0;
    const refresh = () => {
      lastFetchedAtRef.current = Date.now();
      const current = ++seq;
      IpManagementService.getStatus()
        .then((res) => {
          if (!cancelled && current === seq) setStatus(res);
        })
        .catch(() => {
          // 取不到狀態就不顯示，避免誤報
        });
    };
    if (Date.now() - lastFetchedAtRef.current >= ROUTE_REFRESH_MIN_GAP_MS) refresh();
    window.addEventListener(SUBNET_CHANGED_EVENT, refresh);
    return () => {
      cancelled = true;
      window.removeEventListener(SUBNET_CHANGED_EVENT, refresh);
    };
  }, [pathname]);

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

import { useEffect, useRef, useState } from "react";
import NotFoundPage from "../../not-found/NotFoundPage";
import TotpEnrollment from "../../../components/TotpEnrollment/TotpEnrollment";
import CrashState from "../../../components/ErrorState/CrashState";
import ConnectionLostState from "../../../components/ErrorState/ConnectionLostState";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import { useToast } from "../../../hooks/useToast";
import { Demo, RuleList, Section } from "../DevSection";
import { DEMO_CODE, failingTotpService, fakeTotpService, pendingTotpService } from "../fakeTotpService";
import styles from "../DevStylePage.module.scss";

export function NotFoundSection() {
  return (
    <Section
      id="notfound"
      title="404 找不到頁面"
      refs={["pages/not-found/NotFoundPage.jsx", "App.jsx <Route path=\"*\">"]}
      intro="登入後開到不存在的路徑會落到這裡，不再靜默導回儀表板。紙飛機沿航線飛、虛線航跡跟著浮現，停留後淡出重飛（9 秒一輪，SMIL）；雲朵純白不隨主題（規範明列的例外）。「回到首頁」是真按鈕，會離開本頁。"
    >
      <div className={styles.flowFrame}>
        <NotFoundPage />
      </div>
    </Section>
  );
}

const TOTP_STATES = [
  { value: "ready", label: "正常（QR）" },
  { value: "pending", label: "產生中" },
  { value: "failed", label: "取金鑰失敗" },
];
const TOTP_SERVICES = { ready: fakeTotpService, pending: pendingTotpService, failed: failingTotpService };

export function TotpSection() {
  const toast = useToast();
  const [state, setState] = useState("ready");
  return (
    <Section
      id="totp"
      title="兩步驟驗證綁定 TotpEnrollment"
      refs={["components/TotpEnrollment/TotpEnrollment.jsx", "service prop（本頁注入 pages/dev/fakeTotpService）", "qrcode → QR 在瀏覽器端產生"]}
      intro={`帳號設定的對話框與「強制綁定」畫面共用同一個元件。這裡注入假服務，不會替任何帳號建立待確認的金鑰；驗證碼輸入 ${DEMO_CODE} 會成功，其餘顯示「驗證碼不正確」。`}
    >
      <SegmentedControl ariaLabel="TotpEnrollment 狀態" options={TOTP_STATES} value={state} onChange={setState} />
      <div className={styles.totpCell}>
        <TotpEnrollment
          key={state}
          service={TOTP_SERVICES[state]}
          onConfirmed={() => toast.success("示範：兩步驟驗證已啟用（假服務）")}
          onCancel={() => toast.info("示範：取消")}
        />
      </div>
      <RuleList
        items={[
          "失敗態用 ErrorState＋同一顆取消鈕，不顯示 API 原始訊息。",
          "驗證碼欄 44px 是登入／TOTP 的例外高度；送出鈕以 form 屬性綁回表單，交給 Modal 的固定按鈕列也送得出去。",
        ]}
      />
    </Section>
  );
}

export function CrashSection() {
  const error = new Error("Cannot read properties of undefined (reading 'map')");
  error.stack = `TypeError: ${error.message}\n    at ClassList (ClassManagementPage.jsx:142:31)\n    at renderWithHooks (react-dom.development.js:15486:18)`;
  return (
    <Section
      id="crash"
      title="畫面當掉 CrashState"
      refs={["components/ErrorState/CrashState.jsx", "components/ErrorBoundary/ErrorBoundary.jsx", "<ErrorBoundary fullPage>"]}
      intro="ErrorBoundary 攔到 render 錯誤時顯示。插圖是一塊介面脫落掉下、留下紅色虛線缺口再彈回（6 秒一輪）。只留「重新整理頁面」一顆主鈕（真的會重新整理）；錯誤資訊預設收合、單列＋圖示複製鈕。"
    >
      <div className={styles.flowFrame}>
        <CrashState error={error} componentStack={"\n    at ClassList\n    at ClassManagementPage\n    at DashboardLayout"} />
      </div>
    </Section>
  );
}

export function ConnectionLostSection() {
  const [retrying, setRetrying] = useState(false);
  const timerRef = useRef(null);
  useEffect(() => () => window.clearTimeout(timerRef.current), []);
  function retry() {
    setRetrying(true);
    timerRef.current = window.setTimeout(() => setRetrying(false), 3000);
  }
  return (
    <Section
      id="connectionlost"
      title="暫時無法連線 ConnectionLostState"
      refs={["components/ErrorState/ConnectionLostState.jsx", "App.jsx AuthBootstrapState unavailable", "retrying / fullPage"]}
      intro="登入前的服務檢查失敗、後端沒回應時顯示。待機時插頭每 6 秒往插座推一次、碰到閃紅色火花又彈開；按「重新連線」插頭接上、線上跑主色流動虛線（這裡模擬 3 秒後失敗彈回）。"
    >
      <div className={styles.flowFrame}>
        <ConnectionLostState retrying={retrying} onRetry={retry} />
      </div>
    </Section>
  );
}

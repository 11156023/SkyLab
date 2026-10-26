import { useState } from "react";
import MIcon from "../../../components/MIcon";
import EmptyState from "../../../components/EmptyState/EmptyState";
import ErrorState from "../../../components/ErrorState/ErrorState";
import NotFoundState from "../../../components/ErrorState/NotFoundState";
import LoadingState, { LoadingSpinner } from "../../../components/LoadingState/LoadingState";
import Modal from "../../../components/Modal/Modal";
import PageHeader from "../../../components/PageHeader/PageHeader";
import OctoMark from "../../../components/OctoPet/OctoMark";
import OctoAvatar from "../../../components/OctoPet/OctoAvatar";
import RotatingWelcome from "../../../components/RotatingWelcome/RotatingWelcome";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useToast } from "../../../hooks/useToast";
import { Demo, RuleList, Section } from "../DevSection";
import styles from "../DevStylePage.module.scss";

export function EmptyStateSection() {
  const toast = useToast();
  return (
    <Section
      id="emptystate"
      title="空狀態 Empty / Error / NotFound"
      refs={["components/EmptyState/EmptyState.jsx", "components/ErrorState/ErrorState.jsx", "components/ErrorState/NotFoundState.jsx"]}
      intro="空狀態一律 EmptyState（置中圖示＋一行灰字）；有下一步才帶動作鈕。載入失敗用 ErrorState（文案固定，重試是全站唯一的手動刷新）；API 回 404 用 NotFoundState（重試沒有意義，所以沒有按鈕）。"
    >
      <div className={styles.emptyGrid}>
        <div className={styles.emptyCell}>
          <EmptyState
            icon="school"
            title="還沒有班級"
            description="建立第一個班級後，學生名單與上課環境都會在這裡。"
            action={<button type="button" className={styles.btnPrimary} onClick={() => toast.info("示範：新增班級")}><MIcon name="add" size={16} />新增班級</button>}
          />
        </div>
        <div className={styles.emptyCell}>
          <ErrorState onRetry={() => toast.info("示範：重試")} />
        </div>
        <div className={styles.emptyCell}>
          <NotFoundState />
        </div>
      </div>
      <RuleList
        items={[
          "純資訊的空狀態不加鈕（例如學生首頁）；頁首同一顆「新增」鈕等有資料才出現，避免重複。",
          "EmptyState 會 flex: 1，放在 flex-column 的父容器裡才會垂直置中。",
        ]}
      />
    </Section>
  );
}

const WIDTHS = [["sm", 400, "確認框／命名框"], ["log", 560, "錯誤 log 等寬內容"], ["md", 640, "小型單欄表單"], ["lg", 1100, "一般"], ["xl", 1280, "寬版（VNC）"]];

export function ModalSection() {
  const confirm = useConfirm();
  const toast = useToast();
  const [compact, setCompact] = useState(false);
  const [form, setForm] = useState(false);
  const [bare, setBare] = useState(false);
  const [saving, setSaving] = useState(false);
  const compactPresence = useDialogPresence(compact);
  const formPresence = useDialogPresence(form);
  const barePresence = useDialogPresence(bare);

  async function askConfirm(danger) {
    const ok = await confirm({
      title: danger ? "刪除連線" : "重新啟動機器",
      message: danger
        ? "確定要刪除 pve-node-01 的連線？底下的機器會一起從列表移除，這個動作無法復原。"
        : "機器 web-01 會立刻重新啟動，未儲存的工作會遺失。",
      confirmText: danger ? "刪除" : "重新啟動",
      danger,
    });
    toast[ok ? "success" : "info"](ok ? "示範：已確認" : "示範：已取消");
  }

  function submitForm(event) {
    event.preventDefault();
    setSaving(true);
    window.setTimeout(() => {
      setSaving(false);
      setForm(false);
      toast.success("示範：已儲存");
    }, 1200);
  }

  return (
    <Section
      id="modal"
      title="對話框 Confirm / Modal"
      refs={["components/ConfirmDialog/ConfirmProvider.jsx → useConfirm()", "components/Modal/Modal.jsx", "hooks/useDialogPresence", "size: sm | log | md | lg | xl", "closeButton / dividers / bare / layer=\"screen\""]}
      intro="危險操作／二選一確認一律 useConfirm()；新對話框一律用共用 Modal（portal 到 body、遮罩與進出場、Esc／點遮罩、焦點鎖、捲動鎖都在裡面）。按下確認後彈窗即關閉，進度用按鈕 disabled＋toast 呈現。"
    >
      <div className={styles.demoGrid}>
        <Demo label="確認彈窗 useConfirm" refs={["{ title, message, confirmText, danger }"]} note="一般帶 help 圖示（pending 色）、danger 帶 warning 紅圖示＋紅色確認鈕；焦點落在確認鈕，Enter 確認、Esc 取消。">
          <div className={styles.row}>
            <button type="button" className={styles.btnSecondary} onClick={() => askConfirm(false)}>一般確認</button>
            <button type="button" className={styles.btnDangerOutline} onClick={() => askConfirm(true)}>危險確認</button>
          </div>
        </Demo>
        <Demo label="Modal 三版型" refs={["精簡卡（預設）", "closeButton", "bare"]} note="精簡卡：確認框、命名框、小表單；closeButton：欄位多的表單，標題列帶 ×、內容區自己捲、按鈕列固定；bare：只給外框，終端機、VNC 這類畫面自己排。">
          <div className={styles.row}>
            <button type="button" className={styles.btnSecondary} onClick={() => setCompact(true)}>精簡卡 sm</button>
            <button type="button" className={styles.btnSecondary} onClick={() => setForm(true)}>表單 closeButton md</button>
            <button type="button" className={styles.btnSecondary} onClick={() => setBare(true)}>bare log</button>
          </div>
        </Demo>
        <Demo label="寬度五級" refs={["Modal.module.scss .sizeSm … .sizeXl"]} wide>
          <div className={styles.widthScale}>
            {WIDTHS.map(([key, px, note]) => (
              <div key={key} className={styles.widthBar} style={{ width: `min(100%, ${px / 12.8}%)` }}>
                size="{key}" · {px}px · {note}
              </div>
            ))}
          </div>
        </Demo>
      </div>
      <RuleList
        items={[
          "遮罩 fixed inset 0、var(--color-overlay)、blur(4px)、z-index 300；一律 createPortal 到 body（祖先有 backdrop-filter 會困住 fixed）。",
          "有 AI 助手面板時遮罩要 @include dialog-overlay-avoid-ai；終端機、VNC 用 layer=\"screen\" 直接蓋過助手、Esc 不關。",
          "進場遮罩 fadeIn 0.15s＋卡片 slideUp 0.18s；離場由 useDialogPresence 保留 DOM 150ms 播淡出。",
        ]}
      />

      {compactPresence.open && (
        <Modal
          closing={compactPresence.closing}
          onClose={() => setCompact(false)}
          title="重新命名"
          description="名稱只用在列表顯示，不影響機器本身。"
          actions={(
            <>
              <button type="button" className={styles.btnSecondary} onClick={() => setCompact(false)}>取消</button>
              <button type="button" className={styles.btnPrimary} onClick={() => { setCompact(false); toast.success("示範：已重新命名"); }}>儲存</button>
            </>
          )}
        >
          <label className={styles.field}>
            <span>新名稱</span>
            <input defaultValue="web-01" autoFocus />
          </label>
        </Modal>
      )}

      {formPresence.open && (
        <Modal
          as="form"
          onSubmit={submitForm}
          size="md"
          closeButton
          closing={formPresence.closing}
          onClose={() => setForm(false)}
          busy={saving}
          title="新增連線"
          description="送出中 Esc、點遮罩與 × 都不會關（busy）。"
          actions={(
            <>
              <button type="button" className={styles.btnSecondary} onClick={() => setForm(false)} disabled={saving}>取消</button>
              <button type="submit" className={styles.btnPrimary} disabled={saving}>{saving ? "儲存中…" : "儲存"}</button>
            </>
          )}
        >
          <div className={styles.formGrid}>
            <label className={styles.field}><span>名稱</span><input placeholder="pve-node-02" autoFocus /></label>
            <label className={styles.field}><span>主機</span><input placeholder="192.168.100.12" /></label>
            <label className={styles.field}><span>連接埠</span><input type="number" defaultValue={8006} /></label>
            <label className={styles.field}>
              <span>驗證方式</span>
              <select defaultValue="token"><option value="token">API Token</option><option value="password">帳號密碼</option></select>
            </label>
            <label className={`${styles.field} ${styles.fieldFull}`}><span>備註</span><textarea rows={3} /></label>
          </div>
        </Modal>
      )}

      {barePresence.open && (
        <Modal bare size="log" closing={barePresence.closing} onClose={() => setBare(false)} aria-label="bare 版型示範">
          <div className={styles.bareBody}>
            <div className={styles.row} style={{ justifyContent: "space-between" }}>
              <span className={styles.cardTitle}>bare：外框以外全部自己排</span>
              <button type="button" className={styles.dialogClose} aria-label="關閉" onClick={() => setBare(false)}><MIcon name="close" size={20} /></button>
            </div>
            <pre className={styles.preBlock}>{"[2026-10-26 09:12:03] task started\n[2026-10-26 09:12:08] cloning template 9001 → 1042\n[2026-10-26 09:12:41] done"}</pre>
            <p className={styles.caption}>size=&quot;log&quot;（560px）給錯誤 log 等寬內容；終端機／VNC 另加 layer=&quot;screen&quot;。</p>
          </div>
        </Modal>
      )}
    </Section>
  );
}

export function ToastSection() {
  const toast = useToast();
  return (
    <Section
      id="toast"
      title="通知 Toast"
      refs={["hooks/useToast → { success, error, warning, info }", "components/AppToaster.jsx（sonner）", "global.scss [data-sonner-toast] 色票"]}
      intro="操作回饋一律 useToast，不放 inline message；四種 toast 的色票由 _themes.scss 的語意色衍生（warning 對應 --color-pending）。輪詢錯誤用 ref 防轟炸。"
    >
      <Demo label="四種型別" plain>
        <div className={styles.row}>
          <button type="button" className={styles.btnSecondary} onClick={() => toast.success("已儲存設定")}>success</button>
          <button type="button" className={styles.btnSecondary} onClick={() => toast.error("連線失敗，請稍後再試")}>error</button>
          <button type="button" className={styles.btnSecondary} onClick={() => toast.warning("配額即將用完")}>warning</button>
          <button type="button" className={styles.btnSecondary} onClick={() => toast.info("背景任務已開始")}>info</button>
        </div>
      </Demo>
    </Section>
  );
}

export function LoadingSection() {
  return (
    <Section
      id="loading"
      title="載入狀態 Loading"
      refs={["components/LoadingState/LoadingState.jsx", "<LoadingState text fullPage />", "<LoadingSpinner size />"]}
      intro="載入狀態一律用 LoadingState（3D 方塊堆疊）；自帶文案排版的容器用純 LoadingSpinner。學生端的 skeleton／自製動畫已清掉，勿恢復。"
    >
      <div className={styles.demoGrid}>
        <Demo label="LoadingState（預設文字）" refs={["role=status aria-live=polite"]}>
          <LoadingState />
        </Demo>
        <Demo label="自訂文字" refs={["text"]}>
          <LoadingState text="正在載入班級資料…" />
        </Demo>
        <Demo label="LoadingSpinner 尺寸" refs={["size=28 / 44 / 60（預設 60）"]} note="以 200px 原始設計等比縮放（zoom）。FileDropzone 上傳中用 28／44，登入驗證卡用 42。">
          <div className={styles.spinnerRow}>
            {[28, 44, 60].map((size) => (
              <div key={size} className={styles.spinnerCell}>
                <LoadingSpinner size={size} />
                <span>{size}</span>
              </div>
            ))}
          </div>
        </Demo>
      </div>
    </Section>
  );
}

export function MiscSection() {
  const toast = useToast();
  return (
    <Section
      id="misc"
      title="其他共用元件 Misc"
      refs={["components/PageHeader/PageHeader.jsx", "components/OctoPet/OctoMark.jsx", "components/OctoPet/OctoAvatar.jsx", "components/RotatingWelcome/RotatingWelcome.jsx"]}
      intro="PageHeader 是每頁的標題列（eyebrow／h1／副標＋右側動作）；章魚學士是品牌吉祥物（側欄標誌、AI 助手頭像），帽子色走 CSS 變數跟深色模式。"
    >
      <div className={styles.demoGrid}>
        <Demo label="PageHeader" refs={["eyebrow", "title", "subtitle", "leading", "children → 動作區"]} wide plain>
          <PageHeader eyebrow="課務管理" title="班級管理" subtitle="12 個班級 · 2026 秋季">
            <button type="button" className={styles.btnSecondary} onClick={() => toast.info("示範")}>匯入</button>
            <button type="button" className={styles.btnPrimary} onClick={() => toast.info("示範")}><MIcon name="add" size={16} />新增班級</button>
          </PageHeader>
        </Demo>
        <Demo label="章魚學士 OctoMark／OctoAvatar" refs={["size=28（每格 2px）", "cell=2 / 3"]} note="像素要整數倍才不會糊；頭像由外層圓框裁切。">
          <div className={styles.row}>
            <OctoMark size={28} label="SkyLab" />
            <OctoMark size={56} />
            <span className={styles.avatarFrame}><OctoAvatar cell={2} /></span>
            <span className={styles.avatarFrame}><OctoAvatar cell={3} /></span>
          </div>
        </Demo>
        <Demo label="歡迎語輪播 RotatingWelcome" refs={["i18nKey", "ns=login"]} note="依序用每種支援語言顯示同一句；減少動態效果時停在目前語言。">
          <RotatingWelcome className={styles.welcome} i18nKey="OnboardingPage.welcomeTitle" />
        </Demo>
      </div>
    </Section>
  );
}

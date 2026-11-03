import { useState } from "react";
import MIcon from "../../../components/MIcon";
import PasswordInput from "../../../components/PasswordInput/PasswordInput";
import PasswordRules from "../../../components/PasswordRules/PasswordRules";
import NumberInput from "../../../components/NumberInput/NumberInput";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import Switch from "../../../components/Switch/Switch";
import Stepper from "../../../components/Stepper/Stepper";
import ToolbarSearch from "../../../components/DataToolbar/ToolbarSearch";
import MachineKindBadge from "../../../components/MachineKindBadge/MachineKindBadge";
import { useToast } from "../../../hooks/useToast";
import { Demo, RuleList, Section } from "../DevSection";
import styles from "../DevStylePage.module.scss";

export function ButtonsSection() {
  const toast = useToast();
  const [pinned, setPinned] = useState(true);
  const [spinning, setSpinning] = useState(true);
  const click = () => toast.info("按鈕示範：已點擊");
  return (
    <Section
      id="buttons"
      title="按鈕 Buttons"
      refs={["frontend/src/assets/styles/_mixins.scss", "@include btn-primary / btn-secondary / btn-danger / btn-danger-outline / btn-ghost / btn-icon", "@include btn-icon-secondary", "@include btn-dialog-close"]}
      intro="全站按鈕一律 @include btn-*，不在頁面重抄整組。基底高 36px（行高 22.4＋內距 6×2＋1px 透明框×2）、圓角 8、字級 14/500；hover 一律 :not(:disabled)，disabled 一律 opacity .5。"
    >
      <div className={styles.demoGrid}>
        <Demo label="六款" refs={["btn-primary", "btn-secondary", "btn-danger", "btn-danger-outline", "btn-ghost", "btn-ghost($danger: true)"]} wide>
          <div className={styles.row}>
            <button type="button" className={styles.btnPrimary} onClick={click}><MIcon name="add" size={16} />主要動作</button>
            <button type="button" className={styles.btnSecondary} onClick={click}>一般動作</button>
            <button type="button" className={styles.btnDanger} onClick={click}>確認刪除</button>
            <button type="button" className={styles.btnDangerOutline} onClick={click}><MIcon name="delete_outline" size={16} />刪除</button>
            <button type="button" className={styles.btnGhost} onClick={click}>輔助動作</button>
            <button type="button" className={styles.btnGhostDanger} onClick={click}>移除</button>
          </div>
          <div className={styles.row}>
            <button type="button" className={styles.btnPrimary} disabled>主要動作</button>
            <button type="button" className={styles.btnSecondary} disabled>一般動作</button>
            <button type="button" className={styles.btnDanger} disabled>確認刪除</button>
            <button type="button" className={styles.btnDangerOutline} disabled>刪除</button>
            <button type="button" className={styles.btnGhost} disabled>輔助動作</button>
            <button type="button" className={styles.btnGhostDanger} disabled>移除</button>
          </div>
          <RuleList
            items={[
              "primary 每個畫面至多一顆；danger 只用在確認彈窗／最終確認步驟；頁面內破壞性動作的入口用 danger-outline，點了才進 useConfirm。",
              "頁面差異（寬度、margin、grid 位置）寫在 @include 之後；覆寫 padding 要把 1px 框算進去。",
            ]}
          />
        </Demo>
        <Demo label="圖示鈕 32×32" refs={["btn-icon", "btn-icon($danger: true)", "btn-icon-secondary", "btn-dialog-close"]} note="JSX 必帶 aria-label。表格列的更多操作鈕一律 btn-icon-secondary＋more_vert；btn-dialog-close 只給 Dialog 右上角。">
          <div className={styles.row}>
            <button type="button" className={styles.iconBtn} aria-label="編輯" title="btn-icon" onClick={click}><MIcon name="edit" size={18} /></button>
            <button type="button" className={styles.iconBtnDanger} aria-label="刪除" title="btn-icon($danger: true)" onClick={click}><MIcon name="delete_outline" size={18} /></button>
            <button type="button" className={styles.menuBtn} aria-label="更多操作" title="btn-icon-secondary" onClick={click}><MIcon name="more_vert" size={18} /></button>
            <button type="button" className={styles.dialogClose} aria-label="關閉" title="btn-dialog-close" onClick={click}><MIcon name="close" size={20} /></button>
            <button type="button" className={styles.iconBtn} aria-label="編輯（停用）" disabled><MIcon name="edit" size={18} /></button>
          </div>
        </Demo>
        <Demo label="釘選鈕" refs={["btn-icon", "Sidebar .pinBtn"]} note="已釘選常駐傾斜釘子、無底色；未釘選滑過該列才浮出（佔位、可聚焦）。">
          <div className={styles.pinRow}>
            <span>{pinned ? "已釘選的項目" : "未釘選的項目（滑過看看）"}</span>
            <button
              type="button"
              className={`${styles.pinBtn} ${pinned ? styles.pinBtnPinned : ""}`}
              aria-pressed={pinned}
              aria-label={pinned ? "取消釘選" : "釘選"}
              title={pinned ? "取消釘選" : "釘選"}
              onClick={() => setPinned((v) => !v)}
            >
              <MIcon name="push_pin" size={18} />
            </button>
          </div>
        </Demo>
        <Demo label="處理中圖示" refs={["<MIcon spin />", "global.scss .micon-spin"]} note="處理中狀態的 sync／refresh／autorenew 統一轉速 1s；MIcon 是 classic Material Icons 字型，沒有 progress_activity。">
          <div className={styles.row}>
            <button type="button" className={styles.btnSecondary} onClick={() => setSpinning((v) => !v)}>
              <MIcon name="autorenew" size={16} spin={spinning} />
              {spinning ? "處理中…" : "重新整理"}
            </button>
            <MIcon name="sync" size={20} spin={spinning} />
            <MIcon name="hourglass_empty" size={20} spin={spinning} />
          </div>
        </Demo>
        <Demo label="聊天面板：標頭鈕與輸入框" refs={["chat-header-btn", "chat-composer", "chat-composer($padding: 6px)", "chat-composer-btn", "chat-composer-btn($primary: false)"]} wide note="AI 助手（AiFloatingChat）與檢查表助理（AiJudgePanel）共用；標頭鈕 34px 圓形無底色，輸入框 textarea 隨內容長高、約 7 行停住在框內捲動，聚焦只換邊框色。">
          <div className={styles.row}>
            <button type="button" className={styles.chatHeaderBtn} aria-label="資料來源" title="chat-header-btn" onClick={click}><MIcon name="source" size={20} /></button>
            <button type="button" className={styles.chatHeaderBtn} aria-label="清除對話" title="chat-header-btn" onClick={click}><MIcon name="delete_sweep" size={20} /></button>
            <button type="button" className={styles.chatHeaderBtn} aria-label="停用" disabled><MIcon name="close" size={20} /></button>
          </div>
          <div className={styles.chatComposer}>
            <button type="button" className={styles.chatAttach} aria-label="附件" title="chat-composer-btn($primary: false)" onClick={click}><MIcon name="attach_file" size={20} /></button>
            <textarea rows={1} placeholder="輸入訊息，Shift＋Enter 換行" aria-label="訊息" />
            <button type="button" className={styles.chatSend} aria-label="送出" title="chat-composer-btn" onClick={click}><MIcon name="arrow_upward" size={20} /></button>
          </div>
        </Demo>
      </div>
    </Section>
  );
}

const SEGMENT_OPTIONS = [
  { value: "all", label: "全部" },
  { value: "running", label: "運行中" },
  { value: "stopped", label: "已停止" },
];

export function FormsSection() {
  const [password, setPassword] = useState("Sky");
  const [cores, setCores] = useState(4);
  const [segment, setSegment] = useState("all");
  const [query, setQuery] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [agree, setAgree] = useState(false);
  return (
    <Section
      id="forms"
      title="表單 Forms"
      refs={["@include form-field", "@include form-control", "@include form-control-invalid", "components/PasswordInput", "components/NumberInput", "components/PasswordRules"]}
      intro="欄位一律 .field { @include form-field } ＋ .field input/select/textarea { @include form-control }；控制項固定 36px（內距 6 不是 8），跟按鈕、SegmentedControl、搜尋框同一列才對得齊。欄位怎麼排寫在頁面自己的 grid。"
    >
      <div className={styles.demoGrid}>
        <Demo label="欄位狀態" refs={["form-field", "form-control", ".fieldInvalid.fieldInvalid"]} wide>
          <div className={styles.formGrid}>
            <label className={styles.field}>
              <span>班級名稱</span>
              <input placeholder="例如：資管三甲 作業系統" />
            </label>
            <label className={styles.field}>
              <span>送出時未填（form-control-invalid）</span>
              <input className={styles.fieldInvalid} aria-invalid="true" placeholder="必填" />
              <em className={styles.error}>此欄位必填</em>
            </label>
            <label className={styles.field}>
              <span>唯讀 [readonly]</span>
              <input readOnly value="vm-web-01" />
            </label>
            <label className={styles.field}>
              <span>停用 :disabled</span>
              <input disabled value="LDAP 帳號不可改" />
            </label>
            <label className={styles.field}>
              <span>下拉選單</span>
              <select defaultValue="ubuntu">
                <option value="ubuntu">Ubuntu 24.04</option>
                <option value="debian">Debian 12</option>
                <option value="win">Windows Server 2022</option>
              </select>
            </label>
            <label className={styles.field}>
              <span>日期（鎖 36px）</span>
              <input type="date" defaultValue="2026-10-26" />
            </label>
            <label className={styles.field}>
              <span>密碼（PasswordInput 眼睛鈕，不進 Tab 序）</span>
              <PasswordInput value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
              <PasswordRules password={password} />
            </label>
            <label className={styles.field}>
              <span>數字（NumberInput：離開欄位才夾 1–64）</span>
              <NumberInput value={cores} min={1} max={64} step={1} onCommit={setCores} aria-label="CPU 核心數" />
              <span className={styles.hint}>目前定稿值：{cores}</span>
            </label>
            <label className={styles.field}>
              <span>多行文字（固定高：rows 決定，_reset.scss 已全域 resize: none）</span>
              <textarea rows={3} placeholder="備註" />
            </label>
            <label className={styles.field}>
              <span>隨內容長高（field-sizing: content＋max-height，多打幾行看看）</span>
              <textarea className={styles.autoGrow} rows={1} placeholder="回覆學生：一行起跳，最多長到約 5 行，之後在框內捲動" />
              <span className={styles.hint}>聊天輸入框、回覆欄這類「一行起跳」的欄位才用；仍然不寫 resize。</span>
            </label>
          </div>
        </Demo>
        <Demo label="同一列 36px 對齊" refs={["form-control", "SegmentedControl", "ToolbarSearch", "btn-primary", "Switch"]} wide note="欄位、分段切換、搜尋框、按鈕都 36px；搜尋框外框在容器上用 min-height: 36px，不寫 38px。登入／TOTP 的 44px 是例外。">
          <div className={styles.alignRow}>
            <input className={styles.control} placeholder="form-control" aria-label="示範欄位" />
            <SegmentedControl options={SEGMENT_OPTIONS} value={segment} onChange={setSegment} ariaLabel="狀態篩選" />
            <ToolbarSearch value={query} onChange={setQuery} placeholder="搜尋名稱、VMID 或 IP" />
            <button type="button" className={styles.btnPrimary}>新增</button>
            <button type="button" className={styles.btnSecondary}>取消</button>
            <Switch checked={enabled} onChange={setEnabled} label="立即生效的開關" />
          </div>
        </Demo>
        <Demo label="開關與勾選框" refs={["components/Switch", ".checkRow"]} note="一切換就送出 → Switch；按「儲存」才生效 → 勾選框。表格列裡只放開關本體，不寫「啟用」字樣，用 ariaLabel＋title。">
          <div className={styles.stack}>
            <div className={styles.row}>
              <Switch checked={enabled} onChange={setEnabled} ariaLabel="啟用規則" title="啟用規則" />
              <Switch checked={!enabled} onChange={(v) => setEnabled(!v)} label="顯示文字的版本" />
              <Switch checked disabled ariaLabel="停用中的開關" title="停用中" />
            </div>
            <label className={styles.checkRow}>
              <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
              建立後立即開機（按儲存才生效）
            </label>
          </div>
        </Demo>
      </div>
    </Section>
  );
}

export function SegmentSection() {
  const [range, setRange] = useState("7d");
  const [status, setStatus] = useState("pending");
  const [many, setMany] = useState("a");
  return (
    <Section
      id="segment"
      title="分段切換 SegmentedControl"
      refs={["components/SegmentedControl/SegmentedControl.jsx", "options: [{ value, label, icon?, badge?, buttonProps? }]"]}
      intro="互斥選項的即時切換一律用共用 SegmentedControl；固定 36px、選中段主色實心。超過 4 段在 className 加 max-width:100% + overflow-x:auto。"
    >
      <div className={styles.demoGrid}>
        <Demo label="基本＋圖示" refs={["icon"]}>
          <SegmentedControl
            ariaLabel="日期區間"
            value={range}
            onChange={setRange}
            options={[
              { value: "24h", label: "24 小時", icon: "schedule" },
              { value: "7d", label: "7 天", icon: "date_range" },
              { value: "30d", label: "30 天", icon: "calendar_month" },
            ]}
          />
        </Demo>
        <Demo label="數量徽章＋停用段" refs={["badge", "buttonProps: { disabled }"]}>
          <SegmentedControl
            ariaLabel="審核狀態"
            value={status}
            onChange={setStatus}
            options={[
              { value: "pending", label: "待審核", badge: 3 },
              { value: "approved", label: "已核准", badge: 12 },
              { value: "rejected", label: "已拒絕", badge: 0 },
              { value: "published", label: "發布", buttonProps: { disabled: true, title: "條件未滿足" } },
            ]}
          />
        </Demo>
        <Demo label="窄容器：compact＋fullWidth" refs={["compact", "fullWidth"]} note="AI 導師檢查頁 220px 側邊欄的分頁：compact 把各段內距收成 8px，fullWidth 撐滿容器、各段平分。兩個都是元件的 prop，頁面 SCSS 不要再覆寫 .segmentBtn。">
          <div className={styles.narrowBox}>
            <SegmentedControl
              ariaLabel="側欄分頁"
              compact
              fullWidth
              value={status === "approved" ? "approved" : "pending"}
              onChange={setStatus}
              options={[
                { value: "pending", label: "本週檢查", icon: "fact_check" },
                { value: "approved", label: "檢查表助理", icon: "support_agent" },
              ]}
            />
          </div>
        </Demo>
        <Demo label="5 段以上橫向捲動" refs={["className → overflow-x: auto"]} wide>
          <SegmentedControl
            ariaLabel="分頁"
            className={styles.segmentScroll}
            value={many}
            onChange={setMany}
            options={["a", "b", "c", "d", "e", "f"].map((key, index) => ({
              value: key,
              label: ["基本資料", "學生名單", "上課環境", "課表", "AI 導師檢查", "成績"][index],
            }))}
          />
        </Demo>
      </div>
    </Section>
  );
}

export function StepperSection() {
  const [active, setActive] = useState("env");
  return (
    <Section
      id="stepper"
      title="步驟列 Stepper"
      refs={["components/Stepper/Stepper.jsx", "steps: [{ key, label, done?, disabled? }]", "extras: [{ key, label, icon }]"]}
      intro="流程分頁一律用共用 Stepper（圓點連線），chevron 已淘汰。直接放在頁面上不包卡片、不鋪底色；精靈式流程可跳回已完成步、還不能去的步設 disabled（只擋點擊不淡化）。"
    >
      <Demo label="精靈：1、2 完成、目前第 3 步、第 4 步還不能去；extras 是不算步驟的分頁" plain>
        <Stepper
          ariaLabel="建立班級步驟"
          activeKey={active}
          onSelect={setActive}
          steps={[
            { key: "basic", label: "基本資料", done: true },
            { key: "students", label: "學生名單", done: true },
            { key: "env", label: "上課環境" },
            { key: "review", label: "總覽", disabled: active !== "review" },
          ]}
          extras={[
            { key: "progress", label: "上課進度", icon: "timeline" },
            { key: "ai", label: "AI 導師檢查", icon: "smart_toy" },
          ]}
        />
      </Demo>
      <RuleList
        items={[
          "狀態只靠圓點：未完成＝白底淡藍框編號、已完成＝白底主色框 ✓、目前＝主色實心加光暈；連線兩端都走到了才上主色。",
          "能不能到、打不打勾看已存到後端的進度；換步驟前有未存修改先 confirmLeave()。",
          "手機只留目前步驟的標籤，其他剩圓點；放不下時橫向捲動。",
        ]}
      />
    </Section>
  );
}

const STATUS_BADGES = [
  ["badge_success", "運行中", "--color-success"],
  ["badge_info", "進行中", "--color-info"],
  ["badge_pending", "待審核", "--color-pending"],
  ["badge_danger", "失敗", "--color-danger"],
  ["badge_muted", "已停止", "--color-status-neutral"],
];

export function BadgesSection() {
  return (
    <Section
      id="badges"
      title="徽章 Badges"
      refs={["@include badge-base", ".badge_success / _info / _pending / _danger / _muted", "components/MachineKindBadge/MachineKindBadge.jsx"]}
      intro="徽章一律「狀態色 12% 淡底＋狀態色文字」，不加外框；疊在會透出底圖的畫布上時淡底改混 var(--color-surface)。機器來源徽章只用兩群色：我的（info）／別人的（kind-shared），類別交給圖示＋文字。"
    >
      <div className={styles.demoGrid}>
        <Demo label="狀態徽章五款" refs={["color-mix(in srgb, var(--color-*) 12%, transparent)"]}>
          <div className={styles.row}>
            {STATUS_BADGES.map(([cls, label, token]) => (
              <span key={cls} className={`${styles.badge} ${styles[cls]}`} title={token}>{label}</span>
            ))}
          </div>
        </Demo>
        <Demo label="機器來源 MachineKindBadge" refs={["kind", "classRelation", "ownerName", "readOnly"]} note="有擁有者名一律歸「別人的」紫群；淡底 8%。">
          <div className={styles.row}>
            <MachineKindBadge kind="personal" />
            <MachineKindBadge kind="shared" ownerName="王小明" />
            <MachineKindBadge kind="teaching_class" classRelation="student" teachingClassName="作業系統" />
            <MachineKindBadge kind="teaching_class" classRelation="teacher" ownerName="陳同學" />
            <MachineKindBadge kind="quick_practice" />
            <MachineKindBadge kind="course" readOnly />
          </div>
        </Demo>
        <Demo label="實心版（拓撲節點）與行內版" refs={["solid", "plain"]}>
          <div className={styles.row}>
            <MachineKindBadge kind="personal" solid />
            <MachineKindBadge kind="shared" ownerName="王小明" solid />
            <span className={styles.caption}>行內：這台是 <MachineKindBadge kind="course" plain /> 的機器</span>
          </div>
        </Demo>
      </div>
    </Section>
  );
}

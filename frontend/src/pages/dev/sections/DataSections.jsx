import { useEffect, useRef, useState } from "react";
import MIcon from "../../../components/MIcon";
import DataToolbar from "../../../components/DataToolbar/DataToolbar";
import BatchBar, { BatchAction, BatchDivider } from "../../../components/DataToolbar/BatchBar";
import SelectCheckbox from "../../../components/DataToolbar/SelectCheckbox";
import ToolbarSearch from "../../../components/DataToolbar/ToolbarSearch";
import SegmentedControl from "../../../components/SegmentedControl/SegmentedControl";
import MachineKindBadge from "../../../components/MachineKindBadge/MachineKindBadge";
import PowerMenu from "../../../components/PowerMenu/PowerMenu";
import FileDropzone from "../../../components/FileDropzone/FileDropzone";
import FileDropOverlay from "../../../components/FileDropzone/FileDropOverlay";
import Switch from "../../../components/Switch/Switch";
import useRowSelection from "../../../hooks/useRowSelection";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { useToast } from "../../../hooks/useToast";
import { Demo, RuleList, Section } from "../DevSection";
import styles from "../DevStylePage.module.scss";

/* 假資料：不打後端 */
const ROWS = [
  { id: 101, name: "web-01", kind: "personal", status: "running", cpu: 2, mem: "4 GB" },
  { id: 102, name: "db-primary", kind: "shared", owner: "王小明", status: "running", cpu: 4, mem: "8 GB" },
  { id: 103, name: "os-lab-07", kind: "teaching_class", relation: "student", status: "stopped", cpu: 2, mem: "2 GB" },
  { id: 104, name: "student-a-vm", kind: "teaching_class", relation: "teacher", owner: "陳同學", status: "pending", cpu: 1, mem: "1 GB" },
  { id: 105, name: "quick-9f2", kind: "quick_practice", status: "error", cpu: 1, mem: "1 GB" },
];

const STATUS_BADGE = {
  running: ["badge_success", "運行中"],
  stopped: ["badge_muted", "已停止"],
  pending: ["badge_pending", "建立中"],
  error: ["badge_danger", "錯誤"],
};

const FILTERS = [
  { value: "all", label: "全部" },
  { value: "running", label: "運行中" },
  { value: "stopped", label: "已停止" },
];

export function TablesSection() {
  const toast = useToast();
  const confirm = useConfirm();
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [progress, setProgress] = useState(null);
  const [menu, setMenu] = useState(null); // { row, anchorRef }
  const menuPresence = useDialogPresence(menu);
  const timerRef = useRef(null);

  useEffect(() => () => window.clearInterval(timerRef.current), []);

  const shown = ROWS.filter((r) => filter === "all" || r.status === filter)
    .filter((r) => !query || r.name.toLowerCase().includes(query.toLowerCase()));
  const selection = useRowSelection(shown.map((r) => r.id));
  const selected = shown.filter((r) => selection.isSelected(r.id));
  const runningSelected = selected.filter((r) => r.status === "running");

  function simulateBatch() {
    const total = selected.length;
    let done = 0;
    setProgress({ text: "正在關機…", done, total });
    timerRef.current = window.setInterval(() => {
      done += 1;
      if (done >= total) {
        window.clearInterval(timerRef.current);
        setProgress(null);
        selection.clear();
        toast.success(`示範：已對 ${total} 台送出關機`);
        return;
      }
      setProgress({ text: "正在關機…", done, total });
    }, 500);
  }

  async function removeSelected() {
    const names = selected.map((r) => r.name);
    const listed = names.slice(0, 5).join("、") + (names.length > 5 ? ` 等 ${names.length} 台` : "");
    const ok = await confirm({
      title: "刪除機器",
      message: `確定要刪除 ${listed}？這個動作無法復原。`,
      confirmText: "刪除",
      danger: true,
    });
    if (ok) toast.success("示範：已確認刪除（沒有真的刪）");
  }

  const closeMenu = () => setMenu(null);

  return (
    <Section
      id="tables"
      title="表格與清單工具列 Tables / DataToolbar"
      refs={["@include table-wrap / table-base / table-th / table-tr / table-tr-clickable / table-td", "@include row-selected", "components/DataToolbar（DataToolbar、BatchBar、BatchAction、BatchDivider、SelectCheckbox、ToolbarSearch）", "hooks/useRowSelection", "components/PowerMenu", "components/DataTable（TanStack 列渲染）"]}
      intro="列表頁表格一律用表格 mixin 組；RWD 是容器橫向捲動，不轉卡片。整列 hover 變色＝這一列可以點（tr 自己有 onClick 才疊 table-tr-clickable）。勾選後工具列原地換成批量列；勾選狀態下點整列也是勾選。"
    >
      <Demo label="篩選＋搜尋，勾選後換成批量列；⋮ 開真的 PowerMenu（portal）" plain>
        <DataToolbar
          selecting={selected.length > 0}
          batch={(
            <BatchBar countLabel={`已選 ${selected.length} 台`} onClear={selection.clear} progress={progress}>
              <BatchAction
                icon="power_settings_new"
                label="關機"
                disabled={runningSelected.length === 0}
                title={runningSelected.length < selected.length ? `會略過 ${selected.length - runningSelected.length} 台未運行的機器` : undefined}
                onClick={simulateBatch}
              />
              <BatchDivider />
              <BatchAction icon="delete_outline" label="刪除" danger onClick={removeSelected} />
            </BatchBar>
          )}
        >
          <SegmentedControl
            ariaLabel="狀態篩選"
            value={filter}
            onChange={setFilter}
            options={FILTERS.map((f) => ({ ...f, badge: f.value === "all" ? ROWS.length : ROWS.filter((r) => r.status === f.value).length }))}
          />
          <ToolbarSearch value={query} onChange={setQuery} placeholder="搜尋名稱" />
        </DataToolbar>

        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <colgroup>
              <col className={styles.colCheck} />
              <col />
              <col className={styles.colKind} />
              <col className={styles.colStatus} />
              <col className={styles.colNum} />
              <col className={styles.colNum} />
              <col className={styles.colActions} />
            </colgroup>
            <thead>
              <tr>
                <th className={styles.th}>
                  <SelectCheckbox
                    cell
                    checked={selection.allSelected}
                    indeterminate={selection.someSelected}
                    onToggle={selection.toggleAll}
                    ariaLabel="全選"
                  />
                </th>
                <th className={styles.th}>名稱</th>
                <th className={styles.th}>來源</th>
                <th className={styles.th}>狀態</th>
                <th className={styles.th}>CPU</th>
                <th className={styles.th}>記憶體</th>
                <th className={`${styles.th} ${styles.tdActions}`}>操作</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((row) => {
                const [badgeClass, badgeLabel] = STATUS_BADGE[row.status];
                const isSelected = selection.isSelected(row.id);
                return (
                  <tr
                    key={row.id}
                    className={`${styles.tr} ${styles.trClickable} ${isSelected ? styles.trSelected : ""}`}
                    onClick={(event) => {
                      if (selection.rowClick(row.id, event)) return;
                      if (event.target.closest("label, button")) return;
                      toast.info(`示範：開啟 ${row.name} 的詳情`);
                    }}
                  >
                    <td className={styles.td}>
                      <SelectCheckbox
                        cell
                        checked={isSelected}
                        onToggle={(shift) => selection.toggle(row.id, shift)}
                        ariaLabel={`選取 ${row.name}`}
                      />
                    </td>
                    <td className={`${styles.td} ${styles.tdName}`}>{row.name}</td>
                    <td className={styles.td}>
                      <MachineKindBadge kind={row.kind} classRelation={row.relation} ownerName={row.owner} />
                    </td>
                    <td className={styles.td}>
                      <span className={`${styles.badge} ${styles[badgeClass]}`}>{badgeLabel}</span>
                    </td>
                    <td className={styles.td}>{row.cpu}</td>
                    <td className={styles.td}>{row.mem}</td>
                    <td className={`${styles.td} ${styles.tdActions}`}>
                      <button
                        type="button"
                        className={styles.menuBtn}
                        aria-label={`${row.name} 的更多操作`}
                        aria-haspopup="menu"
                        aria-expanded={menu?.row.id === row.id}
                        onClick={(event) => {
                          event.stopPropagation();
                          setMenu(menu?.row.id === row.id ? null : { row, anchorRef: { current: event.currentTarget } });
                        }}
                      >
                        <MIcon name="more_vert" size={18} />
                      </button>
                    </td>
                  </tr>
                );
              })}
              {shown.length === 0 && (
                <tr className={styles.tr}>
                  <td className={styles.td} colSpan={7}>沒有符合的機器</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {menuPresence.open && (
          <PowerMenu
            resource={menuPresence.item.row}
            anchorRef={menuPresence.item.anchorRef}
            closing={menuPresence.closing}
            onClose={closeMenu}
            onControl={(action) => toast.info(`示範：${menuPresence.item.row.name} → ${action}`)}
            onDeleteClick={() => { closeMenu(); toast.info("示範：刪除入口（會先進 useConfirm）"); }}
          />
        )}
      </Demo>
      <RuleList
        items={[
          "列數會變（展開／收合完整欄位的列）的表格要 table-layout: fixed＋<colgroup> 集中宣告欄寬。",
          "勾選框整格都是點擊範圍（SelectCheckbox cell）；Shift＋點連選、Esc 清除選取、只勾一部分時全選框顯示「部分」。",
          "回不去的動作用 danger，放在 BatchDivider 後面的最右邊，先確認並列出名稱（最多 5 個，其餘「等 N」）；送出時 progress 讓整條換成進度。",
          "沒有後端批量 API 時用 utils/runBatch 逐筆呼叫；失敗的保留勾選（selection.replace）可以直接再試。",
        ]}
      />
    </Section>
  );
}

export function DropzoneSection() {
  const toast = useToast();
  const [uploading, setUploading] = useState(false);
  const [showOverlay, setShowOverlay] = useState(false);
  const onFiles = (files) => toast.info(`示範：收到 ${files.map((f) => f.name).join("、")}（不會上傳）`);
  return (
    <Section
      id="dropzone"
      title="檔案上傳 FileDropzone"
      refs={["components/FileDropzone/FileDropzone.jsx", "components/FileDropzone/FileDropOverlay.jsx", "hooks/useFileDrop"]}
      intro="檔案上傳一律用 FileDropzone（完整／compact）；大容器（AI 對話區）拖入時用 FileDropOverlay。整塊是 <label>，點哪裡都開選擇器；hover 只給淡底，拖進來時框線才轉主色。"
    >
      <div className={styles.row}>
        <Switch checked={uploading} onChange={setUploading} label="模擬上傳中（uploading）" />
        <Switch checked={showOverlay} onChange={setShowOverlay} label="顯示 FileDropOverlay" />
      </div>
      <div className={styles.dropRow}>
        <Demo label="完整版（multiple）" refs={["hint", "progress"]}>
          <FileDropzone
            multiple
            accept=".pdf,.png,.jpg"
            hint="PDF 或圖片，單檔 10 MB 以內"
            uploading={uploading}
            progress={{ current: 2, total: 5 }}
            onFiles={onFiles}
          />
        </Demo>
        <Demo label="單行精簡版（compact）與停用" refs={["compact", "disabled"]}>
          <FileDropzone compact uploading={uploading} onFiles={onFiles} />
          <FileDropzone compact disabled title="附件已達上限" onFiles={onFiles} />
        </Demo>
        <Demo label="容器的放置提示 FileDropOverlay" refs={["useFileDrop(onFiles) → dragging && <FileDropOverlay />"]} note="平常不佔空間，拖檔案進容器時才浮出；容器要 position: relative。">
          <div className={styles.overlayHost}>
            這裡是對話區塊，把檔案拖進來看提示（或用上面的開關強制顯示）。
            {showOverlay && <FileDropOverlay />}
          </div>
        </Demo>
      </div>
    </Section>
  );
}

export function CardRulesSection() {
  const toast = useToast();
  return (
    <Section
      id="cardrules"
      title="卡片規則 Card rules"
      refs={["@include glass-surface", "border-radius: $radius-16", "var(--color-surface) + 1px solid var(--color-border)", "var(--color-flow-bg / -border / -shadow)"]}
      intro="卡片 = glass-surface＋圓角 16。玻璃卡裡的內容區塊一律白底細框，不鋪主色系淺藍（換背景色會糊）；可點卡片 hover 只給淡底／陰影／上浮，不改 border-color；全站不放常駐刷新鈕。"
    >
      <div className={styles.demoGrid}>
        <Demo label="玻璃卡裡的內容區塊：白底" refs={["--color-surface", "--color-border"]} plain>
          <div className={styles.glassCard}>
            <span className={styles.cardTitle}>機器資訊</span>
            <div className={styles.infoRow}><span className={styles.infoKey}>VMID</span><span className={styles.infoVal}>1042</span></div>
            <div className={styles.infoRow}><span className={styles.infoKey}>IP</span><span className={styles.infoVal}>10.20.3.14</span></div>
            <pre className={styles.preBlock}>{"$ ssh student@10.20.3.14\nWelcome to Ubuntu 24.04 LTS"}</pre>
          </div>
        </Demo>
        <Demo label="可點卡片 hover：淡底＋上浮，不改框色" refs={["transition: background, box-shadow, transform"]} plain>
          <div className={styles.stack}>
            <button type="button" className={styles.hoverCard} onClick={() => toast.info("示範：進入班級")}>
              <span className={styles.cardTitle}>作業系統 資管三甲</span>
              <span className={styles.hoverCardMeta}>42 位學生 · 第 6 週</span>
            </button>
            <button type="button" className={styles.hoverCard} onClick={() => toast.info("示範：進入班級")}>
              <span className={styles.cardTitle}>網路安全 資管四乙</span>
              <span className={styles.hoverCardMeta}>35 位學生 · 第 6 週</span>
            </button>
          </div>
        </Demo>
        <Demo label="拓撲畫布（白底規則的例外）" refs={["--color-flow-bg", "--color-flow-border", "--color-flow-shadow", "--color-flow-inbound"]} note="藍虛線＝對外開放的連線（--color-flow-inbound），灰虛線＝內部互通。" plain>
          <div className={styles.flowCanvas}>
            <span className={styles.flowNode}>Internet</span>
            <i className={styles.flowEdge} aria-hidden="true" />
            <span className={styles.flowNode}>web-01</span>
            <i className={styles.flowEdgeInternal} aria-hidden="true" />
            <span className={styles.flowNode}>db-primary</span>
          </div>
        </Demo>
      </div>
      <RuleList
        items={[
          "淡底不要用 linear-gradient 疊層：漸層無法 transition，hover 會瞬間跳色。",
          "藍框只留給「選中／目前」狀態（分頁 active、流程目前步驟）。",
          "不放常駐刷新／重新檢查鈕，資料用靜默自動更新；只有錯誤狀態保留「重試」。",
          "空狀態極簡：有下一步才放動作鈕；頁首同一顆鈕等有資料才出現，不重複。",
        ]}
      />
    </Section>
  );
}

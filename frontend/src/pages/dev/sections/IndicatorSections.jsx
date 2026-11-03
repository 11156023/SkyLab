import { useState } from "react";
import MIcon from "../../../components/MIcon";
import { Demo, RuleList, Section } from "../DevSection";
import styles from "../DevStylePage.module.scss";

/* AI 導師檢查頁、AI 助手、學生首頁的小型狀態指示：各頁 module.scss 自己的樣式，這裡逐值重抄做對照，
   數值改了要回來同步（它們不是共用元件，頁面之間也還沒有第二個使用處）。 */
export function IndicatorsSection() {
  const [done, setDone] = useState(true);
  return (
    <Section
      id="indicators"
      title="狀態指示 Indicators"
      refs={["pages/course-operations/class-workspace/AiJudgePanel.module.scss", "components/AiFloatingChat/AiFloatingChat.module.scss", "pages/personal/dashboard/student/StudentCoursePage.module.scss"]}
      intro="頁面專屬的小元件也要跟規範對齊：狀態色五款、徽章不加框、hover 帶過渡、圓角與字級走 token。這一區是逐值重抄的對照，不是共用元件；要在第二頁重用時先抽成共用。"
    >
      <div className={styles.demoGrid}>
        <Demo label="回報打勾 reportTick" refs={["AiJudgePanel .reportTick", "reportTick_ready / _partial"]} note="32px 圓；已完成＝綠底白勾、做到一半＝橙框橙字 n/m（待確認語意）、還沒回報＝灰框空心；JSX 帶 role=img＋aria-label。">
          <div className={styles.row}>
            <span className={`${styles.reportTick} ${styles.reportTickReady}`} role="img" aria-label="已完成回報"><MIcon name="check" size={18} /></span>
            <span className={`${styles.reportTick} ${styles.reportTickPartial}`} role="img" aria-label="回報 2 / 5"><small>2/5</small></span>
            <span className={styles.reportTick} role="img" aria-label="尚未回報" />
          </div>
        </Demo>
        <Demo label="堆疊進度條 reviewStackBar" refs={["AiJudgePanel .reviewStackBar", "role=img + aria-label"]} note="8px 高、pill 圓角、neutral 16% 底；段落依序通過（success）／未通過（danger）／待確認（pending），剩下是尚未檢查；寬度變化帶 transition。">
          <div className={styles.stack}>
            <div className={styles.statLine}>
              <span className={styles.statLabel}><MIcon name="task_alt" size={16} />回報完成</span>
              <div className={styles.stackBar} role="img" aria-label="18 / 24 已回報">
                <span className={styles.stackPass} style={{ width: "75%" }} />
              </div>
              <strong className={styles.statValue}>18<small> / 24</small></strong>
            </div>
            <div className={styles.statLine}>
              <span className={styles.statLabel}><MIcon name="fact_check" size={16} />檢查結果</span>
              <div className={styles.stackBar} role="img" aria-label="通過 12、未通過 4、待確認 3，共 24">
                <span className={styles.stackPass} style={{ width: "50%" }} />
                <span className={styles.stackFail} style={{ width: "16.7%" }} />
                <span className={styles.stackPending} style={{ width: "12.5%" }} />
              </div>
              <span className={styles.statValue}>
                <em className={styles.statPass}>通過 12</em>
                <em className={styles.statFail}>未通過 4</em>
                <em className={styles.statPending}>待確認 3</em>
              </span>
            </div>
          </div>
        </Demo>
        <Demo label="開機燈號 powerDot" refs={["AiJudgePanel .powerDot", "powerDotOn / Off / Unknown"]} note="8px 圓點放在機器晶片最前面：綠＝開著、灰＝關著、空心＝查不到狀態；顏色不是唯一線索，title 與 aria-label 補文字。">
          <div className={styles.row}>
            <span className={styles.machineChip}><span className={`${styles.powerDot} ${styles.powerDotOn}`} role="img" aria-label="開機中" />web-01</span>
            <span className={styles.machineChip}><span className={`${styles.powerDot} ${styles.powerDotOff}`} role="img" aria-label="已關機" />db-02</span>
            <span className={styles.machineChip}><span className={`${styles.powerDot} ${styles.powerDotUnknown}`} role="img" aria-label="狀態未知" />lab-07</span>
          </div>
        </Demo>
        <Demo label="週次有無檢查 weekDot" refs={["AiJudgePanel .weekDot / .weekDotReady"]} note="7px 圓點放在側欄週次列的日期前：實心綠＝已建立檢查、空心＝還沒；aria-hidden，文字本身已經說明。">
          <div className={styles.stack}>
            <small className={styles.weekLabel}><span className={styles.weekDotReady} aria-hidden="true" />10/06 · 已建立檢查</small>
            <small className={styles.weekLabel}><span className={styles.weekDot} aria-hidden="true" />10/13 · 還沒建立 · 草稿</small>
          </div>
        </Demo>
        <Demo label="完成打勾框 weekCompletion" refs={["StudentCoursePage .weekCompletion input", "appearance: none"]} note="學生首頁每週一個勾＝這週全部做完；26px、圓角 8、沒勾是黯淡灰勾、勾了綠底白勾，hover 先變綠框；focus-visible 綠色 28% 外圈。按儲存才生效的一般勾選框仍用原生樣式（表單區）。">
          <div className={styles.row}>
            <label className={styles.completion}>
              <input type="checkbox" checked={done} onChange={(e) => setDone(e.target.checked)} aria-label="第 3 週全部完成" />
              <span>第 3 週（可點）</span>
            </label>
            <label className={styles.completion}>
              <input type="checkbox" checked={false} readOnly aria-label="第 4 週尚未完成" />
              <span>還沒勾</span>
            </label>
            <label className={styles.completion}>
              <input type="checkbox" checked disabled aria-label="第 2 週已鎖定" />
              <span>停用</span>
            </label>
          </div>
        </Demo>
        <Demo label="欄寬拖拉把手 columnResizer" refs={["AiJudgePanel .columnResizer", "role=separator aria-orientation=vertical"]} note="疊在兩欄分隔線上、9px 寬好抓，平常透明，hover／focus／拖拉時亮出 3px 主色細線（帶過渡）；鍵盤左右鍵也能調，連點兩下還原。這裡只示意外觀，不會真的拖。">
          <div className={styles.resizerDemo}>
            <div className={styles.resizerPane}>週次清單</div>
            <div className={styles.resizerPane}>檢查內容</div>
            <div className={styles.columnResizer} role="separator" aria-orientation="vertical" aria-label="調整欄寬" aria-valuemin={240} aria-valuemax={520} aria-valuenow={380} tabIndex={0} title="拖拉調整欄寬，連點兩下還原" />
          </div>
        </Demo>
        <Demo label="助手提示泡泡 pageHelpHint" refs={["AiFloatingChat .pageHelpHint", "z-index: 150"]} note="章魚 FAB 上方的一次性提示（每頁每人只出現一次、8 秒自動收）：白底細框、圓角 12、shadow-md，右下角有小尾巴指向章魚；整句是按鈕，點了直接開助手，× 只關提示。實際是 position: fixed，跟 FAB 同一層 150。">
          <div className={styles.hintStage}>
            <div className={styles.helpHint}>
              <button type="button" className={styles.hintMessage} onClick={() => {}}>
                <span role="status">這一頁有看不懂的地方？點我問章魚學士。</span>
              </button>
              <button type="button" className={styles.hintDismiss} aria-label="關閉提示"><MIcon name="close" size={15} /></button>
            </div>
          </div>
        </Demo>
      </div>
      <RuleList
        items={[
          "做到一半、待確認才用橙色（--color-pending）；錯誤、環境異常一律紅色。",
          "小圓點、打勾框這類只靠顏色的指示，JSX 要帶 role=img＋aria-label 或 title，不能只有顏色。",
          "這些樣式各自住在頁面的 module.scss；第二頁要用同一個時先抽到 components/ 再引用，不要複製貼上。",
        ]}
      />
    </Section>
  );
}

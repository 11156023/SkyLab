import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../contexts/AuthContext";
import { LayoutContext } from "../../layout/layoutContext";
import { AiNavigationService } from "../../services/aiNavigation";
import { AiTemplateRecommendationApi } from "../../services/aiTemplateRecommendation";
import {
  AiContextualHelpService,
  matchSurface,
} from "../../services/aiContextualHelp";
import MIcon from "../MIcon";
import useDialogPresence from "../../hooks/useDialogPresence";
import useBodyScrollLock from "../../hooks/useBodyScrollLock";
import styles from "./AiFloatingChat.module.scss";

/* title/suggestions 是模組層級常數，無法呼叫 hook，改存 key，實際 render 處再 t() */
const PAGE_CONTEXTS = [
  { match: /^\/dashboard/, titleKey: "AiFloatingChat.pageDashboardTitle", suggestionKeys: ["AiFloatingChat.pageDashboardSuggestion1", "AiFloatingChat.pageDashboardSuggestion2", "AiFloatingChat.pageDashboardSuggestion3"] },
  { match: /^\/my-resources/, titleKey: "AiFloatingChat.pageMyResourcesTitle", suggestionKeys: ["AiFloatingChat.pageMyResourcesSuggestion1", "AiFloatingChat.pageMyResourcesSuggestion2", "AiFloatingChat.pageMyResourcesSuggestion3"] },
  { match: /^\/my-requests/, titleKey: "AiFloatingChat.pageMyRequestsTitle", suggestionKeys: ["AiFloatingChat.pageMyRequestsSuggestion1", "AiFloatingChat.pageMyRequestsSuggestion2", "AiFloatingChat.pageMyRequestsSuggestion3"] },
  { match: /^\/resource-mgmt/, titleKey: "AiFloatingChat.pageResourceMgmtTitle", suggestionKeys: ["AiFloatingChat.pageResourceMgmtSuggestion1", "AiFloatingChat.pageResourceMgmtSuggestion2", "AiFloatingChat.pageResourceMgmtSuggestion3"] },
  { match: /^\/request-review/, titleKey: "AiFloatingChat.pageRequestReviewTitle", suggestionKeys: ["AiFloatingChat.pageRequestReviewSuggestion1", "AiFloatingChat.pageRequestReviewSuggestion2", "AiFloatingChat.pageRequestReviewSuggestion3"] },
  { match: /^\/ip-management/, titleKey: "AiFloatingChat.pageIpManagementTitle", suggestionKeys: ["AiFloatingChat.pageIpManagementSuggestion1", "AiFloatingChat.pageIpManagementSuggestion2", "AiFloatingChat.pageIpManagementSuggestion3"] },
  { match: /^\/reverse-proxy/, titleKey: "AiFloatingChat.pageReverseProxyTitle", suggestionKeys: ["AiFloatingChat.pageReverseProxySuggestion1", "AiFloatingChat.pageReverseProxySuggestion2", "AiFloatingChat.pageReverseProxySuggestion3"] },
  { match: /^\/firewall/, titleKey: "AiFloatingChat.pageFirewallTitle", suggestionKeys: ["AiFloatingChat.pageFirewallSuggestion1", "AiFloatingChat.pageFirewallSuggestion2", "AiFloatingChat.pageFirewallSuggestion3"] },
  { match: /^\/domain/, titleKey: "AiFloatingChat.pageDomainTitle", suggestionKeys: ["AiFloatingChat.pageDomainSuggestion1", "AiFloatingChat.pageDomainSuggestion2", "AiFloatingChat.pageDomainSuggestion3"] },
  { match: /^\/gateway/, titleKey: "AiFloatingChat.pageGatewayTitle", suggestionKeys: ["AiFloatingChat.pageGatewaySuggestion1", "AiFloatingChat.pageGatewaySuggestion2", "AiFloatingChat.pageGatewaySuggestion3"] },
  { match: /^\/ai-api-review/, titleKey: "AiFloatingChat.pageAiApiReviewTitle", suggestionKeys: ["AiFloatingChat.pageAiApiReviewSuggestion1", "AiFloatingChat.pageAiApiReviewSuggestion2", "AiFloatingChat.pageAiApiReviewSuggestion3"] },
  { match: /^\/ai-api-keys/, titleKey: "AiFloatingChat.pageAiApiKeysTitle", suggestionKeys: ["AiFloatingChat.pageAiApiKeysSuggestion1", "AiFloatingChat.pageAiApiKeysSuggestion2", "AiFloatingChat.pageAiApiKeysSuggestion3"] },
  { match: /^\/ai-monitoring/, titleKey: "AiFloatingChat.pageAiMonitoringTitle", suggestionKeys: ["AiFloatingChat.pageAiMonitoringSuggestion1", "AiFloatingChat.pageAiMonitoringSuggestion2", "AiFloatingChat.pageAiMonitoringSuggestion3"] },
  { match: /^\/ai-pve/, titleKey: "AiFloatingChat.pageAiPveTitle", suggestionKeys: ["AiFloatingChat.pageAiPveSuggestion1", "AiFloatingChat.pageAiPveSuggestion2", "AiFloatingChat.pageAiPveSuggestion3"] },
  { match: /^\/ai-api/, titleKey: "AiFloatingChat.pageAiApiTitle", suggestionKeys: ["AiFloatingChat.pageAiApiSuggestion1", "AiFloatingChat.pageAiApiSuggestion2", "AiFloatingChat.pageAiApiSuggestion3"] },
  { match: /^\/templates/, titleKey: "AiFloatingChat.pageTemplatesTitle", suggestionKeys: ["AiFloatingChat.pageTemplatesSuggestion1", "AiFloatingChat.pageTemplatesSuggestion2", "AiFloatingChat.pageTemplatesSuggestion3"] },
  { match: /^\/gpu-mgmt/, titleKey: "AiFloatingChat.pageGpuMgmtTitle", suggestionKeys: ["AiFloatingChat.pageGpuMgmtSuggestion1", "AiFloatingChat.pageGpuMgmtSuggestion2", "AiFloatingChat.pageGpuMgmtSuggestion3"] },
  { match: /^\/monitoring/, titleKey: "AiFloatingChat.pageMonitoringTitle", suggestionKeys: ["AiFloatingChat.pageMonitoringSuggestion1", "AiFloatingChat.pageMonitoringSuggestion2", "AiFloatingChat.pageMonitoringSuggestion3"] },
];

const DEFAULT_CONTEXT = {
  titleKey: "AiFloatingChat.pageDefaultTitle",
  suggestionKeys: ["AiFloatingChat.pageDefaultSuggestion1", "AiFloatingChat.pageDefaultSuggestion2", "AiFloatingChat.pageDefaultSuggestion3"],
};

/* 同一個對話框背後有幾種能力，開場列出名稱，使用者才會用到後面幾個。 */
const CAPABILITIES = [
  { titleKey: "AiFloatingChat.capabilityFindTitle" },
  { titleKey: "AiFloatingChat.capabilityGuideTitle" },
  { titleKey: "AiFloatingChat.capabilityRecommendTitle" },
  { titleKey: "AiFloatingChat.capabilityExplainTitle" },
];

const NAVIGATION_PATTERN = /(帶我|前往|打開|開啟|跳到|導航|在哪|哪裡|頁面)/i;
/* 「我要申請一台機器」這種整件事的描述沒有導覽關鍵字，但正是流程導覽要接的。 */
const GUIDE_PATTERN = /(怎麼|怎樣|如何|步驟|流程|我要|我想|幫我)/i;
/* 問規格、問選哪個 → 交給推薦規劃，回來的是一份可以直接填進申請單的配置。 */
const RECOMMEND_PATTERN =
  /(推薦|建議|規格|配置|幾核|多少核|記憶體|多大|硬碟|該用|適合|還是|哪個|哪種|比較|差別|差異)/i;
/* 問眼前這個畫面的事：欄位怎麼填、為什麼送不出去、這頁在做什麼。
   要排在導覽前面——「這格要填什麼」是要說明，不是要被帶去別頁。 */
/* 閘門刻意放寬：真正的分類在後端（intent.py），那裡認得比較多說法，而且答不
   出來時會退回頁面說明。前端寫太窄只會把好的分類器擋在外面。 */
const HELP_PATTERN = new RegExp(
  [
    // 指著畫面上的東西問
    "這格|這欄|這個欄位|欄位|這顆|這個按鈕|按鈕|這個選項|這裡|這張表|這個狀態",
    "這頁|這一頁|本頁|這個頁面|目前頁面",
    // 問意義與用法
    "是什麼|什麼意思|代表什麼|用來做什麼|做什麼用|用途|怎麼用|怎麼填|要填什麼|填什麼",
    "怎麼選|要選什麼|有什麼限制|限制是|格式|可以做什麼|能做什麼|解釋",
    // 問為什麼被擋
    "為什麼不能|為什麼送不|送不出|送不了|沒反應|按不了|紅字|驗證|必填|反灰|停用|灰的",
  ].join("|"),
  "i",
);

/**
 * 一句話該交給哪個能力：推薦配置、導覽（含流程）、或一般問答。
 * 三者共用同一個對話框，使用者不需要知道背後是不同的服務。
 */
/* 問的是整個平台還是眼前這一頁。兩者的答案完全不同：
   「平台怎麼用」要的是功能清單，「這頁怎麼用」要的是這一頁的說明。 */
const GLOBAL_SCOPE_PATTERN = /(平台|系統|全站|整個網站|這個網站|skylab)/i;
const SCREEN_SCOPE_PATTERN =
  /(這頁|這一頁|本頁|這個頁面|目前頁面|這格|這欄|這個欄位|這顆|這個按鈕|這裡)/i;
/* 「有哪些功能」不是導覽——導覽只會挑一頁帶你去，答不出一張清單。 */
const INDEX_PATTERN =
  /(有哪些功能|有什麼功能|哪些功能|功能清單|功能列表|有哪些頁面|可以做哪些)/i;
const GLOBAL_USAGE_PATTERN = /(可以做什麼|能做什麼|怎麼用|做什麼)/i;

/** 問的是「這個平台有哪些功能」而不是某一頁。 */
export function isFeatureIndex(text) {
  if (SCREEN_SCOPE_PATTERN.test(text)) return false;
  if (INDEX_PATTERN.test(text)) return true;
  return GLOBAL_SCOPE_PATTERN.test(text) && GLOBAL_USAGE_PATTERN.test(text);
}

/* 講到流程或步驟就是要被帶著走，不管句子裡還有什麼——導覽優先於說明。 */
const FLOW_PATTERN = /(流程|步驟)/i;

/** 問的是眼前這個畫面。提到平台或系統而沒指著畫面，就不算。 */
export function isScreenHelp(text) {
  if (FLOW_PATTERN.test(text)) return false;
  if (!HELP_PATTERN.test(text)) return false;
  return !GLOBAL_SCOPE_PATTERN.test(text) || SCREEN_SCOPE_PATTERN.test(text);
}

export function routeQuestion(text) {
  if (isFeatureIndex(text)) return "index";
  if (isScreenHelp(text)) return "help";
  if (RECOMMEND_PATTERN.test(text)) return "recommend";
  if (NAVIGATION_PATTERN.test(text) || GUIDE_PATTERN.test(text)) return "navigate";
  return "chat";
}

/**
 * 功能索引要列哪些畫面。
 * 帶參數的路徑（資源詳細）進不去，同一個路徑只留一個（申請列表與申請表單
 * 是同一個入口），避免清單裡出現點了沒用或重複的項目。
 */
export function indexableSurfaces(surfaces) {
  const seen = new Set();
  return (surfaces ?? []).filter((surface) => {
    if (!surface?.path || surface.path.includes(":")) return false;
    if (seen.has(surface.path)) return false;
    seen.add(surface.path);
    return true;
  });
}

/**
 * 把推薦出來的 form_prefill 講成人看得懂的幾行。
 * t 由呼叫端（畫面 render 時）帶入才會翻譯；省略 t 時維持原繁中字面（供單元測試）。
 */
export function describePlan(prefill = {}, t) {
  const tr = (key, options, fallback) => (t ? t(key, options) : fallback);
  const lines = [];
  if (prefill.resource_type) {
    const typeLabel = prefill.resource_type === "vm"
      ? tr("AiFloatingChat.resourceTypeVm", null, "虛擬機")
      : tr("AiFloatingChat.resourceTypeLxc", null, "LXC 容器");
    lines.push(tr("AiFloatingChat.planTypeLine", { type: typeLabel }, `類型：${typeLabel}`));
  }
  const spec = [
    prefill.cores ? tr("AiFloatingChat.planCores", { cores: prefill.cores }, `${prefill.cores} 核心`) : "",
    prefill.memory_mb
      ? tr("AiFloatingChat.planMemory", { gb: (prefill.memory_mb / 1024).toFixed(1) }, `${(prefill.memory_mb / 1024).toFixed(1)} GB RAM`)
      : "",
    prefill.disk_gb ? tr("AiFloatingChat.planDisk", { gb: prefill.disk_gb }, `${prefill.disk_gb} GB 硬碟`) : "",
  ].filter(Boolean);
  if (spec.length) lines.push(tr("AiFloatingChat.planSpecLine", { spec: spec.join(" · ") }, `規格：${spec.join(" · ")}`));
  if (prefill.gpu_mapping_id) {
    lines.push(tr("AiFloatingChat.planGpuLine", { gpu: prefill.gpu_mapping_id }, `GPU：${prefill.gpu_mapping_id}`));
  }
  if (prefill.start_at && prefill.end_at) {
    const day = (value) => new Date(value).toLocaleDateString("zh-TW");
    lines.push(tr(
      "AiFloatingChat.planTimeRangeLine",
      { start: day(prefill.start_at), end: day(prefill.end_at) },
      `時段：${day(prefill.start_at)} ～ ${day(prefill.end_at)}`,
    ));
  } else if (prefill.mode === "immediate") {
    lines.push(tr("AiFloatingChat.planTimeImmediate", null, "時段：立即使用"));
  }
  return lines;
}

function stripThinkTags(text) {
  return String(text || "").replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
}

function pageContextFor(pathname) {
  return PAGE_CONTEXTS.find((item) => item.match.test(pathname)) ?? DEFAULT_CONTEXT;
}

function displayName(user, t) {
  return user?.full_name?.trim() || user?.email?.split("@")[0] || t("AiFloatingChat.defaultUserName");
}

function TypingIndicator() {
  const { t } = useTranslation("components");
  return (
    <div className={styles.typing} aria-label={t("AiFloatingChat.aiReplyingAriaLabel")}>
      <span /><span /><span />
    </div>
  );
}

const STEP_ICON = { done: "check_circle", current: "play_circle", todo: "radio_button_unchecked" };

/* 步驟狀態以「使用者現在在哪一頁」為準，所以他一邊照做、清單就一邊往前推。
   找不到對應頁面時才退回後端算好的狀態。 */
export function stepStatuses(steps, currentPath, floor = 0) {
  // floor 之前的步驟已經做完（例如配置已經產生），不能因為還停在同一頁就倒退回去
  const byPath = steps.findIndex((step, index) => index >= floor && step.path === currentPath);
  const marked = steps.findIndex((step) => step.status === "current");
  // 沒有 floor 也沒人標記時就原樣顯示，不要憑空發明一個進度
  const fallback = marked >= 0 ? Math.max(floor, marked) : (floor > 0 ? floor : -1);
  const active = byPath >= 0 ? byPath : fallback;
  if (active < 0) return steps.map((step) => step.status);
  return steps.map((_, index) => (index < active ? "done" : index === active ? "current" : "todo"));
}

function StepList({ steps, currentPath, floor = 0, onNavigate, onRecommend }) {
  const statuses = stepStatuses(steps, currentPath, floor);
  return (
    <ol className={styles.stepList}>
      {steps.map((step, index) => (
        <li key={`${step.path}-${index}`} className={styles[`step_${statuses[index]}`]}>
          {/* action 步驟由助手就地完成，不換頁 */}
          <button
            type="button"
            onClick={() => (step.action === "recommend"
              ? onRecommend()
              : onNavigate(step.path, step.state))}
          >
            <MIcon
              name={step.action === "recommend" && statuses[index] !== "done"
                ? "auto_fix_high"
                : (STEP_ICON[statuses[index]] ?? STEP_ICON.todo)}
              size={17}
            />
            <span>
              <strong>{index + 1}. {step.title}</strong>
              {/* 做完的步驟只留標題，說明文字佔掉的版面留給還沒做的 */}
              {step.detail && statuses[index] !== "done" && <small>{step.detail}</small>}
            </span>
          </button>
        </li>
      ))}
    </ol>
  );
}

function PlanCard({ plan, onNavigate }) {
  const { t } = useTranslation("components");
  const lines = describePlan(plan.prefill, t);
  return (
    <div className={styles.planCard}>
      {lines.length > 0 && (
        <ul className={styles.planSpec}>
          {lines.map((line) => <li key={line}>{line}</li>)}
        </ul>
      )}
      <button type="button" onClick={() => onNavigate("/my-requests", { create: true, prefill: plan.prefill })}>
        <MIcon name="edit_note" size={17} />
        <span>
          <strong>{t("AiFloatingChat.planGoToFormTitle")}</strong>
          <small>{t("AiFloatingChat.planGoToFormDetail")}</small>
        </span>
      </button>
    </div>
  );
}

/* 配置模式的答案晶片：點一下就等於打了那句話 */
function ChoiceRow({ choices, progress, onAnswer, onPlanNow }) {
  const { t } = useTranslation("components");
  return (
    <div className={styles.choiceBlock}>
      {progress && <span className={styles.choiceProgress}>{progress}</span>}
      <div className={styles.choiceRow}>
        {choices.map((choice) => (
          <button key={choice} type="button" onClick={() => onAnswer(choice)}>
            {choice}
          </button>
        ))}
        <button type="button" className={styles.choiceSkip} onClick={onPlanNow}>
          {t("AiFloatingChat.choiceSkipButton")}
        </button>
      </div>
    </div>
  );
}

function Message({ message, currentPath, onNavigate, onRecommend, onAnswer, onPlanNow }) {
  const isUser = message.role === "user";
  return (
    <div className={`${styles.message} ${isUser ? styles.messageUser : styles.messageAssistant}`}>
      {!isUser && (
        <span className={styles.messageAvatar}>
          <MIcon name="smart_toy" size={17} />
        </span>
      )}
      <div className={styles.messageContent}>
        {/* 模型回的是 markdown（清單、粗體、程式碼），直接印出來會看到一堆星號 */}
        {isUser ? (
          <div className={styles.messageText}>{message.content}</div>
        ) : (
          <div className={`${styles.messageText} ${styles.markdown}`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>{message.content}</ReactMarkdown>
          </div>
        )}
        {/* 先給結果（配置），再給接下來要做的事（流程），最後才是選項 */}
        {message.plan && <PlanCard plan={message.plan} onNavigate={onNavigate} />}
        {message.steps?.length > 0 && (
          <StepList
            steps={message.steps}
            currentPath={currentPath}
            floor={message.stepsFloor ?? 0}
            onNavigate={onNavigate}
            onRecommend={onRecommend}
          />
        )}
        {message.choices?.length > 0 && (
          <ChoiceRow
            choices={message.choices}
            progress={message.progress}
            onAnswer={onAnswer}
            onPlanNow={onPlanNow}
          />
        )}
        {message.targets?.length > 0 && (
          <div className={styles.actionList}>
            {message.targets.map((target) => (
              <button key={target.path} type="button" onClick={() => onNavigate(target.path, target.state)}>
                <span>
                  <strong>{target.title}</strong>
                  {target.reason && <small>{target.reason}</small>}
                </span>
                <MIcon name="arrow_forward" size={17} />
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function AiFloatingChat({ open = false, onOpenChange = () => {} }) {
  // 關閉時先播放離場動畫再卸載面板
  const presence = useDialogPresence(open, 180);
  /* <1440px 時面板是覆蓋層（fixed + backdrop），開啟期間鎖住底下頁面捲動；
     寬螢幕的並排停靠模式不鎖 */
  const [overlayMode, setOverlayMode] = useState(
    () => window.matchMedia("(max-width: 1439px)").matches,
  );
  useEffect(() => {
    const mq = window.matchMedia("(max-width: 1439px)");
    const onChange = (event) => setOverlayMode(event.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);
  useBodyScrollLock(presence.open && overlayMode);
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { t } = useTranslation("components");
  /* 申請表單開著時會把自己註冊進來：規劃就地填進欄位，而且拿得到
     這張表單當下的真實候選，推薦的 GPU 與時段才不會是憑空的。 */
  const { requestForm, surface } = useContext(LayoutContext);
  /* 目前這一頁對應到哪個畫面定義。有頁面自己註冊就用它（同一個路徑可能有多個
     畫面，例如申請列表與申請表單），否則靠路徑對照。清單只跟身分有關，載一次。 */
  const [surfaceList, setSurfaceList] = useState([]);
  const [messages, setMessages] = useState([]);
  const [history, setHistory] = useState([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const scrollRef = useRef(null);
  const inputRef = useRef(null);
  // 配置模式：{ answered, total }，null 代表沒在配置模式
  const [intake, setIntake] = useState(null);
  // 問過哪幾格。問句由推薦 AI 生成，字面對不上，只能自己記
  const askedRef = useRef([]);
  // 正在進行的流程，配置產生後要接回它的下一步，不能斷在配置卡片
  const flowRef = useRef(null);
  const pageContext = useMemo(() => pageContextFor(location.pathname), [location.pathname]);
  const activeSurface = useMemo(() => {
    const matched = matchSurface(surfaceList, location.pathname);
    if (!surface?.id) return matched;
    return surfaceList.find((item) => item.id === surface.id) ?? matched;
  }, [surface, surfaceList, location.pathname]);
  const activeSurfaceId = activeSurface?.id ?? surface?.id ?? null;
  /* 頁名優先用畫面定義的標題：它涵蓋每一頁，PAGE_CONTEXTS 只列了一部分，
     沒列到的會落到「SkyLab」，等於沒講。 */
  const currentPageName = activeSurface?.title ?? t(pageContext.titleKey);

  useEffect(() => {
    let cancelled = false;
    AiContextualHelpService.surfaces()
      .then((list) => { if (!cancelled) setSurfaceList(list ?? []); })
      .catch(() => { /* 對照表載不到就只剩頁面自己註冊的那一個，不影響其他能力 */ });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (open) window.setTimeout(() => inputRef.current?.focus(), 120);
  }, [open]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, loading]);

  function close() {
    onOpenChange(false);
  }

  function clearChat() {
    setMessages([]);
    setHistory([]);
    setInput("");
    setIntake(null);
    askedRef.current = [];
    flowRef.current = null;
    inputRef.current?.focus();
  }

  function handleNavigate(path, state) {
    if (!path) return;
    navigate(path, state ? { state } : undefined);
    if (window.matchMedia("(max-width: 1439px)").matches) close();
  }

  async function sendNavigation(text, nextHistory) {
    const data = await AiNavigationService.resolve(text, {
      // 送出前的前文（不含這一輪），讓「然後呢」這種追問有東西可以指
      history: nextHistory.slice(0, -1),
      currentPath: location.pathname,
    });
    const steps = data.steps ?? [];

    if (data.action === "guide" && steps.length) {
      flowRef.current = { title: data.flow_title, steps };
      const flowTitle = data.flow_title ?? t("AiFloatingChat.defaultFlowTitle");
      const content = t("AiFloatingChat.flowIntro", { flowTitle });
      const assistantMessage = { role: "assistant", content, steps };
      setMessages((previous) => [...previous, assistantMessage]);
      setHistory((previous) => [...previous, {
        role: "assistant",
        content: `${content}（${steps.map((step) => step.title).join("→")}）`,
      }]);
      return true;
    }

    const targets = [...(data.primary ? [data.primary] : []), ...(data.suggestions ?? [])]
      .filter((target, index, all) => all.findIndex((item) => item.path === target.path) === index);

    // 導覽答不出東西時，交給一般問答回答，不要用「找不到頁面」把使用者擋掉。
    if (!targets.length) return false;

    const content = data.action === "clarify"
      ? (data.clarification_question || t("AiFloatingChat.defaultClarificationQuestion"))
      : t("AiFloatingChat.foundTargetsMessage");
    const assistantMessage = { role: "assistant", content, targets };
    setMessages((previous) => [...previous, assistantMessage]);
    setHistory((previous) => [...previous, { role: "assistant", content }]);
    return true;
  }

  /* 推薦配置：規劃出一份可以直接送出的申請內容。資源候選（作業系統、GPU、時段）
     由後端自己補，所以助手不在申請頁也能規劃。 */
  async function sendRecommendation(text, nextHistory) {
    let data;
    try {
      data = await AiTemplateRecommendationApi.recommend({
        messages: nextHistory,
        top_k: 5,
        device_nodes: [],
        form_context: requestForm?.getContext() ?? null,
      });
    } catch {
      // 規劃是三個能力裡最重的一個，失敗就讓一般問答接手，不要整段對話中斷。
      return false;
    }
    const plan = data?.final_plan;
    const prefill = plan?.form_prefill;
    if (!prefill?.resource_type) return false;

    const summary = stripThinkTags(plan.summary);

    /* 配置只是流程的一步，產生完要把剩下的步驟接回來，不能停在這裡。 */
    const flow = flowRef.current;
    const recommendIndex = flow
      ? flow.steps.findIndex((step) => step.action === "recommend")
      : -1;
    /* 進度停在規劃那一步：欄位填好了，但檢查、輸入密碼、按送出都還在同一張
       表單上，還沒走到「等待審核」。 */
    const followUp = flow && recommendIndex >= 0
      ? { steps: flow.steps, stepsFloor: recommendIndex }
      : {};

    /* 申請表單開著就直接填進去——填好的表單本身就是結果，
       不需要再給一張長得像表單的卡片。表單沒開才給卡片＋一鍵帶過去。 */
    const filled = Boolean(requestForm);
    if (filled) requestForm.applyPrefill(prefill);
    const filledNote = t("AiFloatingChat.filledFormNote");
    const content = filled
      ? (summary ? `${summary}\n\n${filledNote}` : filledNote)
      : (summary || t("AiFloatingChat.recommendationIntro"));

    const assistantMessage = {
      role: "assistant",
      content,
      ...(filled ? {} : { plan: { prefill } }),
      ...followUp,
    };
    setMessages((previous) => [...previous, assistantMessage]);
    setHistory((previous) => [...previous, {
      role: "assistant",
      content: `${content}（${describePlan(prefill).join("；")}）`,
    }]);
    return true;
  }

  /* 配置模式的一輪：先看還缺什麼，缺就問（問句交給推薦 AI 用顧問語氣講），
     問齊了才規劃。這樣使用者是被一題一題帶著走，而不是一句話就收到一份猜的配置。 */
  async function advanceIntake(nextHistory) {
    const state = await AiNavigationService.intake(nextHistory, askedRef.current);
    /* 直接問「推薦規格」進來的人沒有走過流程，這裡把流程補上，
       配置產生後才有下一步可以接。 */
    if (state.steps?.length && !flowRef.current) {
      flowRef.current = { title: state.flow_title, steps: state.steps };
    }

    if (state.ready || !state.question) {
      setIntake(null);
      askedRef.current = [];
      return await sendRecommendation("", nextHistory);
    }

    askedRef.current = [...new Set([...askedRef.current, state.question.key])];
    setIntake({ answered: state.answered, total: state.total });

    // 問句由推薦 AI 產生；它掛掉時就用伺服器端那句制式問法，不要卡住對話。
    let question = state.question.text;
    try {
      const reply = await AiTemplateRecommendationApi.chat({
        messages: nextHistory,
        top_k: 5,
        device_nodes: [],
        form_context: requestForm?.getContext() ?? null,
        focus_hint: state.question.text,
      });
      question = stripThinkTags(reply.reply) || question;
    } catch {
      /* 用制式問法 */
    }

    const assistantMessage = {
      role: "assistant",
      content: question,
      choices: state.question.options,
      progress: t("AiFloatingChat.answeredProgress", { answered: state.answered, total: state.total }),
    };
    setMessages((previous) => [...previous, assistantMessage]);
    setHistory((previous) => [...previous, { role: "assistant", content: question }]);
    return true;
  }

  /* 流程裡的「讓 AI 規劃配置」那一步：從這裡進配置模式。 */
  async function runRecommendation() {
    if (loading) return;
    const nextHistory = history.length
      ? history
      : [{ role: "user", content: "我想申請一台機器，請幫我規劃配置。" }];
    setLoading(true);
    try {
      await advanceIntake(nextHistory);
    } catch {
      setMessages((previous) => [...previous, {
        role: "assistant",
        content: t("AiFloatingChat.needMoreInfoMessage"),
      }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  /* 不想被問完的人可以直接跳到結果 */
  async function planNow() {
    if (loading) return;
    setIntake(null);
    askedRef.current = [];
    setLoading(true);
    try {
      const planned = await sendRecommendation("", history);
      if (!planned) {
        setMessages((previous) => [...previous, {
          role: "assistant",
          content: t("AiFloatingChat.needMoreInfoMessage"),
        }]);
      }
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  /* 功能索引：使用者問「有哪些功能」。清單就是他權限內看得到的畫面，
     不呼叫模型——列清單不需要推論，也不該有幻覺的空間。 */
  function sendFeatureIndex() {
    const targets = indexableSurfaces(surfaceList).map((surface) => ({
      path: surface.path,
      title: surface.title,
      reason: surface.purpose ?? "",
    }));
    if (!targets.length) return false;
    const content = t("AiFloatingChat.featureIndexIntro", { count: targets.length });
    setMessages((previous) => [...previous, { role: "assistant", content, targets }]);
    setHistory((previous) => [...previous, {
      role: "assistant",
      content: `${content}（${targets.map((target) => target.title).join("、")}）`,
    }]);
    return true;
  }

  /* 畫面說明：只問眼前這一頁。沒有對應的畫面定義就交給下一個能力，
     不要硬答——這個助手的價值全在「講的都有依據」。 */
  async function sendContextualHelp(text) {
    if (!activeSurfaceId) return false;
    const data = await AiContextualHelpService.explain({
      question: text,
      surfaceId: activeSurfaceId,
      activeTarget: surface?.getActiveTarget?.() ?? null,
      contextVersion: surface?.getVersion?.() ?? 0,
      state: surface?.getState?.() ?? {},
    });
    const answer = data?.answer?.trim();
    if (!answer) return false;
    const assistantMessage = { role: "assistant", content: answer };
    setMessages((previous) => [...previous, assistantMessage]);
    setHistory((previous) => [...previous, assistantMessage]);
    return true;
  }

  async function sendChat(text, nextHistory) {
    const contextualHistory = nextHistory.map((message, index) => {
      if (index !== nextHistory.length - 1 || message.role !== "user") return message;
      return {
        ...message,
        content: `目前所在頁面：${currentPageName}。使用者問題：${message.content}`,
      };
    });
    const data = await AiTemplateRecommendationApi.chat({
      messages: contextualHistory,
      top_k: 5,
      device_nodes: [],
      form_context: null,
    });
    const assistantMessage = {
      role: "assistant",
      content: stripThinkTags(data.reply) || t("AiFloatingChat.noReplyFallback"),
    };
    setMessages((previous) => [...previous, assistantMessage]);
    setHistory((previous) => [...previous, assistantMessage]);
  }

  async function send(value = input) {
    const text = value.trim();
    if (!text || loading) return;

    const userMessage = { role: "user", content: text };
    const nextHistory = [...history, userMessage];
    setInput("");
    setMessages((previous) => [...previous, userMessage]);
    setHistory(nextHistory);
    setLoading(true);

    try {
      // 每個能力答不出來就往下一個退，最後一定有一般問答接住。
      const route = routeQuestion(text);
      // 配置模式進行中就繼續問，除非使用者明講要去別的地方
      const stayInIntake = intake && !NAVIGATION_PATTERN.test(text);
      if (!stayInIntake && intake) {
        setIntake(null);
        askedRef.current = [];
      }

      let handled = false;
      if (stayInIntake || route === "recommend") handled = await advanceIntake(nextHistory);
      else if (route === "index") handled = sendFeatureIndex();
      else if (route === "help") handled = await sendContextualHelp(text);
      else if (route === "navigate") handled = await sendNavigation(text, nextHistory);
      if (!handled) await sendChat(text, nextHistory);
    } catch (error) {
      setMessages((previous) => [...previous, {
        role: "assistant",
        content: error?.message || t("AiFloatingChat.genericErrorFallback"),
      }]);
    } finally {
      setLoading(false);
      inputRef.current?.focus();
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  }

  return (
    <div className={`${styles.root} ${presence.open ? styles.rootOpen : ""}`}>
      {presence.open && (
        <button
          type="button"
          className={`${styles.backdrop} ${presence.closing ? styles.backdropOut : ""}`}
          onClick={close}
          aria-label={t("AiFloatingChat.closeAssistantAriaLabel")}
        />
      )}

      {presence.open && (
        <aside className={`${styles.panel} ${presence.closing ? styles.panelOut : ""}`} aria-label={t("AiFloatingChat.assistantName")} data-guide="request-ai-helper-panel">
          <header className={styles.header}>
            <div className={styles.headerText}>
              <strong>{t("AiFloatingChat.assistantName")}</strong>
              <span>{t("AiFloatingChat.headerSubtitle")}</span>
            </div>
            <button type="button" onClick={clearChat} title={t("AiFloatingChat.newChatLabel")} aria-label={t("AiFloatingChat.newChatLabel")}>
              <MIcon name="refresh" size={19} />
            </button>
            <button type="button" onClick={close} title={t("AiFloatingChat.closeLabel")} aria-label={t("AiFloatingChat.closeLabel")}>
              <MIcon name="close" size={21} />
            </button>
          </header>

          <div className={styles.contextBar}>
            {intake ? (
              <>
                <MIcon name="auto_fix_high" size={16} />
                <span>{t("AiFloatingChat.contextIntakeStatus", { answered: intake.answered, total: intake.total })}</span>
              </>
            ) : (
              <>
                <MIcon name="web_asset" size={16} />
                <span>{t("AiFloatingChat.contextViewingPage", { page: currentPageName })}</span>
              </>
            )}
          </div>

          <div className={styles.messages} ref={scrollRef}>
            {messages.length === 0 ? (
              <div className={styles.emptyState}>
                <h2>{displayName(user, t)}{t("AiFloatingChat.greetingSuffix")}</h2>
                <p>{t("AiFloatingChat.emptyStatePrompt")}</p>
                {/* 能力要講出來，不然沒有人知道可以叫它推薦規格、幫忙填表 */}
                <ul className={styles.capabilities}>
                  {CAPABILITIES.map((item) => (
                    <li key={item.titleKey}>
                      <strong>{t(item.titleKey)}</strong>
                    </li>
                  ))}
                </ul>
                <div className={styles.suggestions}>
                  {pageContext.suggestionKeys.map((key) => (
                    <button key={key} type="button" onClick={() => send(t(key))}>
                      {t(key)}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((message, index) => (
                <Message
                  key={`${message.role}-${index}`}
                  message={message}
                  currentPath={location.pathname}
                  onNavigate={handleNavigate}
                  onRecommend={runRecommendation}
                  onAnswer={(choice) => send(choice)}
                  onPlanNow={planNow}
                />
              ))
            )}
            {loading && (
              <div className={`${styles.message} ${styles.messageAssistant}`}>
                <span className={styles.messageAvatar}><MIcon name="smart_toy" size={17} /></span>
                <TypingIndicator />
              </div>
            )}
          </div>

          <footer className={styles.composerWrap}>
            <div className={styles.composer}>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={handleKeyDown}
                placeholder={t("AiFloatingChat.composerPlaceholder")}
                rows={2}
                disabled={loading}
              />
              <button type="button" onClick={() => send()} disabled={loading || !input.trim()} aria-label={t("AiFloatingChat.sendMessageAriaLabel")}>
                <MIcon name="arrow_upward" size={20} />
              </button>
            </div>
            <small>{t("AiFloatingChat.aiDisclaimer")}</small>
          </footer>
        </aside>
      )}

      {!presence.open && (
        <button type="button" className={styles.fab} onClick={() => onOpenChange(true)} title={t("AiFloatingChat.assistantName")} aria-label={t("AiFloatingChat.openAssistantAriaLabel")} data-guide="request-ai-helper-button">
          <MIcon name="smart_toy" size={22} />
        </button>
      )}
    </div>
  );
}

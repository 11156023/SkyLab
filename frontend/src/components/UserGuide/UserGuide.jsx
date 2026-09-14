import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../contexts/AuthContext";
import MIcon from "../MIcon";
import styles from "./UserGuide.module.scss";

const STUDENT_HOME_GUIDE = {
  id: "student-home",
  titleKey: "UserGuide.studentHome.title",
  icon: "home",
  steps: [
    {
      selector: '[data-guide="home-schedule"]',
      titleKey: "UserGuide.studentHome.step1.title",
      textKey: "UserGuide.studentHome.step1.text",
    },
    {
      selector: '[data-guide="home-quick-templates"]',
      titleKey: "UserGuide.studentHome.step2.title",
      textKey: "UserGuide.studentHome.step2.text",
    },
    {
      selector: '[data-guide="home-other-needs"]',
      titleKey: "UserGuide.studentHome.step3.title",
      textKey: "UserGuide.studentHome.step3.text",
    },
    {
      selector: '[data-guide="home-current-course"]',
      titleKey: "UserGuide.studentHome.step4.title",
      textKey: "UserGuide.studentHome.step4.text",
    },
    {
      selector: '[data-guide="home-progress"]',
      titleKey: "UserGuide.studentHome.step5.title",
      textKey: "UserGuide.studentHome.step5.text",
    },
    {
      selector: '[data-guide="home-start"]',
      titleKey: "UserGuide.studentHome.step6.title",
      textKey: "UserGuide.studentHome.step6.text",
    },
    {
      selector: '[data-guide="home-environment"]',
      titleKey: "UserGuide.studentHome.step7.title",
      textKey: "UserGuide.studentHome.step7.text",
    },
    {
      selector: '[data-guide="home-tasks"]',
      titleKey: "UserGuide.studentHome.step8.title",
      textKey: "UserGuide.studentHome.step8.text",
    },
    {
      selector: '[data-guide="course-ai-assignments"]',
      titleKey: "UserGuide.studentHome.step9.title",
      textKey: "UserGuide.studentHome.step9.text",
      optional: true,
    },
  ],
};

const PAGE_GUIDES = {
  "/dashboard": STUDENT_HOME_GUIDE,
  "/my-requests": {
    id: "my-requests",
    titleKey: "UserGuide.myRequests.title",
    icon: "assignment",
    steps: [
      {
        selector: '[data-guide="request-create"]',
        titleKey: "UserGuide.myRequests.step1.title",
        textKey: "UserGuide.myRequests.step1.text",
      },
      {
        selector: '[data-guide="request-list"]',
        titleKey: "UserGuide.myRequests.step2.title",
        textKey: "UserGuide.myRequests.step2.text",
      },
    ],
  },
  "/my-resources": {
    id: "my-resources",
    titleKey: "UserGuide.myResources.title",
    icon: "computer",
    steps: [
      {
        selector: '[data-guide="resource-quota"]',
        titleKey: "UserGuide.myResources.step1.title",
        textKey: "UserGuide.myResources.step1.text",
      },
      {
        selector: '[data-guide="resource-card"]',
        titleKey: "UserGuide.myResources.step2.title",
        textKey: "UserGuide.myResources.step2.text",
      },
      {
        selector: '[data-guide="resource-console"]',
        titleKey: "UserGuide.myResources.step3.title",
        textKey: "UserGuide.myResources.step3.text",
      },
    ],
  },
  "/firewall": {
    id: "firewall",
    titleKey: "UserGuide.firewall.title",
    icon: "security",
    steps: [
      {
        selector: '[data-guide="firewall-create"]',
        titleKey: "UserGuide.firewall.step1.title",
        textKey: "UserGuide.firewall.step1.text",
      },
      {
        selector: '[data-guide="firewall-map"]',
        titleKey: "UserGuide.firewall.step2.title",
        textKey: "UserGuide.firewall.step2.text",
      },
      {
        selector: '[data-guide="firewall-tools"]',
        titleKey: "UserGuide.firewall.step3.title",
        textKey: "UserGuide.firewall.step3.text",
      },
    ],
  },
  "/reverse-proxy": {
    id: "reverse-proxy",
    titleKey: "UserGuide.reverseProxy.title",
    icon: "swap_horiz",
    steps: [
      {
        selector: '[data-guide="proxy-create"]',
        titleKey: "UserGuide.reverseProxy.step1.title",
        textKey: "UserGuide.reverseProxy.step1.text",
      },
      {
        selector: '[data-guide="proxy-list"]',
        titleKey: "UserGuide.reverseProxy.step2.title",
        textKey: "UserGuide.reverseProxy.step2.text",
      },
    ],
  },
  "/domain": {
    id: "domain",
    titleKey: "UserGuide.domain.title",
    icon: "domain",
    steps: [
      {
        selector: '[data-guide="domain-connect"]',
        titleKey: "UserGuide.domain.step1.title",
        textKey: "UserGuide.domain.step1.text",
      },
      {
        selector: '[data-guide="domain-status"]',
        titleKey: "UserGuide.domain.step2.title",
        textKey: "UserGuide.domain.step2.text",
      },
      {
        selector: '[data-guide="domain-zones"]',
        titleKey: "UserGuide.domain.step3.title",
        textKey: "UserGuide.domain.step3.text",
      },
      {
        selector: '[data-guide="domain-records"]',
        titleKey: "UserGuide.domain.step4.title",
        textKey: "UserGuide.domain.step4.text",
      },
    ],
  },
  "/ai-api": {
    id: "ai-api",
    guideVersion: "v7",
    titleKey: "UserGuide.aiApi.title",
    icon: "psychology",
    steps: [
      {
        selector: '[data-guide="ai-stats"]',
        titleKey: "UserGuide.aiApi.step1.title",
        textKey: "UserGuide.aiApi.step1.text",
      },
      {
        selector: '[data-guide="ai-tabs"]',
        titleKey: "UserGuide.aiApi.step2.title",
        textKey: "UserGuide.aiApi.step2.text",
      },
      {
        selector: '[data-guide="ai-add-key"]',
        activateSelector: '[data-guide-tab="keys"]',
        titleKey: "UserGuide.aiApi.step3.title",
        textKey: "UserGuide.aiApi.step3.text",
      },
      {
        selector: '[data-guide="ai-apply-name"]',
        activateSelector: '[data-guide="ai-add-key"]',
        deactivateSelector: '[data-guide="ai-apply-close"]',
        titleKey: "UserGuide.aiApi.step4.title",
        textKey: "UserGuide.aiApi.step4.text",
      },
      {
        selector: '[data-guide="ai-apply-purpose"]',
        activateSelector: '[data-guide="ai-add-key"]',
        deactivateSelector: '[data-guide="ai-apply-close"]',
        titleKey: "UserGuide.aiApi.step5.title",
        textKey: "UserGuide.aiApi.step5.text",
      },
      {
        selector: '[data-guide="ai-apply-duration"]',
        activateSelector: '[data-guide="ai-add-key"]',
        deactivateSelector: '[data-guide="ai-apply-close"]',
        titleKey: "UserGuide.aiApi.step6.title",
        textKey: "UserGuide.aiApi.step6.text",
      },
      {
        selector: '[data-guide="ai-submit"]',
        activateSelector: '[data-guide="ai-add-key"]',
        deactivateSelector: '[data-guide="ai-apply-close"]',
        titleKey: "UserGuide.aiApi.step7.title",
        textKey: "UserGuide.aiApi.step7.text",
      },
      {
        selector: '[data-guide-tab="keys"]',
        activateSelector: '[data-guide-tab="keys"]',
        titleKey: "UserGuide.aiApi.step8.title",
        textKey: "UserGuide.aiApi.step8.text",
      },
      {
        selector: '[data-guide="ai-keys-content"]',
        activateSelector: '[data-guide-tab="keys"]',
        titleKey: "UserGuide.aiApi.step9.title",
        textKey: "UserGuide.aiApi.step9.text",
      },
      {
        selector: '[data-guide="ai-key-actions"]',
        activateSelector: '[data-guide-tab="keys"]',
        conditionSelector: '[data-guide-tab="keys"][data-guide-has-content="true"]',
        titleKey: "UserGuide.aiApi.step10.title",
        textKey: "UserGuide.aiApi.step10.text",
      },
      {
        selector: '[data-guide-tab="records"]',
        activateSelector: '[data-guide-tab="records"]',
        titleKey: "UserGuide.aiApi.step11.title",
        textKey: "UserGuide.aiApi.step11.text",
      },
      {
        selector: '[data-guide="ai-records-content"]',
        activateSelector: '[data-guide-tab="records"]',
        titleKey: "UserGuide.aiApi.step12.title",
        textKey: "UserGuide.aiApi.step12.text",
      },
      {
        selector: '[data-guide-tab="usage"]',
        activateSelector: '[data-guide-tab="usage"]',
        titleKey: "UserGuide.aiApi.step13.title",
        textKey: "UserGuide.aiApi.step13.text",
      },
      {
        selector: '[data-guide="ai-usage-panel"]',
        activateSelector: '[data-guide-tab="usage"]',
        titleKey: "UserGuide.aiApi.step14.title",
        textKey: "UserGuide.aiApi.step14.text",
      },
      {
        selector: '[data-guide="ai-route-usage"]',
        activateSelector: '[data-guide-tab="usage"]',
        titleKey: "UserGuide.aiApi.step15.title",
        textKey: "UserGuide.aiApi.step15.text",
      },
      {
        selector: '[data-guide="ai-usage-records"]',
        activateSelector: '[data-guide-tab="usage"]',
        titleKey: "UserGuide.aiApi.step16.title",
        textKey: "UserGuide.aiApi.step16.text",
      },
    ],
  },
};

const SPOTLIGHT_GAP = 8;
const PANEL_WIDTH = 420;
const PANEL_HEIGHT = 320;
const VIEWPORT_GAP = 16;

function getPanelPosition(rect) {
  const width = Math.min(PANEL_WIDTH, window.innerWidth - VIEWPORT_GAP * 2);
  const spaceRight = window.innerWidth - rect.right;
  const spaceLeft = rect.left;
  const spaceBelow = window.innerHeight - rect.bottom;

  let left;
  let top;
  let side;

  if (spaceRight >= width + 28) {
    left = rect.right + 20;
    top = rect.top;
    side = "right";
  } else if (spaceLeft >= width + 28) {
    left = rect.left - width - 20;
    top = rect.top;
    side = "left";
  } else if (spaceBelow >= PANEL_HEIGHT + 20) {
    left = rect.left;
    top = rect.bottom + 16;
    side = "bottom";
  } else {
    left = rect.left;
    top = rect.top - PANEL_HEIGHT - 16;
    side = "top";
  }

  return {
    left: Math.max(VIEWPORT_GAP, Math.min(left, window.innerWidth - width - VIEWPORT_GAP)),
    top: Math.max(VIEWPORT_GAP, Math.min(top, window.innerHeight - PANEL_HEIGHT - VIEWPORT_GAP)),
    width,
    side,
  };
}

export default function UserGuide() {
  const { t } = useTranslation("common");
  const location = useLocation();
  const { user } = useAuth();
  const isStudent = user?.role === "student" && !user?.is_superuser;
  const isStudentCoursePage = /^\/dashboard\/course\/[^/]+$/.test(location.pathname);
  const guide = isStudentCoursePage
    ? {
        ...PAGE_GUIDES["/dashboard"],
        id: "student-course",
        titleKey: "UserGuide.studentCourse.title",
        steps: PAGE_GUIDES["/dashboard"].steps.filter(
          (item) => item.selector !== '[data-guide="home-schedule"]'
            && item.selector !== '[data-guide="home-other-needs"]'
        ),
      }
    : PAGE_GUIDES[location.pathname] ?? null;
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [targetRect, setTargetRect] = useState(null);
  const [slot, setSlot] = useState(null);
  const originalAiTab = useRef(null);
  const prevStepRef = useRef(null);

  useEffect(() => {
    if (!guide) {
      setSlot(null);
      return undefined;
    }
    const sync = () => {
      setSlot((prev) => (prev?.isConnected ? prev : document.querySelector("[data-user-guide-slot]")));
    };
    sync();
    // 頁面經 lazy 載入，slot 可能晚於本元件掛載才進 DOM，需持續觀察
    const observer = new MutationObserver(sync);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [guide?.id]);

  const availableSteps = useMemo(() => {
    if (!guide || typeof document === "undefined") return [];
    return guide.steps.filter((item) => {
      if (item.conditionSelector && !document.querySelector(item.conditionSelector)) return false;
      const targetExists = document.querySelector(item.selector);
      if (item.optional) return targetExists;
      return targetExists
        || (item.activateSelector && document.querySelector(item.activateSelector));
    });
  }, [guide, open]);

  const current = availableSteps[step] ?? availableSteps[0];
  const storageKey = guide
    ? `skylab:user-guide:${guide.guideVersion ?? "v5"}:${user?.id ?? user?.email ?? "user"}:${guide.id}`
    : null;
  const isLast = step >= availableSteps.length - 1;

  useEffect(() => {
    setOpen(false);
    setStep(0);
    setTargetRect(null);
  }, [guide?.id]);

  useEffect(() => {
    if (!guide || !isStudent || !storageKey) return undefined;

    try {
      if (localStorage.getItem(storageKey) === "completed") return undefined;
    } catch {
      // 儲存空間不可用時，仍保留學生首次進入頁面的主動導覽。
    }

    // 頁面資料可能還在載入，等到至少一個導覽目標進 DOM 再開啟，
    // 否則 availableSteps 會以空陣列被 memo 住，之後手動點擊也打不開
    let timer = null;
    let attempts = 0;
    const tryOpen = () => {
      if (guide.steps.some((item) => document.querySelector(item.selector))) {
        setStep(0);
        setOpen(true);
        return;
      }
      if (attempts < 20) {
        attempts += 1;
        timer = window.setTimeout(tryOpen, 500);
      }
    };
    timer = window.setTimeout(tryOpen, 500);

    return () => window.clearTimeout(timer);
  }, [guide?.id, isStudent, storageKey]);

  useLayoutEffect(() => {
    if (!open || !current) {
      setTargetRect(null);
      return undefined;
    }

    // 離開上一步時，若該步有 deactivateSelector（例如關閉導覽開啟的彈窗），先點擊關閉
    const prevStep = prevStepRef.current;
    if (prevStep && prevStep !== current && prevStep.deactivateSelector) {
      document.querySelector(prevStep.deactivateSelector)?.click();
    }
    prevStepRef.current = current;

    setTargetRect(null);
    let target = null;
    let observer = null;
    let frame = null;
    let targetTimer = null;
    let settleTimer = null;
    let targetAttempts = 0;

    const update = () => {
      if (!target) return;
      const rect = target.getBoundingClientRect();
      setTargetRect({
        top: Math.max(0, rect.top - SPOTLIGHT_GAP),
        left: Math.max(0, rect.left - SPOTLIGHT_GAP),
        right: Math.min(window.innerWidth, rect.right + SPOTLIGHT_GAP),
        bottom: Math.min(window.innerHeight, rect.bottom + SPOTLIGHT_GAP),
        width: Math.min(window.innerWidth, rect.right + SPOTLIGHT_GAP) - Math.max(0, rect.left - SPOTLIGHT_GAP),
        height: Math.min(window.innerHeight, rect.bottom + SPOTLIGHT_GAP) - Math.max(0, rect.top - SPOTLIGHT_GAP),
      });
    };

    const activate = current.activateSelector
      ? document.querySelector(current.activateSelector)
      : null;
    if (activate && activate.getAttribute("aria-selected") !== "true") activate.click();

    const attachTarget = () => {
      target = document.querySelector(current.selector);
      if (!target && targetAttempts < 12) {
        targetAttempts += 1;
        targetTimer = window.setTimeout(attachTarget, 100);
        return;
      }
      target ??= activate;
      if (!target) return;
      target.scrollIntoView({ behavior: "smooth", block: "center", inline: "nearest" });
      frame = window.requestAnimationFrame(update);
      settleTimer = window.setTimeout(update, 360);
      observer = new ResizeObserver(update);
      observer.observe(target);
    };

    targetTimer = window.setTimeout(attachTarget, current.activateSelector ? 80 : 0);
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);

    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.clearTimeout(targetTimer);
      window.clearTimeout(settleTimer);
      window.removeEventListener("resize", update);
      window.removeEventListener("scroll", update, true);
      observer?.disconnect();
    };
  }, [current, open]);

  if (!guide) return null;

  const complete = () => {
    try {
      localStorage.setItem(storageKey, "completed");
    } catch {
      // 儲存空間不可用時，只關閉本次導覽。
    }
    const activeStep = availableSteps[step] ?? availableSteps[0];
    setOpen(false);
    setStep(0);
    prevStepRef.current = null;
    if (activeStep?.deactivateSelector) {
      document.querySelector(activeStep.deactivateSelector)?.click();
    }
    if (guide.id === "ai-api" && originalAiTab.current) {
      document.querySelector(`[data-guide-tab="${originalAiTab.current}"]`)?.click();
      originalAiTab.current = null;
    }
  };

  const start = () => {
    if (guide.id === "ai-api") {
      originalAiTab.current = document.querySelector('[data-guide-tab][aria-selected="true"]')?.dataset.guideTab ?? null;
    }
    // 先關再開：availableSteps 以 open 為 memo 依賴，重開才會用當下 DOM 重算，
    // 也讓 auto-start 搶跑失敗後（open 已為 true）的點擊仍能生效
    setOpen(false);
    setStep(0);
    window.setTimeout(() => setOpen(true), 80);
  };

  const next = () => {
    if (isLast) complete();
    else setStep((value) => value + 1);
  };

  const panelPosition = targetRect ? getPanelPosition(targetRect) : null;

  return (
    <>
      {slot && createPortal(
        <button
          type="button"
          className={styles.helpButton}
          onClick={start}
          aria-label={t("UserGuide.openGuideAriaLabel", { title: t(guide.titleKey) })}
          title={t("UserGuide.guideTitleAttr", { title: t(guide.titleKey) })}
        >
          <MIcon name="help_outline" size={16} />
        </button>,
        slot
      )}

      {open && current && targetRect && panelPosition && (
        <div className={styles.layer}>
          <div className={styles.guideBackdrop} aria-hidden="true" />
          <div
            className={styles.spotlight}
            style={{
              top: targetRect.top,
              left: targetRect.left,
              width: targetRect.width,
              height: targetRect.height,
            }}
          />
          <button
            type="button"
            className={styles.targetShield}
            style={{
              top: targetRect.top,
              left: targetRect.left,
              width: targetRect.width,
              height: targetRect.height,
            }}
            onClick={next}
            aria-label={t("UserGuide.nextStepAriaLabel")}
          />

          <section
            className={styles.panel}
            data-side={panelPosition.side}
            style={{ left: panelPosition.left, top: panelPosition.top, width: panelPosition.width }}
            role="dialog"
            aria-modal="true"
            aria-labelledby="global-guide-title"
          >
            <div className={styles.header}>
              <span className={styles.icon}><MIcon name={guide.icon} size={22} /></span>
              <div>
                <small>{t("UserGuide.tourSubtitle", { title: t(guide.titleKey) })}</small>
                <strong>{step + 1} / {availableSteps.length}</strong>
              </div>
              <button type="button" onClick={complete} aria-label={t("UserGuide.closeGuideAriaLabel")}>
                <MIcon name="close" size={19} />
              </button>
            </div>

            <div className={styles.content}>
              <h2 id="global-guide-title">{t(current.titleKey)}</h2>
              <p>{t(current.textKey)}</p>
            </div>

            <div className={styles.progress} aria-label={t("UserGuide.progressAriaLabel", { current: step + 1, total: availableSteps.length })}>
              {availableSteps.map((item, index) => (
                <span key={item.selector} className={index <= step ? styles.progressActive : ""} />
              ))}
            </div>

            <div className={styles.actions}>
              <button type="button" className={styles.skip} onClick={complete}>{t("UserGuide.skipButton")}</button>
              <div>
                {step > 0 && (
                  <button type="button" className={styles.back} onClick={() => setStep((value) => value - 1)}>
                    {t("UserGuide.backButton")}
                  </button>
                )}
                <button type="button" className={styles.next} onClick={next}>
                  {isLast ? t("UserGuide.finishButton") : t("UserGuide.nextButton")}
                  <MIcon name={isLast ? "check" : "arrow_forward"} size={17} />
                </button>
              </div>
            </div>
          </section>
        </div>
      )}
    </>
  );
}

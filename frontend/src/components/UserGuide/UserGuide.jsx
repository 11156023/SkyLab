import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../contexts/AuthContext";
import MIcon from "../MIcon";
import { getRouteGuide } from "./routeGuides";
import styles from "./UserGuide.module.scss";

const STUDENT_HOME_GUIDE = {
  id: "student-home",
  autoStart: true,
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
  "/courses": {
    id: "courses",
    titleKey: "UserGuide.courses.title",
    icon: "school",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.courses.step1.title",
        textKey: "UserGuide.courses.step1.text",
      },
      {
        selector: '[data-guide="course-demo-card"]',
        deferred: true,
        titleKey: "UserGuide.courses.step2.title",
        textKey: "UserGuide.courses.step2.text",
      },
      {
        selector: '[data-guide="course-demo-open"]',
        performSelector: '[data-guide="course-demo-open"]',
        chainToGuide: "student-course",
        deferred: true,
        titleKey: "UserGuide.courses.step3.title",
        textKey: "UserGuide.courses.step3.text",
      },
    ],
  },
  "/my-requests": {
    id: "my-requests",
    guideVersion: "v8",
    autoStart: true,
    enterSelector: '[data-guide="request-create"]',
    enterTargetSelector: '[data-guide="request-resource-settings"]',
    titleKey: "UserGuide.myRequests.title",
    icon: "assignment",
    steps: [
      {
        selector: '[data-page-guide="header"]',
        titleKey: "UserGuide.myRequests.step1.title",
        textKey: "UserGuide.myRequests.step1.text",
      },
      {
        selector: '[data-guide="request-ai-helper-button"], [data-guide="request-ai-helper-panel"]',
        titleKey: "UserGuide.myRequests.step2.title",
        textKey: "UserGuide.myRequests.step2.text",
      },
      {
        selector: '[data-guide="request-resource-settings"]',
        titleKey: "UserGuide.myRequests.step3.title",
        textKey: "UserGuide.myRequests.step3.text",
      },
      {
        selector: '[data-guide="request-hardware"]',
        titleKey: "UserGuide.myRequests.step4.title",
        textKey: "UserGuide.myRequests.step4.text",
      },
      {
        selector: '[data-guide="request-schedule"]',
        titleKey: "UserGuide.myRequests.step5.title",
        textKey: "UserGuide.myRequests.step5.text",
      },
      {
        selector: '[data-guide="request-reason"]',
        titleKey: "UserGuide.myRequests.step6.title",
        textKey: "UserGuide.myRequests.step6.text",
      },
      {
        selector: '[data-guide="request-summary"]',
        titleKey: "UserGuide.myRequests.step7.title",
        textKey: "UserGuide.myRequests.step7.text",
      },
      {
        selector: '[data-guide="request-submit"]',
        titleKey: "UserGuide.myRequests.step8.title",
        textKey: "UserGuide.myRequests.step8.text",
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
        selector: '[data-guide="resource-request"]',
        titleKey: "UserGuide.myResources.request.title",
        textKey: "UserGuide.myResources.request.text",
      },
      {
        selector: '[data-guide="resource-card"]',
        deferred: true,
        titleKey: "UserGuide.myResources.row.title",
        textKey: "UserGuide.myResources.row.text",
      },
      {
        selector: '[data-guide="resource-console"]',
        deferred: true,
        titleKey: "UserGuide.myResources.console.title",
        textKey: "UserGuide.myResources.console.text",
      },
      {
        selector: '[data-guide="resource-power-menu"]',
        activateSelector: '[data-guide="resource-more-actions"]',
        deferred: true,
        titleKey: "UserGuide.myResources.power.title",
        textKey: "UserGuide.myResources.power.text",
      },
      {
        selector: '[data-guide="resource-open-detail"]',
        performSelector: '[data-guide="resource-open-detail"]',
        chainToGuide: "resource-detail",
        deferred: true,
        titleKey: "UserGuide.myResources.detail.title",
        textKey: "UserGuide.myResources.detail.text",
      },
    ],
  },
  "/firewall": {
    id: "firewall",
    titleKey: "UserGuide.firewall.title",
    icon: "security",
    cleanupSelector: '[data-guide="connection-dialog-close"]',
    steps: [
      {
        selector: '[data-guide="firewall-drag-start"]',
        titleKey: "UserGuide.firewall.step1.title",
        textKey: "UserGuide.firewall.step1.text",
      },
      {
        selector: '[data-guide="firewall-map"]',
        demo: "drag-connection",
        titleKey: "UserGuide.firewall.dragMove.title",
        textKey: "UserGuide.firewall.dragMove.text",
      },
      {
        selector: '[data-guide="firewall-drag-end"]',
        titleKey: "UserGuide.firewall.dragTarget.title",
        textKey: "UserGuide.firewall.dragTarget.text",
      },
      {
        selector: '[data-guide="firewall-create"]',
        titleKey: "UserGuide.firewall.step2.title",
        textKey: "UserGuide.firewall.step2.text",
      },
      {
        selector: '[data-guide="connection-dialog-endpoints"]',
        activateSelector: '[data-guide="firewall-create"]',
        deferred: true,
        titleKey: "UserGuide.firewall.endpoints.title",
        textKey: "UserGuide.firewall.endpoints.text",
      },
      {
        selector: '[data-guide="connection-dialog-actions"]',
        deferred: true,
        titleKey: "UserGuide.firewall.dialogActions.title",
        textKey: "UserGuide.firewall.dialogActions.text",
      },
      {
        selector: '[data-guide="firewall-map"]',
        activateSelector: '[data-guide="connection-dialog-close"]',
        titleKey: "UserGuide.firewall.map.title",
        textKey: "UserGuide.firewall.map.text",
      },
      {
        selector: '[data-guide="firewall-tools"]',
        titleKey: "UserGuide.firewall.step4.title",
        textKey: "UserGuide.firewall.step4.text",
      },
    ],
  },
  "/reverse-proxy": {
    id: "reverse-proxy",
    titleKey: "UserGuide.reverseProxy.title",
    icon: "swap_horiz",
    cleanupSelector: '[data-guide="proxy-rule-close"]',
    steps: [
      {
        selector: '[data-guide="proxy-create"]',
        titleKey: "UserGuide.reverseProxy.step1.title",
        textKey: "UserGuide.reverseProxy.step1.text",
      },
      {
        selector: '[data-guide="proxy-rule-resource"]',
        activateSelector: '[data-guide="proxy-create"]',
        deferred: true,
        titleKey: "UserGuide.reverseProxy.step2.title",
        textKey: "UserGuide.reverseProxy.step2.text",
      },
      { selector: '[data-guide="proxy-rule-domain"]', deferred: true, titleKey: "UserGuide.reverseProxy.domain.title", textKey: "UserGuide.reverseProxy.domain.text" },
      { selector: '[data-guide="proxy-rule-port"]', deferred: true, titleKey: "UserGuide.reverseProxy.port.title", textKey: "UserGuide.reverseProxy.port.text" },
      { selector: '[data-guide="proxy-rule-actions"]', deferred: true, titleKey: "UserGuide.reverseProxy.actions.title", textKey: "UserGuide.reverseProxy.actions.text" },
      {
        selector: '[data-guide="proxy-list"]',
        activateSelector: '[data-guide="proxy-rule-close"]',
        titleKey: "UserGuide.reverseProxy.list.title",
        textKey: "UserGuide.reverseProxy.list.text",
      },
    ],
  },
  "/domain": {
    id: "domain",
    titleKey: "UserGuide.domain.title",
    icon: "domain",
    cleanupSelector: '[data-guide="domain-modal-close"]',
    steps: [
      {
        selector: '[data-guide="domain-connect"]',
        titleKey: "UserGuide.domain.step1.title",
        textKey: "UserGuide.domain.step1.text",
      },
      {
        selector: '[data-guide="domain-config-form"]',
        activateSelector: '[data-guide="domain-settings-open"]',
        deferred: true,
        titleKey: "UserGuide.domain.configForm.title",
        textKey: "UserGuide.domain.configForm.text",
      },
      {
        selector: '[data-guide="domain-status"], [data-guide="domain-tabs"]',
        activateSelector: '[data-guide="domain-modal-close"]',
        titleKey: "UserGuide.domain.step2.title",
        textKey: "UserGuide.domain.step2.text",
      },
      {
        selector: '[data-guide="domain-tabs"]',
        titleKey: "UserGuide.domain.tabs.title",
        textKey: "UserGuide.domain.tabs.text",
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
      {
        selector: '[data-guide="domain-record-form"]',
        activateSelector: '[data-guide="domain-record-open"]',
        deferred: true,
        titleKey: "UserGuide.domain.recordForm.title",
        textKey: "UserGuide.domain.recordForm.text",
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

const RESOURCE_DETAIL_GUIDE = {
  id: "resource-detail",
  titleKey: "UserGuide.resourceDetail.title",
  icon: "dns",
  steps: [
    { selector: '[data-guide="resource-detail-tabs"]', titleKey: "UserGuide.resourceDetail.step1.title", textKey: "UserGuide.resourceDetail.step1.text" },
    { selector: '[data-guide="resource-detail-overview"]', activateSelector: '[data-guide-tab="resource-overview"]', deferred: true, titleKey: "UserGuide.resourceDetail.step2.title", textKey: "UserGuide.resourceDetail.step2.text" },
    { selector: '[data-guide="resource-detail-monitoring"]', activateSelector: '[data-guide-tab="resource-monitoring"]', deferred: true, titleKey: "UserGuide.resourceDetail.step3.title", textKey: "UserGuide.resourceDetail.step3.text" },
    { selector: '[data-guide="resource-detail-specifications"]', activateSelector: '[data-guide-tab="resource-specifications"]', deferred: true, titleKey: "UserGuide.resourceDetail.step4.title", textKey: "UserGuide.resourceDetail.step4.text" },
    { selector: '[data-guide="resource-detail-snapshots"]', activateSelector: '[data-guide-tab="resource-snapshots"]', deferred: true, titleKey: "UserGuide.resourceDetail.step5.title", textKey: "UserGuide.resourceDetail.step5.text" },
    { selector: '[data-guide="resource-detail-auditLogs"]', activateSelector: '[data-guide-tab="resource-auditLogs"]', deferred: true, titleKey: "UserGuide.resourceDetail.step6.title", textKey: "UserGuide.resourceDetail.step6.text" },
    { selector: '[data-guide="resource-detail-advanced"]', activateSelector: '[data-guide-tab="resource-advanced"]', deferred: true, titleKey: "UserGuide.resourceDetail.step7.title", textKey: "UserGuide.resourceDetail.step7.text" },
    { selector: '[data-guide="resource-setting-lifecycle"]', deferred: true, titleKey: "UserGuide.resourceDetail.step8.title", textKey: "UserGuide.resourceDetail.step8.text" },
    { selector: '[data-guide="resource-setting-firewall"]', deferred: true, titleKey: "UserGuide.resourceDetail.step9.title", textKey: "UserGuide.resourceDetail.step9.text" },
    { selector: '[data-guide="resource-setting-boot"]', deferred: true, titleKey: "UserGuide.resourceDetail.step10.title", textKey: "UserGuide.resourceDetail.step10.text" },
    { selector: '[data-guide="resource-setting-credentials"]', deferred: true, titleKey: "UserGuide.resourceDetail.step11.title", textKey: "UserGuide.resourceDetail.step11.text" },
    { selector: '[data-guide="resource-setting-sharing"]', deferred: true, titleKey: "UserGuide.resourceDetail.step13.title", textKey: "UserGuide.resourceDetail.step13.text" },
  ],
};

const STUDENT_COURSE_GUIDE = {
  id: "student-course",
  titleKey: "UserGuide.studentCourse.title",
  icon: "menu_book",
  steps: [
    { selector: '[data-guide="home-current-course"]', titleKey: "UserGuide.studentCourse.step1.title", textKey: "UserGuide.studentCourse.step1.text" },
    { selector: '[data-guide="home-progress"]', titleKey: "UserGuide.studentCourse.step2.title", textKey: "UserGuide.studentCourse.step2.text" },
    { selector: '[data-guide="home-start"]', optional: true, titleKey: "UserGuide.studentCourse.step3.title", textKey: "UserGuide.studentCourse.step3.text" },
    { selector: '[data-guide="home-tasks"]', titleKey: "UserGuide.studentCourse.step4.title", textKey: "UserGuide.studentCourse.step4.text" },
    {
      selector: '[data-guide="course-week-open"]',
      performSelector: '[data-guide="course-week-open"]',
      chainToGuide: "course-week",
      optional: true,
      titleKey: "UserGuide.studentCourse.step5.title",
      textKey: "UserGuide.studentCourse.step5.text",
    },
  ],
};

const COURSE_WEEK_GUIDE = {
  id: "course-week",
  titleKey: "UserGuide.courseWeek.title",
  icon: "event_note",
  steps: [
    { selector: '[data-guide="course-week-header"]', titleKey: "UserGuide.courseWeek.step1.title", textKey: "UserGuide.courseWeek.step1.text" },
    { selector: '[data-guide="course-week-feedback"]', titleKey: "UserGuide.courseWeek.step2.title", textKey: "UserGuide.courseWeek.step2.text" },
    { selector: '[data-guide="course-week-machine"]', titleKey: "UserGuide.courseWeek.step3.title", textKey: "UserGuide.courseWeek.step3.text" },
    { selector: '[data-guide="course-week-materials"]', optional: true, titleKey: "UserGuide.courseWeek.step4.title", textKey: "UserGuide.courseWeek.step4.text" },
  ],
};

function getDetailedGuide(pathname, search = "") {
  if (/^\/courses\/[^/]+\/weeks\/[^/]+$/.test(pathname)) return COURSE_WEEK_GUIDE;
  if (/^\/(?:courses|dashboard\/course)\/[^/]+$/.test(pathname)) return STUDENT_COURSE_GUIDE;
  if (/^\/(?:my-resources|resource-mgmt)\/[^/]+$/.test(pathname)) return RESOURCE_DETAIL_GUIDE;
  if (pathname === "/domain" && new URLSearchParams(search).get("tab") === "reverse-proxy") return PAGE_GUIDES["/reverse-proxy"];
  return PAGE_GUIDES[pathname] ?? null;
}

const SPOTLIGHT_GAP = 8;

function getFirewallDragGeometry() {
  const preferredSources = [...document.querySelectorAll('[data-guide="firewall-drag-start"]')];
  const preferredTargets = [...document.querySelectorAll('[data-guide="firewall-drag-end"]')];
  const sourceHandles = preferredSources.length
    ? preferredSources
    : [...document.querySelectorAll('[data-firewall-handle="source"]')];
  const targetHandles = preferredTargets.length
    ? preferredTargets
    : [...document.querySelectorAll('[data-firewall-handle="target"]')];

  let best = null;
  sourceHandles.forEach((source) => {
    const sourceNode = source.closest(".react-flow__node");
    const sourceRect = source.getBoundingClientRect();
    const x1 = sourceRect.left + sourceRect.width / 2;
    const y1 = sourceRect.top + sourceRect.height / 2;

    targetHandles.forEach((target) => {
      if (sourceNode && target.closest(".react-flow__node") === sourceNode) return;
      const targetRect = target.getBoundingClientRect();
      const x2 = targetRect.left + targetRect.width / 2;
      const y2 = targetRect.top + targetRect.height / 2;
      const distance = Math.hypot(x2 - x1, y2 - y1);
      if (!best || distance < best.distance) best = { x1, y1, x2, y2, distance };
    });
  });

  return best;
}
const PANEL_WIDTH = 420;
const VIEWPORT_GAP = 16;
const EMPTY_SIMULATION_STEP = {
  selector: "[data-user-guide-empty-preview]",
  deferred: true,
  titleKey: "UserGuide.emptySimulation.title",
  textKey: "UserGuide.emptySimulation.text",
};

const SIMULATION_META = {
  resource: { icon: "dns", item: "demo-resource-01", status: "Running", detail: "CPU 2 · RAM 4 GB · Disk 40 GB" },
  review: { icon: "fact_check", item: "REQ-2026-001", status: "Pending review", detail: "Applicant · Requested resource · Reason" },
  configure: { icon: "tune", item: "Example setting", status: "Configured", detail: "Connection · Permissions · Default values" },
  monitor: { icon: "monitor_heart", item: "System metric", status: "Healthy", detail: "Current value · Trend · Alert threshold" },
  teaching: { icon: "school", item: "Example course", status: "Draft", detail: "Students · Weeks · Learning environment" },
  learning: { icon: "menu_book", item: "Example lesson", status: "In progress", detail: "Instructions · Machine · Assignment" },
  workflow: { icon: "account_tree", item: "Example workflow", status: "Ready", detail: "Source · Target · Rules" },
  request: { icon: "assignment", item: "Example request", status: "Draft", detail: "Requirements · Schedule · Review" },
  explore: { icon: "dashboard", item: "Example item", status: "Available", detail: "Overview · Details · Next action" },
};

function EmptyGuideSimulation({ profile, title, t }) {
  const meta = SIMULATION_META[profile] ?? SIMULATION_META.explore;
  return createPortal(
    <div className={styles.emptySimulation} data-user-guide-empty-preview="">
      <div className={styles.emptySimulationNotice}>
        <MIcon name="visibility" size={16} />
        <span>{t("UserGuide.emptySimulation.badge")}</span>
      </div>
      <div className={styles.emptySimulationWindow}>
        <div className={styles.emptySimulationBar}><i /><i /><i /><strong>{title}</strong></div>
        <div className={styles.emptySimulationBody}>
          <div className={styles.emptySimulationList}>
            <button type="button" className={styles.emptySimulationRow}>
              <MIcon name={meta.icon} size={21} />
              <span><strong>{meta.item}</strong><small>{meta.status}</small></span>
              <MIcon name="chevron_right" size={20} />
            </button>
            <span /><span />
          </div>
          <div className={styles.emptySimulationDetail}>
            <div className={styles.emptySimulationTabs}><b>{t("UserGuide.emptySimulation.overview")}</b><span>{t("UserGuide.emptySimulation.settings")}</span><span>{t("UserGuide.emptySimulation.history")}</span></div>
            <h3>{meta.item}</h3>
            <p>{meta.detail}</p>
            <div className={styles.emptySimulationFields}><i /><i /><i /></div>
            <button type="button">{t("UserGuide.emptySimulation.primaryAction")}</button>
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function getPanelPosition() {
  const width = Math.min(PANEL_WIDTH, window.innerWidth - VIEWPORT_GAP * 2);

  return {
    left: Math.max(VIEWPORT_GAP, window.innerWidth - width - VIEWPORT_GAP),
    top: VIEWPORT_GAP,
    width,
    side: "pinned",
  };
}

export default function UserGuide() {
  const { t } = useTranslation("common");
  const location = useLocation();
  const { user } = useAuth();
  const isStudent = user?.role === "student" && !user?.is_superuser;
  const guide = useMemo(() => {
    const routeGuide = getRouteGuide(location.pathname);
    if (!routeGuide) return null;
    const detailed = getDetailedGuide(location.pathname, location.search);
    if (!detailed || (location.pathname === "/dashboard" && !isStudent)) return routeGuide;
    return {
      ...routeGuide,
      ...detailed,
      generic: false,
      guideVersion: "v8",
    };
  }, [isStudent, location.pathname, location.search]);
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState(0);
  const [pageTitle, setPageTitle] = useState("");
  const [targetRect, setTargetRect] = useState(null);
  const [gestureLine, setGestureLine] = useState(null);
  const [slot, setSlot] = useState(null);
  const [hasEmptyState, setHasEmptyState] = useState(false);
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
    const steps = guide.generic && hasEmptyState
      ? [...guide.steps, EMPTY_SIMULATION_STEP]
      : guide.steps;
    return steps.filter((item) => {
      if (item.deferred) return true;
      if (item.conditionSelector && !document.querySelector(item.conditionSelector)) return false;
      const targetExists = document.querySelector(item.selector);
      if (item.optional) return targetExists;
      return targetExists
        || (item.activateSelector && document.querySelector(item.activateSelector));
    });
  }, [guide, hasEmptyState, open]);

  const current = availableSteps[step] ?? availableSteps[0];
  const showEmptySimulation = open && guide?.generic && current?.selector === EMPTY_SIMULATION_STEP.selector;
  const storageKey = guide
    ? `skylab:user-guide:${guide.guideVersion ?? "v5"}:${user?.id ?? user?.email ?? "user"}:${guide.id}`
    : null;
  const isLast = step >= availableSteps.length - 1;

  const displayTitle = guide?.titleKey ? t(guide.titleKey) : pageTitle || t("UserGuide.defaultPageTitle");

  useEffect(() => {
    setOpen(false);
    setStep(0);
    setTargetRect(null);
    setGestureLine(null);
  }, [guide?.id]);

  useEffect(() => {
    if (!open || !guide?.generic) {
      setHasEmptyState(false);
      return undefined;
    }
    const sync = () => setHasEmptyState(Boolean(document.querySelector("[data-empty-state]")));
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.body, { childList: true, subtree: true });
    return () => observer.disconnect();
  }, [guide?.generic, open]);

  useEffect(() => {
    window.dispatchEvent(new CustomEvent("skylab:user-guide-state", {
      detail: { id: guide?.id ?? null, open: Boolean(open) },
    }));
    return () => {
      window.dispatchEvent(new CustomEvent("skylab:user-guide-state", {
        detail: { id: guide?.id ?? null, open: false },
      }));
    };
  }, [guide?.id, open]);

  useEffect(() => {
    if (!guide || !storageKey) return undefined;

    let chained = false;
    try {
      chained = sessionStorage.getItem("skylab:user-guide-chain") === guide.id;
      if (chained) sessionStorage.removeItem("skylab:user-guide-chain");
    } catch {
      // The guide remains available manually if session storage is unavailable.
    }
    if ((!guide.autoStart || !isStudent) && !chained) return undefined;

    try {
      if (!chained && localStorage.getItem(storageKey) === "completed") return undefined;
    } catch {
      // 儲存空間不可用時，仍保留學生首次進入頁面的主動導覽。
    }

    // 頁面資料可能還在載入，等到至少一個導覽目標進 DOM 再開啟，
    // 否則 availableSteps 會以空陣列被 memo 住，之後手動點擊也打不開
    let timer = null;
    let attempts = 0;
    let entered = !guide.enterSelector;
    const tryOpen = () => {
      if (!entered && guide.enterTargetSelector && document.querySelector(guide.enterTargetSelector)) {
        entered = true;
      }
      if (!entered) {
        const trigger = document.querySelector(guide.enterSelector);
        if (trigger) {
          trigger.click();
          entered = true;
        }
      }
      if (entered && guide.steps.some((item) => document.querySelector(item.selector))) {
        setPageTitle(document.querySelector("h1")?.textContent?.trim() ?? "");
        setStep(0);
        setOpen(true);
        return;
      }
      if (attempts < 40) {
        attempts += 1;
        timer = window.setTimeout(tryOpen, 100);
      }
    };
    timer = window.setTimeout(tryOpen, 500);

    return () => window.clearTimeout(timer);
  }, [guide?.autoStart, guide?.enterSelector, guide?.enterTargetSelector, guide?.id, guide?.steps, isStudent, storageKey]);

  useLayoutEffect(() => {
    if (!open || !current) {
      setTargetRect(null);
      setGestureLine(null);
      return undefined;
    }

    // 離開上一步時，若該步有 deactivateSelector（例如關閉導覽開啟的彈窗），先點擊關閉
    const prevStep = prevStepRef.current;
    if (prevStep && prevStep !== current && prevStep.deactivateSelector) {
      document.querySelector(prevStep.deactivateSelector)?.click();
    }
    prevStepRef.current = current;

    setTargetRect(null);
    setGestureLine(null);
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
      setGestureLine(current.demo === "drag-connection" ? getFirewallDragGeometry() : null);
    };

    const activate = current.activateSelector
      ? document.querySelector(current.activateSelector)
      : null;
    if (activate && activate.getAttribute("aria-selected") !== "true") activate.click();

    const attachTarget = () => {
      target = document.querySelector(current.selector);
      if (!target && targetAttempts < 40) {
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
      setGestureLine(null);
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
    document.querySelector(guide.cleanupSelector)?.click();
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
      /* 分頁已改用共用 SegmentedControl（aria-pressed），不再是 tablist 的 aria-selected */
      originalAiTab.current = document.querySelector('[data-guide-tab][aria-pressed="true"]')?.dataset.guideTab ?? null;
    }
    // 先關再開：availableSteps 以 open 為 memo 依賴，重開才會用當下 DOM 重算，
    // 也讓 auto-start 搶跑失敗後（open 已為 true）的點擊仍能生效
    setOpen(false);
    setStep(0);
    const trigger = guide.enterSelector ? document.querySelector(guide.enterSelector) : null;
    trigger?.click();
    window.setTimeout(() => {
      setPageTitle(document.querySelector("h1")?.textContent?.trim() ?? "");
      setOpen(true);
    }, trigger ? 260 : 80);
  };

  const next = () => {
    if (current.performSelector) {
      try {
        if (current.chainToGuide) sessionStorage.setItem("skylab:user-guide-chain", current.chainToGuide);
      } catch {
        // Cross-page continuation is best-effort when session storage is unavailable.
      }
      const trigger = document.querySelector(current.performSelector);
      if (trigger) {
        trigger.click();
        return;
      }
    }
    if (isLast) complete();
    else setStep((value) => value + 1);
  };

  const panelPosition = targetRect ? getPanelPosition() : null;

  return (
    <>
      {showEmptySimulation && <EmptyGuideSimulation profile={guide.profile} title={displayTitle} t={t} />}
      {/* 只掛在頁首標題旁的 slot；找不到（頁面還在載入、沒有頁首）就不顯示，
          不再退回浮在右下角，免得載入中閃現、疊在 AI 助手鈕上 */}
      {slot && createPortal(
        <button
          type="button"
          data-user-guide-trigger=""
          className={styles.helpButton}
          onClick={start}
          aria-label={t("UserGuide.openGuideAriaLabel", { title: displayTitle })}
          title={t("UserGuide.guideTitleAttr", { title: displayTitle })}
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

          {current.demo === "drag-connection" && gestureLine && (
            <div className={styles.gestureDemo} aria-hidden="true">
              <svg className={styles.gestureSvg}>
                <line
                  className={styles.gestureLineBase}
                  x1={gestureLine.x1}
                  y1={gestureLine.y1}
                  x2={gestureLine.x2}
                  y2={gestureLine.y2}
                />
                <line
                  className={styles.gestureLine}
                  pathLength="1"
                  x1={gestureLine.x1}
                  y1={gestureLine.y1}
                  x2={gestureLine.x2}
                  y2={gestureLine.y2}
                />
              </svg>
              <span
                className={`${styles.gesturePoint} ${styles.gesturePointStart}`}
                style={{ left: gestureLine.x1, top: gestureLine.y1 }}
              />
              <span
                className={`${styles.gesturePoint} ${styles.gesturePointEnd}`}
                style={{ left: gestureLine.x2, top: gestureLine.y2 }}
              />
              <span
                className={styles.gestureCursor}
                style={{
                  left: gestureLine.x1,
                  top: gestureLine.y1,
                  "--guide-drag-x": `${gestureLine.x2 - gestureLine.x1}px`,
                  "--guide-drag-y": `${gestureLine.y2 - gestureLine.y1}px`,
                }}
              >
                <MIcon name="near_me" size={28} />
              </span>
            </div>
          )}

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
                <small>{t("UserGuide.tourSubtitle", { title: displayTitle })}</small>
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
              {/* 進度點順序固定，用索引當 key；selector 會重複（如防火牆導覽兩步都指 firewall-map） */}
              {availableSteps.map((item, index) => (
                <span key={index} className={index <= step ? styles.progressActive : ""} />
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

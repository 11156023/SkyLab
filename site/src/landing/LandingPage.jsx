import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import gsap from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import CampusScene from "./CampusScene";
import { CAMERA_DURATION, CAMERA_LEAD, SECTIONS, TOTAL_LENGTH, cameraState } from "./cameraScript";
import { LANGUAGES, languageInfo } from "../languages";
import { CONTACT_EMAIL, DOCS_URL, GITHUB_URL } from "../config";
import STATS from "../stats.json";
import styles from "./LandingPage.module.scss";

gsap.registerPlugin(ScrollTrigger);

const MODE_STORAGE_KEY = "skylab.landing.mode";

function contactHref(t) {
  return `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent(t("contact.subject"))}`;
}

/* 日/夜切換鈕的圖示:官網不載圖示字型,直接畫 SVG */
function ModeIcon({ mode }) {
  return (
    <svg className={styles.modeIcon} viewBox="0 0 24 24" aria-hidden="true">
      {mode === "night" ? (
        <>
          <circle cx="12" cy="12" r="4.2" />
          <path d="M12 2.5v2.4M12 19.1v2.4M2.5 12h2.4M19.1 12h2.4M5.3 5.3l1.7 1.7M17 17l1.7 1.7M5.3 18.7 7 17M17 7l1.7-1.7" />
        </>
      ) : (
        <path d="M20 14.6A8.2 8.2 0 0 1 9.4 4a8.2 8.2 0 1 0 10.6 10.6Z" />
      )}
    </svg>
  );
}

function HudCard({ title, wide = false, children }) {
  return (
    <article className={`${styles.hudCard} ${wide ? styles.hudCardWide : ""}`} data-hud>
      {title && <h3 className={styles.hudTitle}>{title}</h3>}
      {children}
    </article>
  );
}

function HeroContent({ t }) {
  return (
    <div className={styles.hero}>
      <p className={styles.heroKicker}>{t("hero.tagline")}</p>
      <h1 className={styles.heroTitle}>{t("brand")}</h1>
      <p className={styles.heroSub}>{t("hero.sub")}</p>
      <div className={styles.scrollHint}>
        <span>{t("scrollHint")}</span>
        <span className={styles.scrollHintBar} aria-hidden="true" />
      </div>
    </div>
  );
}

/* S1:只放一句大標,把舞台留給校園上空的私有雲與光纖 */
function OverviewContent({ t }) {
  return (
    <div className={styles.overviewBlock}>
      <h2 className={styles.overviewTitle} data-hud>{t("overview.title")}</h2>
    </div>
  );
}

function LifecycleContent({ t, stats }) {
  const steps = t("lifecycle.steps", { returnObjects: true });
  return (
    <div className={styles.hudCol}>
      <HudCard>
        <h2 className={styles.sectionTitle}>{t("lifecycle.title")}</h2>
        <p className={styles.sectionDesc}>{t("lifecycle.desc")}</p>
        <ol className={styles.stepper}>
          {steps.map((step) => <li key={step}>{step}</li>)}
        </ol>
      </HudCard>
      <HudCard title={t("lifecycle.templates")}>
        <ul className={styles.miniList}>
          {(stats?.templates ?? []).map((tpl) => (
            <li key={tpl.name}>
              <span>{tpl.name}</span>
              <span className={styles.miniTag}>{tpl.kind}</span>
            </li>
          ))}
        </ul>
      </HudCard>
    </div>
  );
}

function WorkflowContent({ t, stats }) {
  const steps = t("workflow.steps", { returnObjects: true });
  const used = stats?.capacity?.usedPercent ?? 0;
  return (
    <div className={styles.hudCol}>
      <HudCard>
        <h2 className={styles.sectionTitle}>{t("workflow.title")}</h2>
        <p className={styles.sectionDesc}>{t("workflow.desc")}</p>
        <ol className={styles.stepper}>
          {steps.map((step) => <li key={step}>{step}</li>)}
        </ol>
      </HudCard>
      <HudCard title={t("workflow.capacity")}>
        <div className={styles.gaugeTrack}>
          <div className={styles.gaugeFill} style={{ width: `${used}%` }} />
        </div>
        <span className={styles.gaugeValue}>{used}%</span>
      </HudCard>
    </div>
  );
}

function ClassroomContent({ t, stats }) {
  const seats = stats?.classroom?.seats ?? 40;
  const online = stats?.classroom?.online ?? 0;
  return (
    <div className={styles.hudCol}>
      <HudCard>
        <h2 className={styles.sectionTitle}>{t("classroom.title")}</h2>
        <p className={styles.sectionDesc}>{t("classroom.desc")}</p>
        <div className={styles.chipRow}>
          {stats?.classroom?.name && <span className={styles.chip}>{stats.classroom.name}</span>}
          <span className={styles.chip}>{t("classroom.aiJudge")}</span>
        </div>
      </HudCard>
      <HudCard title={t("classroom.monitor")}>
        <div className={styles.seatHudGrid}>
          {Array.from({ length: seats }, (_, i) => (
            <span key={i} className={i < online ? styles.seatOn : styles.seatOff} />
          ))}
        </div>
        <span className={styles.hudMeta}>{t("classroom.online", { online, seats })}</span>
      </HudCard>
    </div>
  );
}

function ServerGlyph() {
  return (
    <g className={styles.topoServer}>
      <rect x="-6" y="-5.5" width="12" height="4.6" rx="1" />
      <rect x="-6" y="0.9" width="12" height="4.6" rx="1" />
      <circle cx="-3.4" cy="-3.2" r="0.9" />
      <circle cx="-3.4" cy="3.2" r="0.9" />
    </g>
  );
}

/** 防火牆迷你拓撲:Internet → 防火牆 → 兩台 VM,一條放行(流動虛線)、一條被阻擋(✕) */
function TopoMini({ t }) {
  return (
    <>
      <svg className={styles.topoMini} viewBox="0 0 260 150" aria-hidden="true">
        <line className={styles.topoFlow} x1="52" y1="64" x2="108" y2="64" />
        <line className={styles.topoFlow} x1="141" y1="57" x2="207" y2="36" />
        <line className={styles.topoBlocked} x1="141" y1="71" x2="207" y2="101" />
        <g className={styles.topoBlockBadge} transform="translate(174, 86)">
          <circle r="7" />
          <path d="M -2.6 -2.6 L 2.6 2.6 M 2.6 -2.6 L -2.6 2.6" />
        </g>
        <g transform="translate(36, 64)">
          <circle className={styles.topoNode} r="14" />
          <g className={styles.topoGlyph}>
            <circle r="6.5" />
            <ellipse rx="6.5" ry="2.7" />
            <line x1="-6.5" y1="0" x2="6.5" y2="0" />
          </g>
          <text className={styles.topoLabel} y="30">{t("network.nodeInternet")}</text>
        </g>
        <g transform="translate(126, 64)">
          <circle className={styles.topoPulse} r="16" />
          <circle className={`${styles.topoNode} ${styles.topoHub}`} r="16" />
          <path
            className={styles.topoShield}
            d="M 0 -8 L 6.5 -5.2 L 6.5 0.6 C 6.5 4.8 3.5 7 0 9 C -3.5 7 -6.5 4.8 -6.5 0.6 L -6.5 -5.2 Z"
          />
          <path className={styles.topoShieldTick} d="M -2.6 0.2 L -0.7 2.4 L 3 -2" />
          <text className={styles.topoLabel} y="32">{t("network.nodeFirewall")}</text>
        </g>
        <g transform="translate(222, 30)">
          <circle className={styles.topoNode} r="12" />
          <ServerGlyph />
          <text className={styles.topoLabel} y="28">vm-2481</text>
        </g>
        <g transform="translate(222, 108)">
          <circle className={styles.topoNode} r="12" />
          <ServerGlyph />
          <text className={styles.topoLabel} y="28">vm-2482</text>
        </g>
      </svg>
      <div className={styles.topoLegend}>
        <span><i className={styles.legendAllow} />{t("network.legendAllow")}</span>
        <span><i className={styles.legendBlock} />{t("network.legendBlock")}</span>
      </div>
    </>
  );
}

function NetworkContent({ t, stats }) {
  const net = stats?.network;
  return (
    <div className={styles.hudCol}>
      <HudCard>
        <h2 className={styles.sectionTitle}>{t("network.title")}</h2>
        <p className={styles.sectionDesc}>{t("network.desc")}</p>
        <ul className={styles.kvList}>
          <li><span>{t("network.firewall")}</span><strong>{net?.firewallRules ?? "—"}</strong></li>
          <li><span>{t("network.nat")}</span><strong>{net?.natRules ?? "—"}</strong></li>
          <li><span>{t("network.proxy")}</span><strong>{net?.proxyRoutes ?? "—"}</strong></li>
        </ul>
      </HudCard>
      <HudCard title={t("network.topology")}>
        <TopoMini t={t} />
      </HudCard>
    </div>
  );
}

function AiContent({ t, stats }) {
  const candidates = stats?.ai?.candidates ?? [];
  return (
    <div className={styles.hudCol}>
      <HudCard>
        <h2 className={styles.sectionTitle}>{t("ai.title")}</h2>
        <p className={styles.sectionDesc}>{t("ai.desc")}</p>
        <blockquote className={styles.aiQuote}>{t("ai.recommendation")}</blockquote>
        <p className={styles.hudMeta}>{t("ai.proxyCard")}</p>
      </HudCard>
      <HudCard title={t("ai.placement")}>
        {candidates.map((c) => (
          <div className={styles.scoreRow} key={c.node}>
            <span>{c.node}</span>
            <div className={styles.scoreTrack}>
              <div className={styles.scoreFill} style={{ width: `${c.score}%` }} />
            </div>
            <strong>{c.score}</strong>
          </div>
        ))}
      </HudCard>
    </div>
  );
}

function TerminalContent({ t }) {
  return (
    <HudCard wide>
      <h2 className={styles.sectionTitle}>{t("terminal.title")}</h2>
      <p className={styles.sectionDesc}>{t("terminal.desc")}</p>
      <div className={styles.terminal} aria-hidden="true">
        <div className={styles.terminalBar}><span /><span /><span /></div>
        <pre>
          <span className={styles.tPrompt}>$</span> ssh student@vm-2481.skylab{"\n"}
          Welcome to Ubuntu 24.04 LTS (GNU/Linux 6.8.0-45-generic x86_64){"\n"}
          <span className={styles.tPrompt}>student@vm-2481:~$</span> <span className={styles.tCursor} />
        </pre>
      </div>
    </HudCard>
  );
}

/* 實際操作:平台真實介面錄成的影片(website/scripts/manual-shots/clips.mjs 產生),
   放在瀏覽器視窗框裡輪播。接近畫面才載入與播放;減少動態效果時不自動播放,改給播放控制 */
export const DEMOS = [
  { id: "console", path: "/my-resources" },
  { id: "class", path: "/class-management" },
  { id: "review", path: "/request-review" },
  { id: "firewall", path: "/firewall" },
];

function DemoContent({ t, lang }) {
  const rootRef = useRef(null);
  const videoRef = useRef(null);
  const [active, setActive] = useState(0);
  const [progress, setProgress] = useState(0);
  const [near, setNear] = useState(false);
  const [reduceMotion, setReduceMotion] = useState(false);
  const demo = DEMOS[active];
  const base = `/demo/${lang === "zh-TW" ? "zh-TW" : "en"}`;

  useLayoutEffect(() => {
    setReduceMotion(window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }, []);

  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof IntersectionObserver === "undefined") return undefined;
    const io = new IntersectionObserver(([entry]) => setNear(entry.isIntersecting), { rootMargin: "300px 0px" });
    io.observe(el);
    return () => io.disconnect();
  }, []);

  /* 換片或進出畫面時播放/暫停;muted 要設在屬性上,瀏覽器才允許自動播放 */
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.muted = true;
    if (near && !reduceMotion) video.play().catch(() => {});
    else video.pause();
  }, [near, active, reduceMotion]);

  const select = (index) => {
    setActive(index);
    setProgress(0);
  };

  return (
    <div className={styles.demo} ref={rootRef}>
      <h2 className={styles.demoTitle} data-hud>{t("demo.title")}</h2>
      <div className={styles.browser} data-hud>
        <div className={styles.browserBar} aria-hidden="true">
          <span /><span /><span />
          <div className={styles.browserUrl}>ntub.skylab-tw.com{demo.path}</div>
        </div>
        <video
          key={`${base}/${demo.id}`}
          ref={videoRef}
          className={styles.demoVideo}
          src={near ? `${base}/${demo.id}.mp4` : undefined}
          poster={`${base}/${demo.id}.jpg`}
          muted
          playsInline
          preload="none"
          controls={reduceMotion}
          aria-label={t(`demo.${demo.id}.title`)}
          onTimeUpdate={(e) => {
            const v = e.currentTarget;
            if (v.duration) setProgress(v.currentTime / v.duration);
          }}
          onEnded={() => select((active + 1) % DEMOS.length)}
        />
      </div>
      <div className={styles.demoTabs} role="tablist" aria-label={t("demo.title")} data-hud>
        {DEMOS.map((d, index) => (
          <button
            key={d.id}
            type="button"
            role="tab"
            aria-selected={index === active}
            className={`${styles.demoTab} ${index === active ? styles.demoTabActive : ""}`}
            onClick={() => select(index)}
          >
            <strong>{t(`demo.${d.id}.title`)}</strong>
            <span>{t(`demo.${d.id}.caption`)}</span>
            <i className={styles.demoTabProgress} style={{ transform: `scaleX(${index === active ? progress : 0})` }} />
          </button>
        ))}
      </div>
    </div>
  );
}

/* 收尾:雲海合攏後浮出的 CTA,構圖呼應開場 hero。
   官網的訪客是想到校試用的學校:主要動作是寫信聯繫,其次看原始碼與部署指南 */
function OutroContent({ t, lang }) {
  const docsHref = `${DOCS_URL}${languageInfo(lang).docsPath}guides/deployment`;
  return (
    <div className={styles.outro}>
      <p className={styles.heroKicker} data-hud>{t("hero.tagline")}</p>
      <h2 className={styles.outroTitle} data-hud>{t("outro.title")}</h2>
      <p className={styles.outroNote} data-hud>{t("contact.note")}</p>
      <div className={styles.ctaRow} data-hud>
        <a className={styles.btnPrimary} href={contactHref(t)}>{t("contact.cta")}</a>
        <a className={styles.btnSecondary} href={GITHUB_URL} target="_blank" rel="noreferrer">
          {t("outro.githubCta")}
        </a>
        <a className={styles.btnSecondary} href={docsHref} target="_blank" rel="noreferrer">
          {t("outro.docsCta")}
        </a>
      </div>
      <p className={styles.footer} data-hud>
        {t("footer")}
        <span className={styles.footerSep} aria-hidden="true">·</span>
        {t("contact.emailLabel")}{" "}
        <a className={styles.footerLink} href={contactHref(t)}>{CONTACT_EMAIL}</a>
      </p>
    </div>
  );
}

const ALIGN_CLASS = { left: "alignLeft", right: "alignRight", center: "alignCenter" };
const VALIGN_CLASS = { top: "valignTop", bottom: "valignBottom", center: "" };

export default function LandingPage() {
  const { t, i18n } = useTranslation("landing");
  const rootRef = useRef(null);
  const worldRef = useRef(null);
  const cloudLayerRef = useRef(null);
  const hazeRef = useRef(null);
  const stats = STATS;
  const [viewportTick, setViewportTick] = useState(0);
  /* 日/夜模式:預設夜間(主視覺),選擇記在 localStorage。
     頁面是預渲染再 hydrate 的,首次 render 要跟預渲染的夜間一致,讀到日間再於 paint 前切過去 */
  const [mode, setMode] = useState("night");
  useLayoutEffect(() => {
    try {
      if (window.localStorage.getItem(MODE_STORAGE_KEY) === "day") setMode("day");
    } catch {
      // 無痕模式等 localStorage 不可用時維持夜間
    }
  }, []);

  const toggleMode = () => {
    setMode((current) => {
      const next = current === "night" ? "day" : "night";
      try {
        window.localStorage.setItem(MODE_STORAGE_KEY, next);
      } catch {
        // 無痕模式等 localStorage 不可用時僅本次生效
      }
      return next;
    });
  };

  /* 行動版與 prefers-reduced-motion 一律關閉運鏡:場景停在全景、HUD 直接可見。
     只在掛載時判定一次;跨過門檻的視窗縮放屬邊緣情境,重新整理即可。
     預渲染沒有視窗,一律畫成桌機的運鏡版(多數訪客,hydrate 時不必換畫面),行動版在 paint 前切成靜態 */
  const [motionEnabled, setMotionEnabled] = useState(true);
  useLayoutEffect(() => {
    setMotionEnabled(
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches && window.innerWidth >= 768,
    );
  }, []);

  /* 視窗尺寸變更後重建運鏡 timeline(相機位移量與視窗大小綁定) */
  useEffect(() => {
    if (!motionEnabled) return undefined;
    let timer;
    const onResize = () => {
      clearTimeout(timer);
      timer = setTimeout(() => setViewportTick((n) => n + 1), 200);
    };
    window.addEventListener("resize", onResize);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("resize", onResize);
    };
  }, [motionEnabled]);

  useEffect(() => {
    const world = worldRef.current;
    if (!world) return undefined;

    if (!motionEnabled) {
      const applyStatic = () => {
        const { scale, x, y } = cameraState(SECTIONS[1], window.innerWidth, window.innerHeight);
        world.style.transform = `translate(${x}px, ${y}px) scale(${scale})`;
      };
      applyStatic();
      window.addEventListener("resize", applyStatic);
      return () => window.removeEventListener("resize", applyStatic);
    }

    const ctx = gsap.context(() => {
      const vw = window.innerWidth;
      const vh = window.innerHeight;
      const pad = { done: 0 }; // 只拿來撐 timeline 長度;屬性要先存在,gsap 才不會警告 Missing plugin

      gsap.set(world, { ...cameraState(SECTIONS[0], vw, vh), transformOrigin: "0 0" });

      const tl = gsap.timeline({
        defaults: { ease: "power1.inOut" },
        scrollTrigger: {
          trigger: rootRef.current,
          start: "top top",
          end: "bottom bottom",
          scrub: 0.6,
        },
      });

      /* 可滾動距離＝內容高 − 一個視窗;timeline 以此為總長,
         段落 i 的 sticky 在滾到 cumStart(i) 時 pin 住,運鏡壓在交界處完成 */
      const scrollUnits = TOTAL_LENGTH - 1;
      let cum = 0;
      SECTIONS.forEach((section, i) => {
        if (i > 0) {
          const at = Math.max(Math.min(cum - CAMERA_LEAD, scrollUnits - CAMERA_DURATION), 0);
          tl.to(world, { ...cameraState(section, vw, vh), duration: CAMERA_DURATION }, at);
        }
        cum += section.length;
      });
      tl.set(pad, { done: 1 }, scrollUnits); // 撐滿總長,讓 scrub 與段落邊界對齊

      /* S0→S1:雲層往兩側散開、霧面退場、場景從雲隙間浮現(「穿雲降落」) */
      const cloudAt = Math.max(SECTIONS[0].length - CAMERA_LEAD, 0);
      const clouds = cloudLayerRef.current ? Array.from(cloudLayerRef.current.children) : [];
      clouds.forEach((cloud, i) => {
        tl.to(
          cloud,
          { xPercent: i % 2 === 0 ? -80 : 80, yPercent: i === 2 ? 40 : -15, opacity: 0, duration: CAMERA_DURATION },
          cloudAt,
        );
      });
      tl.to(world, { opacity: 1, duration: CAMERA_DURATION * 0.9 }, cloudAt + 0.05);
      if (hazeRef.current) {
        tl.to(hazeRef.current, { opacity: 0, duration: CAMERA_DURATION }, cloudAt + 0.05);
      }

      /* 收尾(outro):鏡頭拉回全景、私有雲亮起後,雲海從兩側合攏蓋住校園——開場穿雲降落的倒帶,
         CTA 最後浮在雲海上,演出「把實驗室搬上雲」。霧面層同時回來當文字底(日夜各有配色) */
      const outro = SECTIONS[SECTIONS.length - 1];
      const outroSec = rootRef.current.querySelector(`section[data-section="${outro.id}"]`);
      const closeAt = scrollUnits - 0.55;
      clouds.forEach((cloud) => {
        tl.to(cloud, { xPercent: 0, yPercent: 0, opacity: 1, duration: 0.4, ease: "power2.out" }, closeAt);
      });
      tl.to(world, { opacity: 0.15, duration: 0.35 }, closeAt + 0.1);
      if (hazeRef.current) {
        tl.to(hazeRef.current, { opacity: 1, duration: 0.35 }, closeAt + 0.05);
      }
      const outroContent = outroSec ? outroSec.querySelectorAll("[data-hud]") : [];
      if (outroContent.length) {
        tl.fromTo(
          outroContent,
          { opacity: 0, y: 30 },
          { opacity: 1, y: 0, duration: 0.12, stagger: 0.03, ease: "power2.out" },
          closeAt + 0.3,
        );
      }

      /* 段落連動:目前段落寫進根節點 data-active,場景樣式據此點亮
         (焦點建築發光、流量光線增亮、terminal 段窗戶轉暖) */
      SECTIONS.forEach((section) => {
        const el = rootRef.current.querySelector(`section[data-section="${section.id}"]`);
        if (!el) return;
        ScrollTrigger.create({
          trigger: el,
          start: "top 55%",
          end: "bottom 55%",
          onToggle: (self) => {
            if (self.isActive) rootRef.current.setAttribute("data-active", section.id);
          },
        });
      });

      /* 各段 HUD 卡進場 */
      rootRef.current.querySelectorAll("section[data-section]").forEach((sec) => {
        if (sec === outroSec) return; // 收尾由上面的 timeline 在雲海合攏後才浮出
        const cards = sec.querySelectorAll("[data-hud]");
        if (!cards.length) return;
        gsap.from(cards, {
          opacity: 0,
          y: 40,
          duration: 0.7,
          stagger: 0.15,
          ease: "power2.out",
          scrollTrigger: { trigger: sec, start: "top 60%" },
        });
      });
    }, rootRef);

    return () => ctx.revert();
  }, [motionEnabled, viewportTick]);

  /* S4:教室座位隨滾動逐排點亮(=整班機器上線)。
     亮的座位(data-seat)數量來自 stats.json;獨立成一段,運鏡開關或視窗尺寸變了就重建 */
  useEffect(() => {
    if (!motionEnabled) return undefined;
    const ctx = gsap.context(() => {
      const classroomSec = rootRef.current.querySelector('section[data-section="classroom"]');
      const seats = rootRef.current.querySelectorAll("[data-seat]");
      if (!classroomSec || !seats.length) return;
      gsap.to(seats, {
        fill: "#b7d3ff",
        ease: "none",
        stagger: 0.02,
        scrollTrigger: { trigger: classroomSec, start: "top 65%", end: "center 40%", scrub: true },
      });
    }, rootRef);
    return () => ctx.revert();
  }, [motionEnabled, viewportTick]);

  const contentFor = (id) => {
    switch (id) {
      case "hero": return <HeroContent t={t} />;
      case "overview": return <OverviewContent t={t} />;
      case "lifecycle": return <LifecycleContent t={t} stats={stats} />;
      case "workflow": return <WorkflowContent t={t} stats={stats} />;
      case "classroom": return <ClassroomContent t={t} stats={stats} />;
      case "network": return <NetworkContent t={t} stats={stats} />;
      case "ai": return <AiContent t={t} stats={stats} />;
      case "terminal": return <TerminalContent t={t} />;
      case "demo": return <DemoContent t={t} lang={i18n.language} />;
      case "outro": return <OutroContent t={t} lang={i18n.language} />;
      default: return null;
    }
  };

  return (
    <div
      ref={rootRef}
      className={`${styles.landing} ${motionEnabled ? "" : styles.noMotion}`}
      data-active="hero"
      data-mode={mode}
    >
      <div className={styles.sceneViewport} aria-hidden="true">
        {/* 夜空層:銀河帶、極光、流星、閃爍星;固定在視口,不跟相機動(日間整組隱藏) */}
        <div className={styles.skyLayer}>
          <div className={styles.starsTwinkle} />
          <div className={styles.milkyWay} />
          <div className={`${styles.aurora} ${styles.auroraA}`} />
          <div className={`${styles.aurora} ${styles.auroraB}`} />
          <span className={`${styles.meteor} ${styles.meteorA}`} />
          <span className={`${styles.meteor} ${styles.meteorB}`} />
        </div>
        {/* 運鏡模式下場景先隱藏,滾動穿雲時才淡入(靜態模式直接可見) */}
        <div className={styles.sceneWorld} ref={worldRef} style={{ opacity: motionEnabled ? 0 : 1 }}>
          <CampusScene stats={stats} />
        </div>
      </div>
      <div className={styles.vignette} aria-hidden="true" />
      <div className={styles.haze} ref={hazeRef} aria-hidden="true" />
      <div className={styles.cloudLayer} ref={cloudLayerRef} aria-hidden="true">
        <div className={`${styles.cloud} ${styles.cloudA}`} />
        <div className={`${styles.cloud} ${styles.cloudB}`} />
        <div className={`${styles.cloud} ${styles.cloudC}`} />
        <div className={`${styles.cloud} ${styles.cloudD}`} />
        <div className={`${styles.cloud} ${styles.cloudE}`} />
        <div className={styles.cloudSea} />
      </div>

      <header className={styles.topBar}>
        <span className={styles.brand}>{t("brand")}</span>
        <div className={styles.topActions}>
          {/* 每個語言是獨立的預渲染網址,切換語言就是換頁 */}
          {LANGUAGES.map((lang) => (
            <a
              key={lang.code}
              href={lang.path}
              hrefLang={lang.code}
              aria-current={i18n.language === lang.code ? "page" : undefined}
              className={`${styles.langBtn} ${i18n.language === lang.code ? styles.langBtnActive : ""}`}
            >
              {lang.label}
            </a>
          ))}
          <button
            type="button"
            className={styles.langBtn}
            aria-label={t(mode === "night" ? "modeToDay" : "modeToNight")}
            onClick={toggleMode}
          >
            <ModeIcon mode={mode} />
          </button>
          <a className={styles.topContact} href={contactHref(t)}>{t("contact.cta")}</a>
        </div>
      </header>

      <main className={styles.sections}>
        {SECTIONS.map((section) => (
          <section
            key={section.id}
            data-section={section.id}
            className={styles.section}
            style={{ minHeight: `${section.length * 100}vh` }}
          >
            <div
              className={`${styles.sticky} ${styles[ALIGN_CLASS[section.align]]} ${
                styles[VALIGN_CLASS[section.valign]] ?? ""
              }`}
            >
              {contentFor(section.id)}
            </div>
          </section>
        ))}
      </main>
    </div>
  );
}

/* 段落道具:讓每段的焦點「看得出在做什麼」,而不只是亮起一棟方塊。
   - DataCenterNodes:機房屋頂的節點機櫃,台數＝概況的節點數;ai 段在上方長出放置分數柱、VM 落進最高分節點
   - WorkflowPipeline:宿舍 → 行政樓審核環 → 機房節點的申請管線,申請單過審核環由琥珀轉綠
   - AiCore:懸浮 AI 核心,ai 段光束掃向各節點
   - FirewallGate / AllowedPackets:校門缺口的防火牆光幕,綠色封包穿過、紅色封包撞牆彈回
   - PrivateCloud:hero/overview 校園上空的私有雲,光纖接到各棟屋頂
   - DataCenterInterior / LifecycleTheater:lifecycle 段機房牆面轉玻璃露出機櫃;
     屋頂範本模具演一輪 建立→快照→規格調整→回收(與 HUD 步驟同一條 CSS 時間軸)
   - ClassroomRoom:電腦教室挑空,桌機螢幕＝整班機器,台數/在線數來自 stats
   循環動畫一律用 SMIL(同一條文件時間軸,封包與閃光天然同步);
   各段專屬圖層(.pcLayer/.lcLayer/.wfLayer/.aiLayer/.netLayer)平時 visibility:hidden,由根節點 data-active 打開,
   運鏡關閉(.noMotion)時 data-active 不會變,自然不出現。 */
import { project, linePath, projectedLength, isoEllipse, P } from "./iso";
import {
  AI_CORE,
  CLASSROOM,
  CLOUD,
  CLOUD_LINKS,
  DATACENTER,
  GATE,
  LIFECYCLE,
  NODE,
  pipelinePoints,
} from "./sceneLayout";
import { IsoBox } from "./IsoShapes";
import styles from "./LandingPage.module.scss";

const fmt = (n) => n.toFixed(1);

/* ── 機房節點 ── */

/** 節點機櫃正面(南面)兩排狀態燈 */
function NodeLeds({ slot }) {
  const leds = [];
  const y = slot.y + NODE.d;
  for (let row = 0; row < 2; row += 1) {
    const z = slot.top - 9 - row * 9;
    for (let col = 0; col < 5; col += 1) {
      const x = slot.x + 6 + col * 7.5;
      leds.push(
        <polygon
          key={`${row}-${col}`}
          className={(row * 5 + col) % 3 === 0 ? styles.nodeLedOn : styles.nodeLed}
          points={P([[x, y, z], [x + 4, y, z], [x + 4, y, z + 3], [x, y, z + 3]])}
        />,
      );
    }
  }
  return leds;
}

export function DataCenterNodes({ slots, candidates, winner }) {
  const core = project(AI_CORE.x, AI_CORE.y, AI_CORE.z - 30);
  return (
    <g data-b="datacenter">
      {slots.map((slot, i) => (
        <g key={i} className={i === winner ? styles.nodeWinner : undefined}>
          <IsoBox x={slot.x} y={slot.y} w={NODE.w} d={NODE.d} h={NODE.h} z0={slot.top - NODE.h} className={styles.nodeBox} />
          <NodeLeds slot={slot} />
        </g>
      ))}

      {/* ai 段:核心光束掃向每台節點 */}
      <g className={styles.aiLayer}>
        {slots.map((slot, i) => {
          const [tx, ty] = project(slot.cx, slot.cy, slot.top);
          return (
            <path
              key={i}
              className={i === winner ? `${styles.aiBeam} ${styles.aiBeamWin}` : styles.aiBeam}
              d={`M ${fmt(core[0])} ${fmt(core[1])} L ${fmt(tx)} ${fmt(ty)}`}
            />
          );
        })}
      </g>

      {/* ai 段:放置分數柱(依序長高)＋分數,最後 VM 落進最高分節點 */}
      <g className={styles.aiLayer} data-b="ai">
        {slots.map((slot, i) => {
          const score = candidates?.[i]?.score;
          if (score == null) return null;
          const h = Math.max(score, 4) * 1.05;
          const [lx, ly] = project(slot.cx, slot.cy, slot.top + h + 12);
          const win = i === winner;
          return (
            <g key={i}>
              <g className={styles.aiBarGrow} style={{ transitionDelay: `${0.2 + i * 0.18}s` }}>
                <IsoBox
                  x={slot.cx - 6}
                  y={slot.cy - 6}
                  w={12}
                  d={12}
                  h={h}
                  z0={slot.top}
                  className={win ? `${styles.aiBar} ${styles.aiBarWin}` : styles.aiBar}
                />
              </g>
              <text
                className={win ? `${styles.aiScore} ${styles.aiScoreWin}` : styles.aiScore}
                style={{ transitionDelay: `${0.6 + i * 0.18}s` }}
                x={fmt(lx)}
                y={fmt(ly)}
              >
                {score}
              </text>
            </g>
          );
        })}
        {slots[winner] && (
          <g className={styles.aiVmDrop} style={{ transitionDelay: `${0.9 + slots.length * 0.18}s` }}>
            <IsoBox
              x={slots[winner].x + 4}
              y={slots[winner].y + NODE.d - 20}
              w={16}
              d={16}
              h={12}
              z0={slots[winner].top}
              className={styles.aiVm}
            />
          </g>
        )}
      </g>
    </g>
  );
}

/* ── 申請管線(workflow) ── */

const WF_DUR = 6; // 秒;三張申請單錯開 2 秒
const WF_GAP = 2;

/** 申請單:琥珀(待審)方塊上疊一顆綠色(已核准)方塊,過審核環時切換 */
function Packet({ path, keyPoints, begin }) {
  const timing = { dur: `${WF_DUR}s`, begin: `${begin}s`, repeatCount: "indefinite" };
  return (
    <g>
      <animateMotion {...timing} path={path} calcMode="linear" keyPoints={keyPoints} keyTimes="0;0.4;0.5;0.92;1" />
      <animate {...timing} attributeName="opacity" values="0;1;1;0" keyTimes="0;0.04;0.93;1" />
      <IsoBox x={-9} y={-9} w={18} d={18} h={12} className={styles.pkPending} />
      <g opacity="0">
        <animate {...timing} attributeName="opacity" calcMode="discrete" values="0;1" keyTimes="0;0.45" />
        <IsoBox x={-9} y={-9} w={18} d={18} h={12} className={styles.pkApproved} />
      </g>
    </g>
  );
}

export function WorkflowPipeline({ target }) {
  const { points, stampIndex } = pipelinePoints(target);
  const d = linePath(points);
  const f = (projectedLength(points.slice(0, stampIndex + 1)) / projectedLength(points)).toFixed(4);
  const keyPoints = `0;${f};${f};1;1`;
  const [sx, sy, sz] = points[stampIndex];
  const submitPad = isoEllipse(points[0][0], points[0][1], 24, points[0][2]);
  const stampPad = isoEllipse(sx, sy, 20, sz);
  const stampRing = isoEllipse(sx, sy, 26, sz + 26);
  const arrive = isoEllipse(target.cx, target.cy, 34, target.top);
  /* 閃光時刻:申請單本地時間 45%(轉綠)/92%(抵達節點),換算成 WF_GAP 週期的起點 */
  const stampBegin = ((WF_DUR * 0.45) % WF_GAP).toFixed(2);
  const arriveBegin = ((WF_DUR * 0.92) % WF_GAP).toFixed(2);
  const pulse = { dur: `${WF_GAP}s`, repeatCount: "indefinite", attributeName: "opacity" };

  return (
    <g className={styles.wfLayer}>
      <ellipse className={styles.wfPadSubmit} {...submitPad} />
      <ellipse className={styles.wfPadStamp} {...stampPad} />
      <path className={styles.wfTrackGlow} d={d} />
      <path className={styles.wfTrack} d={d} />

      {[0, 1, 2].map((i) => (
        <Packet key={i} path={d} keyPoints={keyPoints} begin={-i * WF_GAP} />
      ))}

      {/* 審核環:申請單穿過時閃綠 */}
      <ellipse className={styles.wfRing} {...stampRing} />
      <ellipse className={styles.wfRingFlash} {...stampRing} opacity="0">
        <animate {...pulse} begin={`${stampBegin}s`} values="1;0.1;0" keyTimes="0;0.45;1" />
      </ellipse>
      {/* 抵達節點:供應完成的擴散光 */}
      <ellipse className={styles.wfArrive} {...arrive} opacity="0">
        <animate {...pulse} begin={`${arriveBegin}s`} values="0.95;0;0" keyTimes="0;0.5;1" />
      </ellipse>
    </g>
  );
}

/* ── AI 核心 ── */

export function AiCore() {
  const { x, y, z } = AI_CORE;
  const r = 30;
  const h = 42;
  const T = [x, y, z + h];
  const B = [x, y, z - h];
  const E = [x + r, y, z]; // +x
  const S = [x, y + r, z]; // +y
  const W = [x - r, y, z];
  const N = [x, y - r, z];
  const [cx, cy] = project(x, y, z);
  const [gx, gy] = project(x, y, 0);
  const ground = isoEllipse(x, y, 46, 0);
  const ringA = isoEllipse(x, y, 52, z);
  const ringB = isoEllipse(x, y, 66, z - 6);

  return (
    <g data-b="ai">
      <ellipse className={styles.aiGround} {...ground} />
      <path className={styles.aiTether} d={`M ${fmt(cx)} ${fmt(cy + h)} L ${fmt(gx)} ${fmt(gy)}`} />
      <g className={styles.aiBob}>
        <circle className={styles.aiAura} cx={cx} cy={cy} r={48} filter="url(#lp-softGlow)" />
        <ellipse className={styles.aiRing} {...ringB} />
        <polygon className={styles.aiFaceL} points={P([T, W, S])} />
        <polygon className={styles.aiFaceR} points={P([T, N, E])} />
        <polygon className={styles.aiFaceF} points={P([T, E, S])} />
        <polygon className={styles.aiFaceB} points={P([B, E, S])} />
        <ellipse className={`${styles.aiRing} ${styles.aiRingFront}`} {...ringA} />
      </g>
    </g>
  );
}

/* ── 防火牆(network) ── */

const NET_DUR = 7;
const BLOCK_DUR = 3.2;

/** 放行的封包:沿既有進出流量線跑,畫在建築之前讓遮擋正確(掛在 FlowLinesGroup 裡) */
export function AllowedPackets({ inPath, outPath }) {
  const packets = [
    { d: inPath, begin: 0, cls: styles.pkIn },
    { d: inPath, begin: -NET_DUR / 3, cls: styles.pkIn },
    { d: inPath, begin: (-NET_DUR * 2) / 3, cls: styles.pkIn },
    { d: outPath, begin: -1, cls: styles.pkOut },
    { d: outPath, begin: -1 - NET_DUR / 2, cls: styles.pkOut },
  ];
  return (
    <g className={styles.netLayer}>
      {packets.map((p, i) => {
        const timing = { dur: `${NET_DUR}s`, begin: `${p.begin.toFixed(2)}s`, repeatCount: "indefinite" };
        return (
          <g key={i} className={p.cls} opacity="0">
            <animateMotion {...timing} path={p.d} />
            <animate {...timing} attributeName="opacity" values="0;1;1;0" keyTimes="0;0.05;0.93;1" />
            <circle className={styles.pkHalo} r="11" />
            <circle r="4.5" />
          </g>
        );
      })}
    </g>
  );
}

/** 被擋下的封包:從校外飛向光幕,撞上後漣漪＋彈回淡出 */
function BlockedPacket({ x, begin }) {
  const z = 22;
  const d = linePath([[x, 1600, z], [x, GATE.y + 3, z]]);
  const [hx, hy] = project(x, GATE.y, z);
  const timing = { dur: `${BLOCK_DUR}s`, begin: `${begin}s`, repeatCount: "indefinite" };
  return (
    <g>
      <g className={styles.pkBlocked} opacity="0">
        <animateMotion {...timing} path={d} calcMode="linear" keyPoints="0;1;0.8" keyTimes="0;0.7;1" />
        <animate {...timing} attributeName="opacity" values="0;1;1;0" keyTimes="0;0.1;0.72;1" />
        <circle className={styles.pkHalo} r="11" />
        <circle r="4.5" />
      </g>
      <circle className={styles.fwRipple} cx={hx} cy={hy} r="0" opacity="0">
        <animate {...timing} attributeName="r" values="0;0;4;26" keyTimes="0;0.69;0.7;1" />
        <animate {...timing} attributeName="opacity" values="0;0;1;0" keyTimes="0;0.69;0.7;1" />
      </circle>
    </g>
  );
}

export function FirewallGate() {
  const { x0, x1, y, h } = GATE;
  const plane = (z0, z1) => P([[x0, y, z1], [x1, y, z1], [x1, y, z0], [x0, y, z0]]);
  const [shx, shy] = project((x0 + x1) / 2, y, h + 44);
  return (
    <g data-b="gatehouse">
      {/* 缺口兩側的閘柱 */}
      <IsoBox x={x0 - 12} y={y - 15} w={12} d={30} h={h + 8} className={styles.fwPost} />
      <polygon className={styles.fwCurtain} points={plane(0, h)} />
      {[16, 32, 48].map((z) => (
        <path key={z} className={styles.fwLine} d={linePath([[x0, y, z], [x1, y, z]])} />
      ))}
      <path className={styles.fwScan} d={linePath([[x0, y, 2], [x1, y, 2]])} />
      <path className={styles.fwEdge} d={linePath([[x0, y, h], [x1, y, h]])} />
      <IsoBox x={x1} y={y - 15} w={12} d={30} h={h + 8} className={styles.fwPost} />

      {/* 盾牌標誌:平時低調,network 段亮起 */}
      <g className={styles.fwShield} transform={`translate(${fmt(shx)} ${fmt(shy)})`}>
        <path d="M 0 -20 L 16 -13 L 16 1.5 C 16 12 8.8 17.6 0 22.5 C -8.8 17.6 -16 12 -16 1.5 L -16 -13 Z" />
        <path className={styles.fwTick} d="M -6.5 0.5 L -1.8 6 L 7.5 -5" />
      </g>

      <g className={styles.netLayer}>
        <BlockedPacket x={655} begin={0} />
        <BlockedPacket x={748} begin={-BLOCK_DUR / 2} />
      </g>
    </g>
  );
}

/* ── 私有雲(hero / overview) ── */

/* 雲團:整朵共用一條由上到下的垂直漸層(交疊處不會出現接縫),
   每顆球再疊一層偏左上的柔光,體積感來自高光而不是描邊;
   底部用 clip 切平,切口疊腹部陰影＋城市反光的亮邊。
   [dx, dy, r] 相對雲心(螢幕座標) */
const PC_PUFFS = [
  [-52, -46, 66], [36, -60, 80], [118, -22, 58],
  [-140, 8, 44], [-86, -2, 56], [-4, -4, 66], [80, 4, 60], [146, 12, 42],
];
const PC_BASE = 36; // 雲底切平線(相對雲心)
const PC_PULSE = 2.4;

/** 切平線與各球的交弦聯集 → 雲底實際寬度(光纖埠與亮邊都沿這段排) */
function cloudBaseSpan() {
  let lo = Infinity;
  let hi = -Infinity;
  PC_PUFFS.forEach(([dx, dy, r]) => {
    const h = PC_BASE - dy;
    if (Math.abs(h) >= r) return;
    const half = Math.sqrt(r * r - h * h);
    lo = Math.min(lo, dx - half);
    hi = Math.max(hi, dx + half);
  });
  return [lo, hi];
}

function Puffs({ cx, cy, ...props }) {
  return PC_PUFFS.map(([dx, dy, r]) => (
    <circle key={`${dx},${dy}`} cx={fmt(cx + dx)} cy={fmt(cy + dy)} r={r} {...props} />
  ));
}

export function PrivateCloud() {
  const [cx, cy] = project(CLOUD.x, CLOUD.y, CLOUD.z);
  const baseY = cy + PC_BASE;
  const [spanLo, spanHi] = cloudBaseSpan();
  /* 目標依螢幕 x 排序後,光纖埠沿雲底等距分配,光纖才不會交叉 */
  const targets = CLOUD_LINKS.map(([x, y, z]) => ({ p: project(x, y, z), dock: isoEllipse(x, y, 14, z) }))
    .sort((a, b) => a.p[0] - b.p[0]);
  const portLo = cx + spanLo + 34;
  const portHi = cx + spanHi - 34;
  const links = targets.map((t, i) => {
    const ax = portLo + ((portHi - portLo) * i) / (targets.length - 1);
    return { ...t, ax, d: `M ${fmt(ax)} ${fmt(baseY)} L ${fmt(t.p[0])} ${fmt(t.p[1])}` };
  });

  return (
    <g className={styles.pcLayer}>
      <defs>
        <linearGradient id="lp-cloudFill" gradientUnits="userSpaceOnUse" x1="0" y1={fmt(cy - 150)} x2="0" y2={fmt(baseY)}>
          <stop offset="0" className={styles.stPuffHi} />
          <stop offset="0.55" className={styles.stPuffMid} />
          <stop offset="1" className={styles.stPuffLo} />
        </linearGradient>
        <radialGradient id="lp-puffLight" cx="0.5" cy="0.5" r="0.5">
          <stop offset="0" className={styles.stLightA} />
          <stop offset="0.55" className={styles.stLightB} />
          <stop offset="1" className={styles.stLightC} />
        </radialGradient>
        <linearGradient id="lp-belly" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className={styles.stBellyA} />
          <stop offset="1" className={styles.stBellyB} />
        </linearGradient>
        <clipPath id="lp-cloudBase">
          <rect x={fmt(cx - 260)} y={fmt(cy - 240)} width="520" height={240 + PC_BASE} />
        </clipPath>
        <clipPath id="lp-cloudShape">
          <Puffs cx={cx} cy={cy} />
        </clipPath>
        <filter id="lp-cloudHalo" x="-40%" y="-60%" width="180%" height="220%">
          <feGaussianBlur stdDeviation="18" />
        </filter>
      </defs>

      {/* 光纖:細實線＋外暈,脈衝從雲底埠往下送 */}
      {links.map((l) => <path key={`g${l.d}`} className={styles.pcFiberGlow} d={l.d} />)}
      {links.map((l) => <path key={l.d} className={styles.pcFiber} d={l.d} />)}
      {links.map((l, i) => {
        const timing = { dur: `${PC_PULSE}s`, begin: `${(-i * 0.53).toFixed(2)}s`, repeatCount: "indefinite" };
        return (
          <g key={l.d}>
            <ellipse className={styles.pcDock} {...l.dock} />
            <circle className={styles.pcPulse} r="3.5" opacity="0">
              <animateMotion {...timing} path={l.d} />
              <animate {...timing} attributeName="opacity" values="0;1;1;0" keyTimes="0;0.1;0.85;1" />
            </circle>
          </g>
        );
      })}

      {/* 外暈 → 輪廓光(描邊只露出外緣) → 球體 → 腹部陰影 → 底部亮邊 */}
      <g className={styles.pcHalo} filter="url(#lp-cloudHalo)">
        <Puffs cx={cx} cy={cy} />
      </g>
      <g clipPath="url(#lp-cloudBase)">
        <g className={styles.pcRim}>
          <Puffs cx={cx} cy={cy} />
        </g>
        <Puffs cx={cx} cy={cy} fill="url(#lp-cloudFill)" />
        <g clipPath="url(#lp-cloudShape)">
          {PC_PUFFS.map(([dx, dy, r]) => (
            <circle
              key={`l${dx},${dy}`}
              cx={fmt(cx + dx - r * 0.2)}
              cy={fmt(cy + dy - r * 0.24)}
              r={fmt(r * 0.8)}
              fill="url(#lp-puffLight)"
            />
          ))}
          <rect x={fmt(cx - 260)} y={fmt(baseY - 64)} width="520" height="64" fill="url(#lp-belly)" />
        </g>
      </g>
      <path className={styles.pcBaseEdge} d={`M ${fmt(cx + spanLo + 6)} ${fmt(baseY)} L ${fmt(cx + spanHi - 6)} ${fmt(baseY)}`} />
      {links.map((l) => <ellipse key={`p${l.d}`} className={styles.pcPort} cx={fmt(l.ax)} cy={fmt(baseY)} rx="5" ry="2.2" />)}
    </g>
  );
}

/* ── 機房剖面與生命週期(lifecycle) ── */

/** 機櫃:南面/東面的狀態燈依 face 決定畫在哪一面 */
function Rack({ x, y, w, d, h, face }) {
  const leds = [];
  for (let k = 0; k < 4; k += 1) {
    const z = h - 10 - k * 9;
    const pts = face === "south"
      ? [[x + 5, y + d, z], [x + w - 5, y + d, z], [x + w - 5, y + d, z + 3], [x + 5, y + d, z + 3]]
      : [[x + w, y + 5, z], [x + w, y + d - 5, z], [x + w, y + d - 5, z + 3], [x + w, y + 5, z + 3]];
    leds.push(<polygon key={k} className={k % 2 === 0 ? styles.nodeLedOn : styles.nodeLed} points={P(pts)} />);
  }
  return (
    <g>
      <IsoBox x={x} y={y} w={w} d={d} h={h} className={styles.nodeBox} />
      {leds}
    </g>
  );
}

/** 機房內部:地板＋靠南牆一排、靠東牆一列機櫃;平時被實心牆擋住,lifecycle 段牆轉玻璃才看得到 */
export function DataCenterInterior() {
  const { x, y, w, d } = DATACENTER;
  const racks = [];
  for (let i = 0; i < 3; i += 1) racks.push({ x: x + w - 40, y: y + 22 + i * 34, w: 30, d: 26, face: "east" });
  for (let i = 0; i < 8; i += 1) racks.push({ x: x + 18 + i * 34, y: y + d - 50, w: 26, d: 34, face: "south" });
  return (
    <g>
      <polygon className={styles.dcFloor} points={P([[x, y, 0], [x + w, y, 0], [x + w, y + d, 0], [x, y + d, 0]])} />
      {racks.map((r) => <Rack key={`${r.x}-${r.y}`} {...r} h={48} />)}
    </g>
  );
}

/** 屋頂劇場:範本模具(玻璃殼＋映像核心)→ VM 生成 → 快照分身 → 規格放大 → 到期沉入回收 */
export function LifecycleTheater() {
  const { template: T, vm: V, snapshot: S } = LIFECYCLE;
  const z0 = DATACENTER.h;
  const [tx, ty] = project(T.x + T.s / 2, T.y + T.s / 2, z0 + T.s);
  const [vx, vy] = project(V.x + V.s / 2, V.y + V.s / 2, z0 + V.s / 2);
  const [fx, fy] = project(S.x + S.s / 2, S.y + S.s / 2, z0 + S.s / 2);
  const recycle = isoEllipse(V.x + V.s / 2, V.y + V.s / 2, 24, z0);
  const pad = isoEllipse(T.x + T.s / 2, T.y + T.s / 2, 30, z0);
  const core = 14;
  return (
    <g data-b="datacenter" className={styles.lcLayer}>
      <ellipse className={styles.lcPad} {...pad} />
      <IsoBox x={T.x + (T.s - core) / 2} y={T.y + (T.s - core) / 2} w={core} d={core} h={core} z0={z0 + 10} className={styles.lcCore} />
      <IsoBox x={T.x} y={T.y} w={T.s} d={T.s} h={T.s} z0={z0} className={styles.lcTemplate} />
      <path className={styles.lcBeam} d={`M ${fmt(tx)} ${fmt(ty)} Q ${fmt((tx + vx) / 2)} ${fmt(Math.min(ty, vy) - 40)} ${fmt(vx)} ${fmt(vy)}`} />
      <ellipse className={styles.lcRecycle} {...recycle} />
      <g className={styles.lcVm}>
        <IsoBox x={V.x} y={V.y} w={V.s} d={V.s} h={V.s} z0={z0} className={styles.aiVm} />
      </g>
      <g className={styles.lcGhost}>
        <IsoBox x={S.x} y={S.y} w={S.s} d={S.s} h={S.s} z0={z0} className={styles.lcGhostBox} />
      </g>
      <circle className={styles.lcFlash} cx={fx} cy={fy} r="18" />
    </g>
  );
}

/* ── 電腦教室(classroom) ── */

export function ClassroomRoom({ seats, online }) {
  const C = CLASSROOM;
  const z = C.h;
  const total = Math.max(Math.round(Number(seats)) || 40, 1);
  const shown = Math.min(total, C.cols * C.maxRows);
  const lit = Math.round(Math.min(Math.max(Number(online) || 0, 0) / total, 1) * shown);
  const rail = 6;
  const wall = 10;

  const desks = Array.from({ length: shown }, (_, i) => {
    const x = C.x + 18 + (i % C.cols) * 35;
    const y = C.y + 44 + Math.floor(i / C.cols) * 29;
    const on = i < lit;
    return (
      <g key={i}>
        <IsoBox x={x} y={y} w={22} d={12} h={4} z0={z} className={styles.deskBox} />
        <IsoBox x={x + 5} y={y + 1} w={12} d={2} h={10} z0={z + 4} className={styles.deskBox} />
        <polygon
          className={on ? styles.seat : styles.screenOff}
          data-seat={on ? "" : undefined}
          points={P([[x + 6, y + 3, z + 5.5], [x + 16, y + 3, z + 5.5], [x + 16, y + 3, z + 12.5], [x + 6, y + 3, z + 12.5]])}
        />
      </g>
    );
  });

  return (
    <g data-b="classroom">
      <polygon
        className={styles.classFloor}
        points={P([[C.x + rail, C.y + rail, z], [C.x + C.w - rail, C.y + rail, z], [C.x + C.w - rail, C.y + C.d - rail, z], [C.x + rail, C.y + C.d - rail, z]])}
      />
      {/* 後方與西側女兒牆、黑板,先畫;桌機;最後前方與東側女兒牆 */}
      <IsoBox x={C.x} y={C.y} w={C.w} d={rail} h={wall} z0={z} className={styles.roofBox} />
      <IsoBox x={C.x} y={C.y + rail} w={rail} d={C.d - rail} h={wall} z0={z} className={styles.roofBox} />
      <IsoBox x={C.x + 90} y={C.y + 12} w={120} d={3} h={26} z0={z + 4} className={styles.deskBox} />
      <polygon
        className={styles.classBoard}
        points={P([[C.x + 94, C.y + 15, z + 8], [C.x + 206, C.y + 15, z + 8], [C.x + 206, C.y + 15, z + 27], [C.x + 94, C.y + 15, z + 27]])}
      />
      {desks}
      <IsoBox x={C.x} y={C.y + C.d - rail} w={C.w} d={rail} h={wall} z0={z} className={styles.roofBox} />
      <IsoBox x={C.x + C.w - rail} y={C.y} w={rail} d={C.d - rail} h={wall} z0={z} className={styles.roofBox} />
    </g>
  );
}

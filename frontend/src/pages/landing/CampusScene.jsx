import { memo, useMemo } from "react";
import { project, VIEWBOX, P, linePath, isoEllipse } from "./iso";
import { IsoBox, Shadow } from "./IsoShapes";
import { nodeSlots, pickWinner } from "./sceneLayout";
import {
  AiCore,
  AllowedPackets,
  ClassroomRoom,
  DataCenterInterior,
  DataCenterNodes,
  FirewallGate,
  LifecycleTheater,
  PrivateCloud,
  WorkflowPipeline,
} from "./StoryProps";
import styles from "./LandingPage.module.scss";

/* 夜景等距校園場景。
   建築量體、位置與 data 屬性是運鏡腳本的對位基準（cameraScript.js 的
   focus 對到這裡的世界座標）——調美術可以，動佈局要連 cameraScript 一起調。
   段落連動：
   - 根節點 data-active（LandingPage 設定）→ 焦點建築發光、流量光線增亮、
     terminal 段教學大樓窗戶轉暖（樣式都在 LandingPage.module.scss）
   - [data-seat] → S4 教室段的逐排點亮 scrub
   立體感三件套：足底投影（Shadow）、側面垂直漸層（defs 的 lp-g* 漸層）、
   頂面背光邊（Rim）；畫序=景深,建築依足底 (x+w+y+d) 排序。
   資料連動(stats.json):機房屋頂節點台數＝節點數、全校亮窗數＝VM 數、
   ai 段分數柱＝放置建議分數;段落道具本身在 StoryProps.jsx。 */

const BUILDINGS = [
  { key: "admin",      x: 100,  y: 110,  w: 200, d: 150, h: 140, focus: true, roof: [[30, 30, 26, 26, 14], [72, 34, 20, 20, 10]] },
  { key: "library",    x: 350,  y: 190,  w: 170, d: 170, h: 100, roof: [[58, 58, 54, 54, 8]] },
  { key: "dorm1",      x: 110,  y: 380,  w: 130, d: 100, h: 80 },
  { key: "dorm2",      x: 290,  y: 420,  w: 130, d: 100, h: 80 },
  { key: "studentCenter", x: 540, y: 430, w: 90, d: 110, h: 55 },
  { key: "teaching",   x: 860,  y: 140,  w: 200, d: 170, h: 210, focus: true, roof: [[24, 24, 18, 18, 16], [150, 122, 14, 14, 34]] },
  { key: "lecture",    x: 1110, y: 240,  w: 150, d: 120, h: 70 },
  { key: "datacenter", x: 140,  y: 860,  w: 300, d: 200, h: 70,  focus: true, slits: true },
  { key: "classroom",  x: 880,  y: 860,  w: 300, d: 190, h: 55,  focus: true },
  { key: "labAnnex",   x: 1230, y: 880,  w: 110, d: 140, h: 85 },
  { key: "storage",    x: 910,  y: 1110, w: 100, d: 70,  h: 35 },
  { key: "gatehouse",  x: 800,  y: 1250, w: 80,  d: 80,  h: 55,  focus: true, roof: [[26, 26, 24, 24, 8]] },
];

const TREES = [
  [595, 210], [595, 400], [595, 565], [795, 410], [795, 575],
  [310, 595], [415, 600], [430, 515], [855, 390], [1310, 600],
  [1150, 800], [840, 1120],
];

const LAMPS = [
  [615, 340], [615, 980], [615, 1240], [785, 420], [785, 1140],
  [240, 615], [1000, 785], [1240, 785],
];

/** 地面上的圓角矩形（跑道用）：Q 控制點經投影後仍是正確的貝茲曲線 */
function roundedRectPath(x0, y0, x1, y1, r) {
  const p = (x, y) => {
    const [sx, sy] = project(x, y, 0);
    return `${sx.toFixed(1)} ${sy.toFixed(1)}`;
  };
  return [
    `M ${p(x0 + r, y0)}`,
    `L ${p(x1 - r, y0)}`, `Q ${p(x1, y0)} ${p(x1, y0 + r)}`,
    `L ${p(x1, y1 - r)}`, `Q ${p(x1, y1)} ${p(x1 - r, y1)}`,
    `L ${p(x0 + r, y1)}`, `Q ${p(x0, y1)} ${p(x0, y1 - r)}`,
    `L ${p(x0, y0 + r)}`, `Q ${p(x0, y0)} ${p(x0 + r, y0)}`,
    "Z",
  ].join(" ");
}

/** 頂面背光邊：遠離鏡頭的兩條頂邊描亮,強化量體輪廓 */
function Rim({ x, y, w, d, h }) {
  return <path className={styles.rim} d={linePath([[x + w, y, h], [x, y, h], [x, y + d, h]])} />;
}

/* 決定性偽隨機：同一顆窗每次 render 亮暗一致 */
function hash(seed) {
  const s = Math.sin(seed * 127.1 + 311.7) * 43758.5453;
  return s - Math.floor(s);
}

/** 一般建築的窗格幾何:南、東兩個可見面 */
function windowQuads(b) {
  const quads = [];
  const win = 12;
  for (let z = 14; z <= b.h - 18; z += 24) {
    for (let u = 16; u <= b.w - 16 - win; u += 26) {
      quads.push(P([[b.x + u, b.y + b.d, z], [b.x + u + win, b.y + b.d, z], [b.x + u + win, b.y + b.d, z + win], [b.x + u, b.y + b.d, z + win]]));
    }
    for (let v = 16; v <= b.d - 16 - win; v += 26) {
      quads.push(P([[b.x + b.w, b.y + v, z], [b.x + b.w, b.y + v + win, z], [b.x + b.w, b.y + v + win, z + win], [b.x + b.w, b.y + v, z + win]]));
    }
  }
  return quads;
}

/* 全校窗格依決定性亂數排名:亮窗數由 VM 數決定,前 k 名亮、其中約四分之一是暖光 */
const WINDOWS = Object.fromEntries(
  BUILDINGS.filter((b) => !b.slits).map((b) => [b.key, windowQuads(b).map((pts, i) => ({ id: `${b.key}-${i}`, pts }))]),
);
const WINDOW_RANK = Object.values(WINDOWS)
  .flat()
  .map((w, i) => ({ id: w.id, r: hash(i + 1) }))
  .sort((a, b) => b.r - a.r)
  .map((w) => w.id);

function windowClasses(vms) {
  const total = WINDOW_RANK.length;
  const lit = Math.min(Number.isFinite(vms) ? vms : Math.round(total * 0.3), Math.round(total * 0.85));
  const warm = Math.round(lit * 0.23);
  const map = {};
  WINDOW_RANK.forEach((id, rank) => {
    map[id] = rank < warm ? styles.winWarm : rank < lit ? styles.winLit : styles.winUnlit;
  });
  return map;
}

function Windows({ b, classes }) {
  return WINDOWS[b.key].map((w) => <polygon key={w.id} className={classes[w.id]} points={w.pts} />);
}

/** 機房的橫向通風縫（取代窗格） */
function Slits({ b }) {
  const bands = [20, 42];
  return bands.map((z) => (
    <g key={z}>
      <polygon className={styles.winSlit} points={P([[b.x + 18, b.y + b.d, z], [b.x + b.w - 18, b.y + b.d, z], [b.x + b.w - 18, b.y + b.d, z + 6], [b.x + 18, b.y + b.d, z + 6]])} />
      <polygon className={styles.winSlit} points={P([[b.x + b.w, b.y + 18, z], [b.x + b.w, b.y + b.d - 18, z], [b.x + b.w, b.y + b.d - 18, z + 6], [b.x + b.w, b.y + 18, z + 6]])} />
    </g>
  ));
}

/** 焦點建築的發光描邊：預設隱形，data-active 對到時亮起 */
function GlowOutline({ b }) {
  const { x, y, w, d, h } = b;
  return (
    <g className={styles.glowOutline} data-glow={b.key} filter="url(#lp-softGlow)">
      <polygon points={P([[x, y, h], [x + w, y, h], [x + w, y + d, h], [x, y + d, h]])} />
      <polygon points={P([[x, y + d, h], [x + w, y + d, h], [x + w, y + d, 0], [x, y + d, 0]])} />
      <polygon points={P([[x + w, y, h], [x + w, y + d, h], [x + w, y + d, 0], [x + w, y, 0]])} />
    </g>
  );
}

function Building({ b, winClasses }) {
  const ping = b.focus ? isoEllipse(b.x + b.w / 2, b.y + b.d / 2, Math.max(b.w, b.d) * 0.72) : null;
  return (
    <g data-b={b.key}>
      {/* 聚焦時的地面雷達擴散圈(兩圈交錯);預設 opacity 0,段落連動時播放 */}
      {ping && (
        <>
          <ellipse className={styles.ping} cx={ping.cx} cy={ping.cy} rx={ping.rx} ry={ping.ry} />
          <ellipse className={styles.ping} style={{ animationDelay: "1.3s" }} cx={ping.cx} cy={ping.cy} rx={ping.rx} ry={ping.ry} />
        </>
      )}
      <Shadow x={b.x} y={b.y} w={b.w} d={b.d} h={b.h} />
      {/* 機房內部先畫,lifecycle 段牆面轉玻璃時透出來 */}
      {b.key === "datacenter" && <DataCenterInterior />}
      <IsoBox x={b.x} y={b.y} w={b.w} d={b.d} h={b.h} className={b.focus ? styles.boxKey : ""} />
      {b.slits ? <Slits b={b} /> : <Windows b={b} classes={winClasses} />}
      <Rim x={b.x} y={b.y} w={b.w} d={b.d} h={b.h} />
      {(b.roof ?? []).map(([dx, dy, w, d, h], i) => (
        <IsoBox key={i} x={b.x + dx} y={b.y + dy} w={w} d={d} h={h} z0={b.h} className={styles.roofBox} />
      ))}
      {b.focus && <GlowOutline b={b} />}
    </g>
  );
}

function Tree({ x, y }) {
  const sh = isoEllipse(x + 8, y + 8, 16);
  return (
    <g className={styles.tree}>
      <ellipse className={styles.treeShadow} cx={sh.cx} cy={sh.cy} rx={sh.rx} ry={sh.ry} />
      <IsoBox x={x - 3} y={y - 3} w={6} d={6} h={12} className={styles.treeTrunk} />
      <IsoBox x={x - 11} y={y - 11} w={22} d={22} h={18} z0={10} className={styles.treeCanopy} />
      <IsoBox x={x - 7} y={y - 7} w={14} d={14} h={13} z0={27} className={styles.treeCanopyTop} />
    </g>
  );
}

/** 路燈光暈（地面光池先畫,燈桿後畫在建築之上） */
function LampPool({ x, y }) {
  const e = isoEllipse(x, y, 34);
  return <ellipse className={styles.lampPool} cx={e.cx} cy={e.cy} rx={e.rx} ry={e.ry} />;
}

function Lamp({ x, y }) {
  const [hx, hy] = project(x, y, 33);
  return (
    <g>
      <IsoBox x={x - 1.5} y={y - 1.5} w={3} d={3} h={30} className={styles.lampPole} />
      <circle className={styles.lampHalo} cx={hx} cy={hy} r={5.5} />
      <circle className={styles.lampHead} cx={hx} cy={hy} r={1.9} />
    </g>
  );
}

/** 車輛：direction "v"＝沿 y 軸(縱向路)、"h"＝沿 x 軸(橫向路);畫在原點,由外層 translate 定位 */
function CarBody({ direction = "v", tone }) {
  const body = direction === "v" ? { x: -8, y: -17, w: 16, d: 34 } : { x: -17, y: -8, w: 34, d: 16 };
  const cab = direction === "v" ? { x: -6, y: -9, w: 12, d: 16 } : { x: -9, y: -6, w: 16, d: 12 };
  return (
    <g className={`${styles.car} ${tone ?? ""}`}>
      <IsoBox {...body} h={9} />
      <IsoBox {...cab} h={6} z0={9} className={styles.carCab} />
    </g>
  );
}

function ParkedCar({ x, y, direction, tone }) {
  const [sx, sy] = project(x, y, 0);
  return (
    <g transform={`translate(${sx.toFixed(1)}, ${sy.toFixed(1)})`}>
      <CarBody direction={direction} tone={tone} />
    </g>
  );
}

/** 操場：草皮＋圓角跑道＋內場;圓角用投影後的 Q 曲線,透視正確 */
function SportsField() {
  return (
    <g>
      <polygon className={styles.fieldBase} points={P([[880, 420], [1280, 420], [1280, 620], [880, 620]])} />
      <path className={styles.trackRing} d={roundedRectPath(904, 444, 1256, 596, 36)} />
      <path className={styles.trackLine} d={roundedRectPath(904, 444, 1256, 596, 36)} />
      <polygon className={styles.infield} points={P([[940, 478], [1220, 478], [1220, 562], [940, 562]])} />
    </g>
  );
}

/** 中央廣場：鋪面圓環＋噴泉 */
function Plaza() {
  const base = isoEllipse(500, 560, 55);
  const ring = isoEllipse(500, 560, 34);
  const water = isoEllipse(500, 560, 9, 13);
  return (
    <g>
      <ellipse className={styles.plazaBase} cx={base.cx} cy={base.cy} rx={base.rx} ry={base.ry} />
      <ellipse className={styles.plazaRing} cx={ring.cx} cy={ring.cy} rx={ring.rx} ry={ring.ry} />
      <IsoBox x={492} y={552} w={16} d={16} h={12} className={styles.roofBox} />
      <ellipse className={styles.fountainWater} cx={water.cx} cy={water.cy} rx={water.rx} ry={water.ry} />
    </g>
  );
}

/** 停車場：場地、車位線與兩台停放的車 */
function Parking() {
  const slots = [128, 176, 224, 272, 320];
  return (
    <g>
      <polygon className={styles.parkBase} points={P([[80, 1120], [360, 1120], [360, 1300], [80, 1300]])} />
      {slots.map((sx) => (
        <path key={sx} className={styles.parkLine} d={linePath([[sx, 1140], [sx, 1205]])} />
      ))}
      <ParkedCar x={152} y={1172} direction="v" tone={styles.carBlue} />
      <ParkedCar x={248} y={1172} direction="v" tone={styles.carPurple} />
    </g>
  );
}

function WaterTower() {
  const [bx, by] = project(545, 1145, 90);
  return (
    <g>
      <Shadow x={524} y={1124} w={42} d={42} h={86} />
      <IsoBox x={536} y={1136} w={18} d={18} h={60} className={styles.lampPole} />
      <IsoBox x={524} y={1124} w={42} d={42} h={26} z0={60} className={styles.roofBox} />
      <circle className={styles.beacon} cx={bx} cy={by} r={2.4} />
    </g>
  );
}

/* 建築依足底深度排序,painter's algorithm 才不會前後蓋錯 */
const SORTED_BUILDINGS = BUILDINGS.slice().sort(
  (a, b) => a.x + a.w + a.y + a.d - (b.x + b.w + b.y + b.d),
);

function CampusScene({ stats }) {
  const teachingBeacon = project(1017, 269, 246);
  const vms = stats?.condition?.vms;
  const winClasses = useMemo(() => windowClasses(vms), [vms]);
  const slots = useMemo(() => nodeSlots(stats?.condition?.nodes), [stats?.condition?.nodes]);
  const candidates = stats?.ai?.candidates;
  const winner = pickWinner(candidates, slots.length);
  return (
    <svg
      className={styles.sceneSvg}
      viewBox={`${VIEWBOX.x} ${VIEWBOX.y} ${VIEWBOX.w} ${VIEWBOX.h}`}
      role="img"
      aria-hidden="true"
    >
      <defs>
        <filter id="lp-softGlow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="7" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        {/* 側面垂直漸層:上亮下暗,便宜的體積感。
            stop 顏色走 CSS class(LandingPage.module.scss),日間模式才能換色 */}
        <linearGradient id="lp-gs" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className={styles.stGsHi} />
          <stop offset="1" className={styles.stGsLo} />
        </linearGradient>
        <linearGradient id="lp-ge" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className={styles.stGeHi} />
          <stop offset="1" className={styles.stGeLo} />
        </linearGradient>
        <linearGradient id="lp-gsk" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className={styles.stGskHi} />
          <stop offset="1" className={styles.stGskLo} />
        </linearGradient>
        <linearGradient id="lp-gek" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" className={styles.stGekHi} />
          <stop offset="1" className={styles.stGekLo} />
        </linearGradient>
      </defs>

      {/* 地面、街區鋪面、步道與道路(scenery=聚焦時整組調暗的背景景物) */}
      <g className={styles.scenery}>
      <polygon className={styles.ground} points={P([[0, 0], [1400, 0], [1400, 1400], [0, 1400]])} />
      <g>
        {[100, 200, 300, 400, 500, 600, 700, 800, 900, 1000, 1100, 1200, 1300].map((k) => (
          <g key={k}>
            <path className={styles.gridLine} d={linePath([[k, 0], [k, 1400]])} />
            <path className={styles.gridLine} d={linePath([[0, k], [1400, k]])} />
          </g>
        ))}
      </g>
      <polygon className={styles.pad} points={P([[70, 90], [560, 90], [560, 560], [70, 560]])} />
      <polygon className={styles.pad} points={P([[820, 110], [1310, 110], [1310, 390], [820, 390]])} />
      <polygon className={styles.pad} points={P([[110, 830], [560, 830], [560, 1090], [110, 1090]])} />
      <polygon className={styles.pad} points={P([[840, 830], [1360, 830], [1360, 1070], [840, 1070]])} />
      <polygon className={styles.walkway} points={P([[760, 218], [860, 218], [860, 232], [760, 232]])} />
      <polygon className={styles.walkway} points={P([[520, 293], [640, 293], [640, 307], [520, 307]])} />
      <polygon className={styles.walkway} points={P([[440, 943], [640, 943], [640, 957], [440, 957]])} />
      <polygon className={styles.walkway} points={P([[760, 948], [880, 948], [880, 962], [760, 962]])} />
      <polygon className={styles.road} points={P([[640, 0], [760, 0], [760, 1400], [640, 1400]])} />
      <polygon className={styles.road} points={P([[0, 640], [1400, 640], [1400, 760], [0, 760]])} />
      <path className={styles.laneLine} d={linePath([[700, 0], [700, 1400]])} />
      <path className={styles.laneLine} d={linePath([[0, 700], [1400, 700]])} />

      <SportsField />
      <Plaza />
      <Parking />
      {LAMPS.map(([x, y]) => <LampPool key={`p${x}-${y}`} x={x} y={y} />)}
      </g>

      <FlowLinesGroup />

      {/* 行駛中的車(畫在建築前,被建築正確遮擋;位移動畫在 SCSS) */}
      <g className={styles.scenery}>
        <g className={`${styles.movingCar} ${styles.carDriveSouth}`}><CarBody direction="v" tone={styles.carTeal} /></g>
        <g className={`${styles.movingCar} ${styles.carDriveNorth}`}><CarBody direction="v" tone={styles.carBlue} /></g>
        <g className={`${styles.movingCar} ${styles.carDriveEast}`}><CarBody direction="h" tone={styles.carPurple} /></g>
      </g>

      {/* 建築群(依景深排序) */}
      {SORTED_BUILDINGS.map((b) => <Building key={b.key} b={b} winClasses={winClasses} />)}

      {/* 機房散熱模組屬於機房的聚焦組,跟主體一起亮 */}
      <g data-b="datacenter">
        <Shadow x={470} y={880} w={50} d={190} h={35} />
        <IsoBox x={470} y={880} w={50} d={50} h={35} className={styles.roofBox} />
        <IsoBox x={470} y={950} w={50} d={50} h={35} className={styles.roofBox} />
        <IsoBox x={470} y={1020} w={50} d={50} h={35} className={styles.roofBox} />
      </g>
      <DataCenterNodes slots={slots} candidates={candidates} winner={winner} />
      <LifecycleTheater />

      <ClassroomRoom seats={stats?.classroom?.seats} online={stats?.classroom?.online} />

      <g className={styles.scenery}>
        {/* 空橋:教學大樓↔階梯教室、行政樓↔圖書館 */}
        <IsoBox x={1060} y={262} w={50} d={36} h={14} z0={42} className={styles.roofBox} />
        <IsoBox x={300} y={206} w={50} d={34} h={12} z0={52} className={styles.roofBox} />
        <WaterTower />
        {TREES.map(([x, y]) => <Tree key={`${x}-${y}`} x={x} y={y} />)}
        {LAMPS.map(([x, y]) => <Lamp key={`l${x}-${y}`} x={x} y={y} />)}
        {/* 校門與圍牆（南緣,道路穿過缺口） */}
        <IsoBox x={40} y={1350} w={570} d={30} h={26} className={styles.wall} />
        <IsoBox x={790} y={1350} w={570} d={30} h={26} className={styles.wall} />
      </g>

      {/* 段落道具:防火牆閘門、申請管線、AI 核心(畫在最上層,都高過或位在沿線建築之前) */}
      <FirewallGate />
      <WorkflowPipeline target={slots[winner]} />
      <AiCore />
      <PrivateCloud />

      {/* 教學大樓天線的航空障礙燈 */}
      <circle className={styles.beacon} cx={teachingBeacon[0]} cy={teachingBeacon[1]} r={2.4} />
    </svg>
  );
}

/** 校門進出流量：沿道路的虛線光流，畫在建築之前讓它被建築正確遮擋 */
const FLOW_IN = linePath([[685, 1560], [685, 715], [290, 715], [290, 860]]);
const FLOW_OUT = linePath([[1030, 860], [1030, 685], [715, 685], [715, 1560]]);

function FlowLinesGroup() {
  return (
    <g className={styles.flowLines}>
      <path className={styles.flowIn} d={FLOW_IN} />
      <path className={styles.flowOut} d={FLOW_OUT} />
      <AllowedPackets inPath={FLOW_IN} outPath={FLOW_OUT} />
    </g>
  );
}

export default memo(CampusScene);

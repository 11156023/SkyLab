/**
 * 把 octoBrain 的 pose 與粒子畫到 canvas：每一格是 u×u 的實心方塊，邊緣取整數避免縫隙。
 */
import {
  BOOK, BOOK_COLORS, BOOK_HANDS, BUBBLE, COLORS, EYE_AT, EYES, FLIP, GLYPH, HEAD,
  LAPTOP_BASE_COLOR, LAPTOP_BASE_W, LAPTOP_LID, LEGS, MOUTHS, TYPE_HANDS,
} from "./octoSprites";
import { LAYOUT } from "./octoBrain";

function makePainter(ctx, u) {
  return (x, y, color) => {
    const x0 = Math.round(x * u);
    const y0 = Math.round(y * u);
    ctx.fillStyle = color;
    ctx.fillRect(x0, y0, Math.round((x + 1) * u) - x0, Math.round((y + 1) * u) - y0);
  };
}

function drawRows(paint, rows, x0, y0, map) {
  for (let j = 0; j < rows.length; j++) {
    const row = rows[j];
    for (let i = 0; i < row.length; i++) {
      const ch = row[i];
      if (ch !== ".") paint(x0 + i, y0 + j, map[ch]);
    }
  }
}

const glyph = (paint, name, x, y, color) => drawRows(paint, GLYPH[name], Math.round(x), Math.round(y), { X: color });

function drawProp(paint, p, ox, by, theme) {
  if (p.prop.kind === "book") {
    // 書從觸手後面升上來：超出觸手底部的那幾排先不畫
    BOOK.forEach((row, j) => {
      const ly = 9 + p.prop.lift + j;
      if (ly > 13) return;
      for (let i = 0; i < row.length; i++) if (row[i] !== ".") paint(ox + 4 + i, by + ly, BOOK_COLORS[row[i]]);
    });
    if (p.prop.lift === 0) for (const [hx, hy, ch] of BOOK_HANDS) paint(ox + hx, by + hy, COLORS[ch]);
    if (p.prop.flip >= 0) for (const [px, py] of FLIP[p.prop.flip]) paint(ox + px, by + py, BOOK_COLORS.W);
    return;
  }
  const lap = { A: theme.lid, O: BOOK_COLORS.B };
  LAPTOP_LID.forEach((row, j) => {
    const ly = 9 + p.prop.lift + j;
    if (ly > 14) return;
    for (let i = 0; i < row.length; i++) if (row[i] !== ".") paint(ox + 4 + i, by + ly, lap[row[i]]);
  });
  if (p.prop.lift === 0) {
    for (let i = 0; i < LAPTOP_BASE_W; i++) paint(ox + 3 + i, by + 14, LAPTOP_BASE_COLOR);
    // hand：0 左手舉起、1 右手舉起、-1 兩手都放下
    const left = p.prop.hand === 0 ? TYPE_HANDS.up : TYPE_HANDS.down;
    const right = p.prop.hand === 1 ? TYPE_HANDS.up : TYPE_HANDS.down;
    for (const [hx, hy, ch] of left) paint(ox + hx, by + hy, COLORS[ch]);
    for (const [hx, hy, ch] of right) paint(ox + 19 - hx, by + hy, COLORS[ch]);
  }
}

function drawPet(paint, p, ox, oy, theme) {
  const map = { M: COLORS.M, L: COLORS.L, D: COLORS.D, c: theme.cap };
  const by = oy + p.jy;
  drawRows(paint, LEGS[p.legs], ox, by + 9, map);
  const hy = by + p.headDy;
  drawRows(paint, HEAD, ox, hy, map);
  // 東張西望時眼睛和嘴巴一起左右移，像整張臉轉過去
  EYE_AT.forEach(([ax, ay], i) => {
    for (const [dx, dy] of EYES[p.eyes][i]) paint(ox + ax + dx + p.look, hy + ay + dy + p.eyeDy, COLORS.k);
  });
  for (const [mx, my] of MOUTHS[p.mouth]) paint(ox + mx + p.look, hy + my, COLORS.k);
  if (p.prop) drawProp(paint, p, ox, by, theme);
  // 帽穗：從帽沿右端拉一條四格長的線（Bresenham，甩起來也不會斷開）
  const tx = Math.round(Math.sin(p.tassel) * 4);
  const ty = Math.round(Math.cos(p.tassel) * 4);
  const sx = Math.sign(tx);
  const sy = Math.sign(ty);
  const ax = Math.abs(tx);
  const ay = Math.abs(ty);
  let x = 0;
  let y = 0;
  let err = ax - ay;
  while (x !== tx || y !== ty) {
    const e2 = 2 * err;
    if (e2 > -ay) { err -= ay; x += sx; }
    if (e2 < ax) { err += ax; y += sy; }
    paint(ox + 15 + x, hy + 1 + y, COLORS.y);
  }
}

// 粒子快消失時改成一閃一閃
const visible = (p) => p.max - p.life > 0.45 || Math.floor(p.life * 10) % 2 === 0;

export function drawScene(ctx, { u, pose, parts, theme }) {
  const { OX, OY } = LAYOUT;
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  const paint = makePainter(ctx, u);
  // 腳下的影子，跳得越高越窄
  const sw = Math.max(6, 16 - Math.round(-pose.jy * 1.4));
  const sx = Math.round(OX + 10 - sw / 2);
  const fy = Math.round((OY + 15) * u);
  ctx.fillStyle = theme.shadow;
  ctx.fillRect(Math.round(sx * u), fy, Math.round((sx + sw) * u) - Math.round(sx * u), Math.round((OY + 16) * u) - fy);
  for (const p of parts) {
    if (p.kind !== "ink" || !visible(p)) continue;
    paint(Math.round(p.x), Math.round(p.y), theme.ink);
    if (p.big) paint(Math.round(p.x) + 1, Math.round(p.y), theme.ink);
  }
  drawPet(paint, pose, OX + pose.shake, OY, theme);
  for (const p of parts) {
    if (p.kind === "ink" || !visible(p)) continue;
    if (p.kind === "heart") glyph(paint, "heart", p.x, p.y, theme.heart);
    else if (p.kind === "z") glyph(paint, p.life > 1.1 ? "Z" : "z", p.x, p.y, theme.fx);
    else if (p.kind === "code") glyph(paint, p.g, p.x, p.y, theme.fx);
  }
  if (pose.bang) glyph(paint, "bang", OX + 18 + pose.shake, OY + pose.jy + pose.headDy - 2, theme.fx);
  if (pose.bubble) {
    // 泡泡跟著身體一起跳，尾巴尖端落在帽沿右上方
    const map = { o: theme.bubbleLine, w: theme.bubbleFill, d: theme.bubbleDot };
    drawRows(paint, BUBBLE, OX + 14, OY + pose.jy - 7 - pose.bubbleDy, map);
  }
}

/* 每格的「長相」：pose 和粒子取整後一樣就不必重畫，省下大部分的繪製 */
export function sceneKey(pose, parts) {
  let key = `${pose.legs}|${pose.eyes}|${pose.mouth}|${pose.eyeDy}|${pose.look}|${pose.headDy}|${pose.jy}|${pose.shake}|${pose.bang}|${pose.bubble}${pose.bubbleDy}`
    + `|${Math.round(Math.sin(pose.tassel) * 4)},${Math.round(Math.cos(pose.tassel) * 4)}`;
  if (pose.prop) key += `|${pose.prop.kind}${pose.prop.lift}${pose.prop.flip}${pose.prop.hand}`;
  for (const p of parts) key += `|${p.kind}${Math.round(p.x)},${Math.round(p.y)}${visible(p) ? 1 : 0}`;
  return key;
}

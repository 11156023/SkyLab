/**
 * 章魚學士的像素資料：20 格寬，觸手捲起來 15 格高、伸開 17 格高。
 * 字元對照：M 主色、L 高光、D 陰影、c 帽子、y 帽穗；
 * 書 B 封面、S 書背、W 書頁；筆電 A 螢幕背面、O 標誌。
 * 顏色固定用主題藍（不跟使用者換的主色）；深色模式只調帽子、筆電與特效，見 OctoPet.module.scss。
 */
export const COLORS = { M: "#5471bf", L: "#84a0e4", D: "#314994", k: "#202748", y: "#f7c442" };
export const BOOK_COLORS = { B: "#f7c442", S: "#c98f1c", W: "#faf8f0" };
export const LAPTOP_BASE_COLOR = "#b0bace";

export const SPRITE_W = 20;

export const HEAD = [
  "....................",
  "....cccccccccccc....",
  ".......cccccc.......",
  "......MMMMMMMM......",
  ".....MLLMMMMMMM.....",
  "....MLMMMMMMMMMM....",
  "....MMMMMMMMMMMM....",
  "....MMMMMMMMMMMM....",
  "....MMMMMMMMMMMM....",
];

export const LEGS = {
  // 待機：觸手捲成一團，淺色當高光、深色壓在腳底
  curl: [
    "...MMMMMMMMMMMMMM...",
    ".MLLMMMLLMMLLMMMLLM.",
    ".MMMLMLMMMMMMLMLMMM.",
    ".MDMDMDMDMMDMDMDMDM.",
    ".MDDMMMDDMMDDMMMDDM.",
    "..DDD.DDDDDDDD.DDD..",
  ],
  // 跳起來、噴墨：五隻腳之間填滿、用深色摺痕分開，高光只點在外緣、留斷點
  stretch: [
    "...MMMMMMMMMMMMMM...",
    "...MMDMMMMMMMMDMM...",
    "...LMDMMDMMDMMDML...",
    "...LMMMMDMMDMMMML...",
    "...MMDMMDMMDMMDMM...",
    "...LMDMMMMMMMMDML...",
    "...MM.MM.MM.MM.MM...",
    "....D.DD.DD.DD.D....",
  ],
};
// 走路：左右兩半輪流抬起最底下那排（配合頭一上一下），不在中間開縫
const liftFoot = (rows, from, to) =>
  rows.map((row, j) => (j === rows.length - 1 ? row.slice(0, from) + ".".repeat(to - from) + row.slice(to) : row));
LEGS.walkA = liftFoot(LEGS.curl, 0, 10);
LEGS.walkB = liftFoot(LEGS.curl, 10, 20);

// 伸腳比捲起來長兩格，站在地上時要把身體撐高，腳才不會插進地板
export const STRETCH_LIFT = LEGS.stretch.length - LEGS.curl.length;

// 眼睛 1×3 直條；嘴巴平常一字兩格，開心往下彎、生氣或受驚往上拱，都是三格（偏左半格）
export const EYE_AT = [[7, 5], [12, 5]];
export const EYES = {
  open: [[[0, 0], [0, 1], [0, 2]], [[0, 0], [0, 1], [0, 2]]],
  closed: [[[-1, 1], [0, 1], [1, 1]], [[-1, 1], [0, 1], [1, 1]]],
  happy: [[[-1, 1], [0, 0], [1, 1]], [[-1, 1], [0, 0], [1, 1]]],
};
export const MOUTHS = {
  flat: [[9, 8], [10, 8]],
  smile: [[8, 8], [9, 9], [10, 8]],
  frown: [[8, 9], [9, 8], [10, 9]],
  none: [],
};

// 看書：從外面看到金色封面和書背，書頁側邊露在上緣
export const BOOK = [
  ".WWWW..WWWW.",
  "BBBBBSSBBBBB",
  "BBBBBSSBBBBB",
  "BSSSBSSBSSSB",
  "BBBBBSSBBBBB",
];
export const BOOK_HANDS = [[3, 10, "M"], [3, 11, "L"], [4, 11, "M"], [16, 10, "M"], [16, 11, "L"], [15, 11, "M"]];
// 翻頁：一角白紙從右頁翹起、立在中間、翻到左頁（避開眼睛那兩欄）
export const FLIP = [
  [[13, 8], [14, 8]],
  [[11, 7], [11, 8]],
  [[9, 7], [10, 7], [9, 8], [10, 8]],
  [[8, 7], [8, 8]],
  [[5, 8], [6, 8]],
];

// 寫程式：筆電螢幕背面（方正的深色長方形＋金色標誌）和鍵盤座，兩側觸手輪流敲鍵盤
export const LAPTOP_LID = [
  "AAAAAAAAAAAA",
  "AAAAAAAAAAAA",
  "AAAAAOOAAAAA",
  "AAAAAAAAAAAA",
  "AAAAAAAAAAAA",
];
export const LAPTOP_BASE_W = 14;
export const TYPE_HANDS = {
  up: [[2, 12, "M"], [3, 12, "L"], [3, 13, "M"]],
  down: [[2, 13, "M"], [3, 13, "L"], [3, 14, "M"]],
};

// 有新回覆還沒看：頭上冒「…」對話泡泡，尾巴朝左下指向章魚。o 外框、w 底、d 點點
export const BUBBLE = [
  ".ooooooo.",
  "owwwwwwwo",
  "owdwdwdwo",
  "owwwwwwwo",
  ".owwoooo.",
  ".owo.....",
  "oo.......",
];

export const GLYPH = {
  heart: [".X.X.", "XXXXX", ".XXX.", "..X.."],
  z: ["XXXX", "..X.", ".X..", "XXXX"],
  Z: ["XXXXX", "...X.", "..X..", ".X...", "XXXXX"],
  bang: ["X", "X", "X", ".", "X"],
  0: ["XXX", "X.X", "XXX"],
  1: ["XX.", ".X.", "XXX"],
};

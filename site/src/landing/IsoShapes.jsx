import { P } from "./iso";
import styles from "./LandingPage.module.scss";

/** 等距方塊：頂面＋南面（y+d 側）＋東面（x+w 側），z0 是底部抬升 */
export function IsoBox({ x, y, w, d, h, z0 = 0, className }) {
  const t = z0 + h;
  return (
    <g className={`${styles.isoBox} ${className ?? ""}`}>
      <polygon className={styles.faceSouth} points={P([[x, y + d, t], [x + w, y + d, t], [x + w, y + d, z0], [x, y + d, z0]])} />
      <polygon className={styles.faceEast} points={P([[x + w, y, t], [x + w, y + d, t], [x + w, y + d, z0], [x + w, y, z0]])} />
      <polygon className={styles.faceTop} points={P([[x, y, t], [x + w, y, t], [x + w, y + d, t], [x, y + d, t]])} />
    </g>
  );
}

/** 足底投影：footprint 與其沿 (+x,+y) 位移的凸包（螢幕上正下方,月光感） */
export function Shadow({ x, y, w, d, h }) {
  const k = Math.min(Math.max(h * 0.5, 20), 80);
  return (
    <polygon
      className={styles.shadow}
      points={P([[x, y], [x + w, y], [x + w + k, y + k], [x + w + k, y + d + k], [x + k, y + d + k], [x, y + d]])}
    />
  );
}

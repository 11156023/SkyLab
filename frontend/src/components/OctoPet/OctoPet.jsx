import { useEffect, useRef } from "react";
import { LAYOUT } from "./octoBrain";
import { mountOctoPet } from "./octoController";
import { SPRITE_W } from "./octoSprites";
import styles from "./OctoPet.module.scss";

/**
 * 章魚學士：戴學士帽的像素小寵物（專題吉祥物）。
 *
 * 本身只是裝飾（aria-hidden），要能點就包在 <button> 裡、由外層負責 aria-label 與點擊；
 * 章魚只回應 hover（開心）與在身上來回滑（摸頭冒愛心），不會搶外層的點擊。
 *
 * @param {"idle"|"thinking"|"done"|"error"} activity
 *   idle 沒事做（偶爾走走、沒人理會拿書出來看、整頁沒動靜會睡覺）／thinking 寫程式／
 *   done ^_^ 跳一下後冒「…」對話泡泡／error 噴墨後冒泡泡
 * @param {number} scale 每一格幾 px，用整數像素才不會糊；預設 3（60×48）
 * @param {number} walkRange 待機時最多往左走幾格（0 不走）；整個元素一起移動，點擊與 hover 跟著走
 * @param {boolean} quiet 安靜模式：只呼吸、眨眼、晃帽穗、看游標，不會自己看書、走路、睡覺（放在訊息旁用）
 */
export default function OctoPet({ activity = "idle", scale = 3, walkRange = 0, quiet = false, className = "" }) {
  const rootRef = useRef(null);
  const canvasRef = useRef(null);
  const controllerRef = useRef(null);
  const activityRef = useRef(activity);
  activityRef.current = activity;

  useEffect(() => {
    const controller = mountOctoPet(canvasRef.current, rootRef.current, { scale, walkRange, quiet, activity: activityRef.current });
    controllerRef.current = controller;
    return () => {
      controller.destroy();
      controllerRef.current = null;
    };
  }, [scale, walkRange, quiet]);

  useEffect(() => {
    controllerRef.current?.setActivity(activity);
  }, [activity]);

  const { CW, CH, OX, OY } = LAYOUT;
  return (
    <span
      ref={rootRef}
      className={`${styles.root} ${className}`}
      // 外框只框章魚本體（20×16 格）；Zzz、星星、飄出來的 0/1 畫在框外，不擋點擊
      style={{ width: SPRITE_W * scale, height: 16 * scale }}
      aria-hidden="true"
    >
      <canvas
        ref={canvasRef}
        className={styles.canvas}
        style={{ left: -OX * scale, top: -OY * scale, width: CW * scale, height: CH * scale }}
      />
    </span>
  );
}

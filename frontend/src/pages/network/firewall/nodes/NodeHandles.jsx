/**
 * NodeHandles
 * 節點四面各一組連接點（source 與 target 疊在同一位置）。
 * 線走哪一側由 buildFlow 的 pickHandles 依相對位置決定，
 * 使用者也可以從任一側拉線建立連線。
 * 平常隱形，滑到節點上或正在拉線時才浮現，避免畫布佈滿小圓點。
 * data-firewall-handle 與 data-guide 供 UserGuide 的拖曳示範定位：
 * dragStartSide／dragEndSide 指定哪一側的 source／target 當示範起點與終點。
 */

import { Fragment } from "react";
import { Handle, Position } from "@xyflow/react";
import { HANDLE } from "../utils/buildFlow";
import styles from "../FirewallPage.module.scss";

const SIDES = [
  { position: Position.Top,    source: HANDLE.SOURCE.top,    target: HANDLE.TARGET.top },
  { position: Position.Right,  source: HANDLE.SOURCE.right,  target: HANDLE.TARGET.right },
  { position: Position.Bottom, source: HANDLE.SOURCE.bottom, target: HANDLE.TARGET.bottom },
  { position: Position.Left,   source: HANDLE.SOURCE.left,   target: HANDLE.TARGET.left },
];

export default function NodeHandles({ dragStartSide, dragEndSide }) {
  /* 用 Fragment 而非 div：節點本身是 flex container，多一層元素會變成 flex item */
  return SIDES.map((side) => (
    <Fragment key={side.position}>
      <Handle
        type="target"
        id={side.target}
        position={side.position}
        className={styles.nodeHandle}
        data-firewall-handle="target"
        data-guide={side.position === dragEndSide ? "firewall-drag-end" : undefined}
      />
      <Handle
        type="source"
        id={side.source}
        position={side.position}
        className={styles.nodeHandle}
        data-firewall-handle="source"
        data-guide={side.position === dragStartSide ? "firewall-drag-start" : undefined}
      />
    </Fragment>
  ));
}

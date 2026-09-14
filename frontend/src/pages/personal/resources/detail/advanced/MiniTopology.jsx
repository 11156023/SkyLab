/**
 * MiniTopology
 * 以單台 VM 為中心的唯讀迷你拓撲：左邊是有連線的其他 VM、中間是這台、右邊是 Internet。
 * 節點／邊元件直接沿用防火牆拓撲頁的，只是關掉拖拉與連線。
 */

import { useEffect, useMemo, useRef } from "react";
import { Background, BackgroundVariant, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import styles from "../ResourceDetailPage.module.scss";
import VMNode from "../../../../network/firewall/nodes/VMNode";
import GatewayNode from "../../../../network/firewall/nodes/GatewayNode";
import ConnectionEdge from "../../../../network/firewall/edges/ConnectionEdge";
import { buildFlow } from "../../../../network/firewall/utils/buildFlow";

const NODE_TYPES = { gateway: GatewayNode, vm: VMNode };
const EDGE_TYPES = { connection: ConnectionEdge };

export default function MiniTopology({ topology }) {
  const rfInstance = useRef(null);
  const { nodes, edges } = useMemo(
    () => buildFlow(topology ?? { nodes: [], edges: [] }, undefined, true),
    [topology],
  );

  useEffect(() => {
    window.requestAnimationFrame(() =>
      rfInstance.current?.fitView({ padding: 0.25, duration: 200 }),
    );
  }, [nodes, edges]);

  return (
    <div className={styles.flowMini}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onInit={(instance) => { rfInstance.current = instance; }}
        fitView
        fitViewOptions={{ padding: 0.25 }}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={false}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
        panOnDrag
        minZoom={0.4}
        maxZoom={1.4}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
      </ReactFlow>
    </div>
  );
}

/**
 * MiniTopology
 * 以單台 VM 為中心的迷你拓撲：左邊是有連線的其他 VM、中間是這台、右邊是 Internet。
 * 節點／邊元件直接沿用防火牆拓撲頁的，操作也跟那一頁同一套：
 * - 機器可以拖（只影響這張圖的排版，不存檔；拓撲重抓後沿用拖過的位置）
 * - 從機器邊緣拉線到另一個節點 → onConnectRequest，由上層開連線表單（要有管理權）
 * - 點連線 → onSelectEdge，由上層開連線內容面板（看開放了什麼、刪除）
 *
 * 權限：canManage＝這台機器能不能改；另一端的機器看節點自己的 can_manage（別人的機器、
 * 快速練習機是 false）。任一端不能改就不開表單，改呼叫 onBlocked 讓上層說明原因。
 *
 * children 疊在畫布上（連線內容面板）。
 */

import { useContext, useEffect, useMemo, useRef, useState } from "react";
import { Background, BackgroundVariant, ReactFlow, useNodesState } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import styles from "../ResourceDetailPage.module.scss";
import fwStyles from "../../../../network/firewall/FirewallPage.module.scss";
import VMNode from "../../../../network/firewall/nodes/VMNode";
import GatewayNode from "../../../../network/firewall/nodes/GatewayNode";
import ConnectionEdge from "../../../../network/firewall/edges/ConnectionEdge";
import { GATEWAY_KEY, buildFlow, routeEdges } from "../../../../network/firewall/utils/buildFlow";
import { INTERNET_KEY } from "../../../../../components/ConnectionDialog/intents";
import { canManageNode } from "../../../../../components/ConnectionDialog/topologyNodes";
import { ThemeContext } from "../../../../../contexts/ThemeContext";

const NODE_TYPES = { gateway: GatewayNode, vm: VMNode };
const EDGE_TYPES = { connection: ConnectionEdge };
const EMPTY_TOPOLOGY = Object.freeze({ nodes: [], edges: [] });
const FIT_OPTIONS = { padding: 0.25 };

const toDialogKey = (nodeId) => (nodeId === GATEWAY_KEY ? INTERNET_KEY : String(nodeId));

export default function MiniTopology({
  topology,
  vmid,
  canManage = false,
  selectedEdgeId = null,
  onSelectEdge,
  onConnectRequest,
  onBlocked,
  children,
}) {
  /* 畫布配色跟防火牆頁一樣跟著主題；沒有 provider（單獨渲染這張卡）就當淺色 */
  const theme = useContext(ThemeContext)?.theme ?? "light";
  const rfInstance = useRef(null);
  const [connecting, setConnecting] = useState(false);
  const centerId = String(vmid);
  /* 拖過的位置：拓撲重抓（建了線、刪了線）後沿用，機器不會跳回預設排版 */
  const draggedRef = useRef(new Map());
  /* 點連線的回呼走 ref：上層每次 render 給的函式都不同，不該讓整張圖因此重建 */
  const selectRef = useRef(onSelectEdge);
  selectRef.current = onSelectEdge;

  const flow = useMemo(
    () => buildFlow(topology ?? EMPTY_TOPOLOGY, {
      showLabel: true,
      onSelectEdge: (edge, id) => selectRef.current?.(edge, id),
    }),
    [topology],
  );
  const [nodes, setNodes, onNodesChange] = useNodesState([]);

  /* 節點的組成（多了或少了一台有連線的機器）變了才重新縮放到全貌；只是拖動或線變了不動視角 */
  const nodeSetKey = flow.nodes.map((node) => node.id).join("|");
  const fittedKeyRef = useRef(null);
  useEffect(() => {
    setNodes(flow.nodes.map((node) => {
      const dragged = draggedRef.current.get(node.id);
      /* 沒有這台的管理權：整張圖只能看，連接點不出現（下方的提示會講原因） */
      const data = canManage ? node.data : { ...node.data, connectable: false };
      return { ...node, data, position: dragged ?? node.position };
    }));
  }, [flow.nodes, canManage, setNodes]);
  useEffect(() => {
    if (!nodes.length || fittedKeyRef.current === nodeSetKey) return;
    /* 等節點量好尺寸才縮放得準 */
    if (!nodes.every((node) => node.measured?.width && node.measured?.height)) return;
    fittedKeyRef.current = nodeSetKey;
    rfInstance.current?.fitView({ ...FIT_OPTIONS, duration: 200 });
  }, [nodes, nodeSetKey]);

  /* 選取狀態就地套到邊上，再依目前節點位置決定每條線走哪一側（拖動時線即時改走最短路徑） */
  const edges = useMemo(
    () => routeEdges(
      flow.edges.map((edge) => (
        (edge.data.selected ?? false) === (edge.id === selectedEdgeId)
          ? edge
          : { ...edge, data: { ...edge.data, selected: edge.id === selectedEdgeId } }
      )),
      nodes,
    ),
    [flow.edges, nodes, selectedEdgeId],
  );

  const nodeById = (id) => (topology?.nodes ?? []).find((node) => (
    id === GATEWAY_KEY ? node.node_type === "gateway" : String(node.vmid) === id
  ));

  /* 這張圖以這台機器為中心：線的其中一端一定要是它（不在這裡牽別台機器之間的線） */
  const isValidConnection = (conn) => Boolean(
    conn?.source && conn?.target && conn.source !== conn.target
    && (conn.source === centerId || conn.target === centerId),
  );

  function handleConnect(conn) {
    if (!isValidConnection(conn)) return;
    /* 連線會同時寫兩端的規則：這台或另一端不能改就擋在這裡，不讓人填完表單才被拒絕 */
    const blocked = [conn.source, conn.target]
      .map(nodeById)
      .find((node) => node && node.node_type !== "gateway" && !canManageNode(node));
    if (!canManage || blocked) {
      onBlocked?.(blocked ?? nodeById(centerId));
      return;
    }
    onConnectRequest?.({ source: toDialogKey(conn.source), target: toDialogKey(conn.target) });
  }

  return (
    <div className={`${styles.flowMini} ${connecting ? fwStyles.connecting : ""}`}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onNodeDragStop={(_, __, dragged) => {
          for (const node of dragged ?? []) draggedRef.current.set(node.id, { ...node.position });
        }}
        onConnect={handleConnect}
        onConnectStart={() => setConnecting(true)}
        onConnectEnd={() => setConnecting(false)}
        isValidConnection={isValidConnection}
        connectionRadius={36}
        onPaneClick={() => selectRef.current?.(null, null)}
        nodeTypes={NODE_TYPES}
        edgeTypes={EDGE_TYPES}
        onInit={(instance) => { rfInstance.current = instance; }}
        fitView
        fitViewOptions={FIT_OPTIONS}
        nodesDraggable
        nodesConnectable={canManage}
        elementsSelectable
        deleteKeyCode={null}
        zoomOnScroll={false}
        zoomOnDoubleClick={false}
        panOnDrag
        minZoom={0.4}
        maxZoom={1.4}
        colorMode={theme}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={18} size={1} />
      </ReactFlow>
      {children}
    </div>
  );
}

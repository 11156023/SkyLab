/**
 * FirewallPage
 * 防火牆拓撲頁面，使用 @xyflow/react 繪製互動式節點圖。
 */

import { useState, useEffect, useRef, useCallback } from "react";
import {
  ReactFlow,
  useNodesState,
  useEdgesState,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  getTopology,
  createConnection,
  deleteConnection,
  saveLayout,
} from "../../../services/firewall";
import RulesPanel       from "../../../components/RulesPanel/RulesPanel";
import ConnectionDialog from "../../../components/ConnectionDialog/ConnectionDialog";
import GatewayNode      from "./nodes/GatewayNode";
import VMNode           from "./nodes/VMNode";
import ConnectionEdge   from "./edges/ConnectionEdge";
import { buildFlow, portLabel } from "./utils/buildFlow";
import { useTheme } from "../../../contexts/ThemeContext";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import styles from "./FirewallPage.module.scss";
import MIcon from "../../../components/MIcon";

/* ─── 常數 ──────────────────────────────────────────────── */
const GATEWAY_KEY   = "gateway";
const SAVE_DEBOUNCE = 600;
const VM_COL_X      = 160;
const ROW_H         = 160;
const GATEWAY_X     = VM_COL_X + 520;

/* ─── 類型映射（必須在元件外定義） ─────────────────────── */
const NODE_TYPES = { gateway: GatewayNode, vm: VMNode };
const EDGE_TYPES = { connection: ConnectionEdge };

/* ─── 主頁面 ─────────────────────────────────────────────── */
export default function FirewallPage() {
  const { theme } = useTheme();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [topology,     setTopology]     = useState(null);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState("");
  const [selectedNode, setSelectedNode] = useState(null);
  const [showDialog,   setShowDialog]   = useState(false);
  const [deleteEdge,   setDeleteEdge]   = useState(null);
  const [showLabels,   setShowLabels]   = useState(false);
  const [showMiniMap,  setShowMiniMap]  = useState(true);
  const rfInstance = useRef(null);
  const saveTimer  = useRef(null);

  /* ── 刪除邊回呼 ── */
  const handleDeleteEdge = useCallback((edge) => setDeleteEdge(edge), []);

  /* ── showLabels 變更時同步更新所有邊 ── */
  useEffect(() => {
    setEdges((prev) =>
      prev.map((e) => ({ ...e, data: { ...e.data, showLabel: showLabels } }))
    );
  }, [showLabels, setEdges]);

  /* ── 載入拓撲（silent = true 時不觸發 loading / error state，供背景自動刷新使用） ── */
  const fetchTopology = useCallback(async (silent = false) => {
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const data = await getTopology();
      setTopology(data);
      const { nodes: n, edges: e } = buildFlow(data, handleDeleteEdge, showLabels);
      setNodes(n);
      setEdges(e);
    } catch (err) {
      if (!silent) setError(err?.message ?? "載入拓撲失敗");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [handleDeleteEdge, showLabels, setNodes, setEdges]);

  useEffect(() => { fetchTopology(); }, [fetchTopology]);
  useAutoRefresh(() => fetchTopology(true));

  /* ── 自動排列 ── */
  const autoArrange = useCallback(() => {
    setNodes((prev) => {
      const vmNodes = prev.filter((n) => n.type === "vm");
      const gateway = prev.find((n) => n.type === "gateway");
      const startY  = 80;
      const totalH  = vmNodes.length * ROW_H;

      const arranged = vmNodes.map((node, i) => ({
        ...node,
        position: { x: VM_COL_X, y: startY + i * ROW_H },
      }));

      if (gateway) {
        arranged.push({
          ...gateway,
          position: { x: GATEWAY_X, y: startY + (totalH - ROW_H) / 2 },
        });
      }

      setTimeout(() => {
        const layoutNodes = arranged.map((n) => ({
          vmid:       n.id === GATEWAY_KEY ? null : Number(n.id),
          node_type:  n.id === GATEWAY_KEY ? "gateway" : "vm",
          position_x: Math.round(n.position.x),
          position_y: Math.round(n.position.y),
        }));
        saveLayout(layoutNodes).catch(() => {});
        rfInstance.current?.fitView({ padding: 0.2, duration: 400 });
      }, 50);

      return arranged;
    });
  }, [setNodes]);

  /* ── 節點拖曳結束 → debounce 儲存佈局 ── */
  const onNodeDragStop = useCallback((_, __, draggedNodes) => {
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(() => {
      const layoutNodes = draggedNodes.map((n) => ({
        vmid:       n.id === GATEWAY_KEY ? null : Number(n.id),
        node_type:  n.id === GATEWAY_KEY ? "gateway" : "vm",
        position_x: Math.round(n.position.x),
        position_y: Math.round(n.position.y),
      }));
      saveLayout(layoutNodes).catch(() => {});
    }, SAVE_DEBOUNCE);
  }, []);

  /* ── 點擊節點：開啟規則面板 ── */
  const onNodeClick = useCallback((_, node) => {
    if (node.type === "gateway") { setSelectedNode(null); return; }
    setSelectedNode((prev) => prev?.id === node.id ? null : node);
  }, []);

  /* ── 點擊空白處：取消選取 ── */
  const onPaneClick = useCallback(() => setSelectedNode(null), []);

  /* ── VM 節點列表（供 ConnectionDialog 使用） ── */
  const vmNodes = (topology?.nodes ?? [])
    .filter((n) => n.node_type !== "gateway")
    .map((n) => ({ key: String(n.vmid), vmid: n.vmid, name: n.name }));

  /* ── 建立連線 ── */
  const handleCreateConnection = async (data) => {
    await createConnection(data);
    setShowDialog(false);
    fetchTopology();
  };

  /* ── 確認刪除邊 ── */
  const confirmDeleteEdge = async () => {
    if (!deleteEdge) return;
    try {
      await deleteConnection({
        source_vmid: deleteEdge.source_vmid,
        target_vmid: deleteEdge.target_vmid,
        ports: null,
      });
      setDeleteEdge(null);
      fetchTopology();
    } catch (err) {
      alert(err?.message ?? "刪除失敗");
    }
  };

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <div className={styles.pageHeader}>
        <div className={styles.pageHeading}>
          <h1 className={styles.pageTitle}>防火牆</h1>
          <p className={styles.pageSubtitle}>管理 VM 之間與對外的網路連線規則</p>
        </div>
        <div className={styles.headerActions}>
          <button
            type="button"
            className={styles.btnPrimary}
            onClick={() => setShowDialog(true)}
          >
            <MIcon name="add" size={16} />
            新增連線
          </button>
        </div>
      </div>

      {/* ── Content ── */}
      <div className={styles.content}>
        {loading && !topology && (
          <div className={styles.centerState}>
            <div className={styles.topoLoader}>
              {Array.from({ length: 9 }, (_, i) => (
                <div key={i} className={styles.topoNode} style={{ "--i": i }} />
              ))}
            </div>
            <span className={styles.loadingTitle}>載入拓撲中…</span>
          </div>
        )}

        {error && (
          <div className={styles.centerState}>
            <MIcon name="error_outline" size={36} />
            <span>{error}</span>
            <button type="button" className={styles.btnSecondary} onClick={fetchTopology}>
              重試
            </button>
          </div>
        )}

        {!loading && !error && topology && (
          <div className={styles.flowWrap}>
            <ReactFlow
              nodes={nodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeDragStop={onNodeDragStop}
              onNodeClick={onNodeClick}
              onPaneClick={onPaneClick}
              onInit={(instance) => { rfInstance.current = instance; }}
              nodeTypes={NODE_TYPES}
              edgeTypes={EDGE_TYPES}
              deleteKeyCode={null}
              fitView
              fitViewOptions={{ padding: 0.2 }}
              colorMode={theme}
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
              <Controls />
              {showMiniMap && <MiniMap zoomable pannable />}

              <Panel position="top-left">
                <div className={styles.toolbar}>
                  <button
                    type="button"
                    className={styles.toolbarBtn}
                    onClick={autoArrange}
                  >
                    <MIcon name="dashboard" size={16} />
                    自動排列
                  </button>
                  <button
                    type="button"
                    className={`${styles.toolbarBtn} ${showLabels ? styles.toolbarBtnActive : ""}`}
                    onClick={() => setShowLabels((v) => !v)}
                  >
                    <MIcon name={showLabels ? "label" : "label_off"} size={16} />
                    連線標籤
                  </button>
                  <button
                    type="button"
                    className={`${styles.toolbarBtn} ${showMiniMap ? styles.toolbarBtnActive : ""}`}
                    onClick={() => setShowMiniMap((v) => !v)}
                  >
                    <MIcon name="map" size={16} />
                    地圖
                  </button>
                </div>
              </Panel>

              <Panel position="bottom-left" style={{ marginLeft: 60 }}>
                <p className={styles.hint}>
                  拖拉節點移動位置；從節點右側拖拉到另一個節點建立連線；點擊節點聚焦；再次點擊或點空白處取消
                </p>
              </Panel>
            </ReactFlow>

            {selectedNode && (
              <RulesPanel
                node={{ vmid: Number(selectedNode.id), name: selectedNode.data.name }}
                onClose={() => setSelectedNode(null)}
              />
            )}
          </div>
        )}
      </div>

      {/* ── 新增連線 Dialog ── */}
      {showDialog && (
        <ConnectionDialog
          nodes={vmNodes}
          onConfirm={handleCreateConnection}
          onClose={() => setShowDialog(false)}
        />
      )}

      {/* ── 刪除確認 ── */}
      {deleteEdge && (
        <div className={styles.confirmOverlay} onClick={() => setDeleteEdge(null)}>
          <div className={styles.confirmDialog} onClick={(e) => e.stopPropagation()}>
            <h3 className={styles.confirmTitle}>刪除連線</h3>
            <p className={styles.confirmMsg}>
              確定要刪除此連線嗎？
              {deleteEdge.ports?.length > 0 && (
                <><br /><small>{portLabel(deleteEdge.ports)}</small></>
              )}
            </p>
            <div className={styles.confirmActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => setDeleteEdge(null)}>取消</button>
              <button type="button" className={styles.btnDanger} onClick={confirmDeleteEdge}>刪除</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

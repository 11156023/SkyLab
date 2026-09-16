/**
 * FirewallPage
 * 防火牆拓撲頁面，使用 @xyflow/react 繪製互動式節點圖。
 */

import { useState, useEffect, useMemo, useRef, useCallback } from "react";
import { useTranslation } from "react-i18next";
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
  deleteConnection,
  saveLayout,
} from "../../../services/firewall";
import RulesPanel       from "../../../components/RulesPanel/RulesPanel";
import ConnectionDialog from "../../../components/ConnectionDialog/ConnectionDialog";
import {
  canConnectNode,
  canManageNode,
  isPeerNode,
  toDialogNodes,
} from "../../../components/ConnectionDialog/topologyNodes";
import GatewayNode      from "./nodes/GatewayNode";
import VMNode           from "./nodes/VMNode";
import ConnectionEdge   from "./edges/ConnectionEdge";
import ConnectionDetailPanel from "./ConnectionDetailPanel";
import { buildFlow, isOutboundEdge, portLabel, routeEdges } from "./utils/buildFlow";
import { useTheme } from "../../../contexts/ThemeContext";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import LoadingState from "../../../components/LoadingState/LoadingState";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useToast } from "../../../hooks/useToast";
import styles from "./FirewallPage.module.scss";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";

/* ─── 常數 ──────────────────────────────────────────────── */
const GATEWAY_KEY   = "gateway";
const SAVE_DEBOUNCE = 600;
const VM_COL_X      = 160;
const ROW_H         = 160;
const GATEWAY_X     = VM_COL_X + 520;

const NODE_TYPES = { gateway: GatewayNode, vm: VMNode };
const EDGE_TYPES = { connection: ConnectionEdge };

/** ReactFlow 節點 id → ConnectionDialog 的選項 key（網關節點對應 "internet"） */
const toDialogKey = (nodeId) => (nodeId === GATEWAY_KEY ? "internet" : String(nodeId));

/* ─── 主頁面 ─────────────────────────────────────────────── */
export default function FirewallPage() {
  const { t } = useTranslation("network");
  const { theme } = useTheme();
  const toast = useToast();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [topology,     setTopology]     = useState(null);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState("");
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedEdge, setSelectedEdge] = useState(null); // { id, edge }
  const [showDialog,   setShowDialog]   = useState(false);
  const [dialogPreset, setDialogPreset] = useState(null); // 拉線帶入的來源/目標
  const [deleteEdge,   setDeleteEdge]   = useState(null);
  /* 預設開啟：標籤本身就是「這條線在開什麼」的答案，不該要使用者自己去翻開 */
  const [showLabels,   setShowLabels]   = useState(true);
  const [showMiniMap,  setShowMiniMap]  = useState(true);
  /* 上網線（機器 → 網際網路的出站線）預設隱藏：幾乎每台機器都有一條，
     全畫出來會蓋掉內部互通與對外開放；對外開放是暴露面，不藏 */
  const [showInternet, setShowInternet] = useState(false);
  const [connecting,   setConnecting]   = useState(false);
  const connDialog    = useDialogPresence(showDialog);
  const deleteConfirm = useDialogPresence(deleteEdge);
  /* 關閉細項面板時先播 0.22s 滑出動畫再卸載，時長需與 SCSS 的 panelOut 一致 */
  const rulesPanel    = useDialogPresence(selectedNode, 220);
  const detailPanel   = useDialogPresence(selectedEdge, 220);
  const rfInstance = useRef(null);
  const saveTimer  = useRef(null);
  /* 重建拓撲時要沿用目前選取的邊，但選取本身不該讓整張圖重排，所以走 ref */
  const selectedEdgeIdRef = useRef(null);
  selectedEdgeIdRef.current = selectedEdge?.id ?? null;
  /* 上網線開關同理：切換由下方的同步 effect 就地套用，不重排、不 fitView */
  const showInternetRef = useRef(showInternet);
  showInternetRef.current = showInternet;

  /* ── 點選邊：開啟連線細節面板（與節點面板互斥） ── */
  const handleSelectEdge = useCallback((edge, id) => {
    setSelectedNode(null);
    setSelectedEdge((prev) => (prev?.id === id ? null : { id, edge }));
  }, []);

  /* ── 標籤／上網線開關、選取狀態變更時同步更新所有邊 ── */
  useEffect(() => {
    setEdges((prev) =>
      prev.map((e) => ({
        ...e,
        hidden: !showInternet && isOutboundEdge(e.data.edge),
        data: { ...e.data, showLabel: showLabels, selected: e.id === selectedEdge?.id },
      }))
    );
  }, [showLabels, showInternet, selectedEdge, setEdges]);

  /* ── 切換上網線：藏起來時，若正開著某條上網線的細節面板，一併關掉 ── */
  const toggleInternet = useCallback(() => {
    const next = !showInternet;
    setShowInternet(next);
    if (!next) {
      setSelectedEdge((sel) => (sel && isOutboundEdge(sel.edge) ? null : sel));
    }
  }, [showInternet]);

  /* ── 載入拓撲（silent = true 時不觸發 loading / error state，供背景自動刷新使用） ── */
  const fetchTopology = useCallback(async (silent = false, signal) => {
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const data = await getTopology({ signal });
      setTopology(data ?? { nodes: [], edges: [] });
    } catch (err) {
      if (!silent) setError(err?.message ?? t("FirewallPage.loadTopologyFailed"));
    } finally {
      if (!silent && !signal?.aborted) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!topology) return;
    const { nodes: nextNodes, edges: nextEdges } = buildFlow(topology, {
      onSelectEdge: handleSelectEdge,
      showLabel: showLabels,
      showInternet: showInternetRef.current,
      selectedEdgeId: selectedEdgeIdRef.current,
    });
    /* 拓撲刷新時保留仍存在的選取節點：規則面板可就地操作後，
       不能被 30 秒自動刷新或連線變更關掉 */
    setSelectedNode((prev) =>
      prev ? nextNodes.find((n) => n.id === prev.id) ?? null : null
    );
    /* 連線面板同理；連線被刪掉或改掉時才關閉 */
    setSelectedEdge((prev) => {
      if (!prev) return null;
      const match = nextEdges.find((e) => e.id === prev.id);
      return match ? { id: match.id, edge: match.data.edge } : null;
    });
    setDeleteEdge(null);
    setNodes(nextNodes);
    setEdges(nextEdges);
    window.requestAnimationFrame(() => rfInstance.current?.fitView({ padding: 0.2, duration: 250 }));
  }, [handleSelectEdge, setEdges, setNodes, showLabels, topology]);

  useEffect(() => {
    const controller = new AbortController();
    fetchTopology(false, controller.signal);
    return () => controller.abort();
  }, [fetchTopology]);
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

  /* ── 點擊節點：開啟規則面板（與連線面板互斥） ── */
  const onNodeClick = useCallback((_, node) => {
    setSelectedEdge(null);
    if (node.type === "gateway") { setSelectedNode(null); return; }
    setSelectedNode((prev) => prev?.id === node.id ? null : node);
  }, []);

  /* ── 點擊空白處：取消選取 ── */
  const onPaneClick = useCallback(() => {
    setSelectedNode(null);
    setSelectedEdge(null);
  }, []);

  /* ── 拉線前驗證：禁止自連（網關只有一個，自連即網關對網關） ── */
  const isValidConnection = useCallback(
    (conn) => Boolean(conn?.source && conn?.target && conn.source !== conn.target),
    []
  );

  /* ── 拉線完成：帶入來源/目標，開啟新增連線對話框 ──
     連線會同時寫兩端的規則：來源一定要能管；目標能管或是老師開放給班級的
     機器才行。擋在這裡，不讓人填完表單才吃 403 */
  const onConnect = useCallback((conn) => {
    if (!conn?.source || !conn?.target || conn.source === conn.target) return;
    const byId = new Map((topology?.nodes ?? []).map((n) => [String(n.vmid), n]));
    const src = byId.get(conn.source);
    const tgt = byId.get(conn.target);
    if (src && isPeerNode(src)) {
      toast.error(t("FirewallPage.peerSourceNotAllowed", { name: src.name }));
      return;
    }
    const blocked = [src, tgt].find((n) => n && !canManageNode(n) && !canConnectNode(n));
    if (blocked) {
      toast.error(t("FirewallPage.readOnlyNode", { name: blocked.name }));
      return;
    }
    setDialogPreset({ source: toDialogKey(conn.source), target: toDialogKey(conn.target) });
    setShowDialog(true);
  }, [topology, toast, t]);

  /* ── VM 節點列表（供 ConnectionDialog 使用）：只有可管理的機器 ── */
  const vmNodes = toDialogNodes(topology?.nodes);

  /* ── 依目前節點位置決定每條線走哪一側：拖動節點時線會即時改走最短路徑 ── */
  const routedEdges = useMemo(() => routeEdges(edges, nodes), [edges, nodes]);

  /* ── 連線面板顯示兩端名稱；vmid 為 null 代表網際網路 ── */
  const resolveName = useCallback(
    (vmid) => {
      if (vmid === null || vmid === undefined) return t("GatewayNode.internet");
      const hit = (topology?.nodes ?? []).find((n) => n.vmid === vmid);
      return hit?.name ?? `vmid:${vmid}`;
    },
    [t, topology],
  );

  /* ── 對話框送出成功（連線或自訂規則都由對話框自己呼叫 API）── */
  const handleDialogDone = () => {
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
      setSelectedEdge(null);
      fetchTopology();
    } catch (err) {
      toast.error(err?.message ?? t("FirewallPage.deleteFailed"));
    }
  };

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <PageHeader title={t("FirewallPage.title")} subtitle={t("FirewallPage.subtitle")}>
        <div className={styles.headerActions}>
          <button
            type="button"
            className={styles.btnPrimary}
            onClick={() => { setDialogPreset(null); setShowDialog(true); }}
            data-guide="firewall-create"
          >
            <MIcon name="add" size={16} />
            {t("FirewallPage.addConnection")}
          </button>
        </div>
      </PageHeader>

      {/* ── Content ── */}
      <div className={styles.content}>
        {loading && !topology && (
          <div className={styles.centerState}>
            <LoadingState text={t("FirewallPage.loadingTopology")} />
          </div>
        )}

        {error && (
          <div className={styles.centerState}>
            <MIcon name="error_outline" size={36} />
            <span>{error}</span>
            <button type="button" className={styles.btnSecondary} onClick={fetchTopology}>
              {t("FirewallPage.retry")}
            </button>
          </div>
        )}

        {!loading && !error && topology && (
          <div
            className={`${styles.flowWrap} ${connecting ? styles.connecting : ""}`}
            data-guide="firewall-map"
          >
            <ReactFlow
              nodes={nodes}
              edges={routedEdges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onNodeDragStop={onNodeDragStop}
              onNodeClick={onNodeClick}
              onPaneClick={onPaneClick}
              onConnect={onConnect}
              onConnectStart={() => setConnecting(true)}
              onConnectEnd={() => setConnecting(false)}
              isValidConnection={isValidConnection}
              connectionRadius={36}
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

              {nodes.length === 0 && (
                <Panel position="top-center">
                  <div className={styles.emptyTopology}>
                    <MIcon name="security" size={23} />
                    <strong>{t("FirewallPage.emptyTitle")}</strong>
                    <span>{t("FirewallPage.emptyDesc")}</span>
                  </div>
                </Panel>
              )}

              <Panel position="top-left">
                <div className={styles.toolbar} data-guide="firewall-tools">
                  <button
                    type="button"
                    className={styles.toolbarBtn}
                    onClick={autoArrange}
                  >
                    <MIcon name="dashboard" size={16} />
                    {t("FirewallPage.autoArrange")}
                  </button>
                  <button
                    type="button"
                    className={`${styles.toolbarBtn} ${showLabels ? styles.toolbarBtnActive : ""}`}
                    onClick={() => setShowLabels((v) => !v)}
                  >
                    <MIcon name={showLabels ? "label" : "label_off"} size={16} />
                    {t("FirewallPage.connectionLabels")}
                  </button>
                  <button
                    type="button"
                    className={`${styles.toolbarBtn} ${showInternet ? styles.toolbarBtnActive : ""}`}
                    onClick={toggleInternet}
                  >
                    <MIcon name={showInternet ? "public" : "public_off"} size={16} />
                    {t("FirewallPage.internetLines")}
                  </button>
                  <button
                    type="button"
                    className={`${styles.toolbarBtn} ${showMiniMap ? styles.toolbarBtnActive : ""}`}
                    onClick={() => setShowMiniMap((v) => !v)}
                  >
                    <MIcon name="map" size={16} />
                    {t("FirewallPage.miniMap")}
                  </button>
                </div>
              </Panel>

              <Panel position="bottom-left" style={{ marginLeft: 60 }}>
                <div className={styles.bottomStack}>
                  {/* 線的顏色本來只寫在程式碼註解裡，圖上沒有任何地方解釋 */}
                  <div className={styles.legend}>
                    <span className={styles.legendItem}>
                      <i className={`${styles.legendLine} ${styles.legendInbound}`} />
                      {t("FirewallPage.legendInbound")}
                    </span>
                    {/* 上網線藏起來時，圖例的「對外連線」變淡，提醒圖上少了這種線 */}
                    <span className={`${styles.legendItem} ${showInternet ? "" : styles.legendItemHidden}`}>
                      <i className={`${styles.legendLine} ${styles.legendOutbound}`} />
                      {t("FirewallPage.legendOutbound")}
                    </span>
                    <span className={styles.legendItem}>
                      <i className={`${styles.legendLine} ${styles.legendInternal}`} />
                      {t("FirewallPage.legendInternal")}
                    </span>
                  </div>
                  <p className={styles.hint}>
                    {t("FirewallPage.hint")}
                  </p>
                </div>
              </Panel>
            </ReactFlow>

            {rulesPanel.open && (
              <RulesPanel
                node={{ vmid: Number(rulesPanel.item.id), name: rulesPanel.item.data.name }}
                canManage={canManageNode(rulesPanel.item.data)}
                peer={isPeerNode(rulesPanel.item.data)}
                allowedPorts={rulesPanel.item.data.allowed_ports ?? []}
                ownerName={rulesPanel.item.data.owner_name ?? null}
                closing={rulesPanel.closing}
                onClose={() => setSelectedNode(null)}
                onChanged={() => fetchTopology(true)}
              />
            )}

            {detailPanel.open && (
              <ConnectionDetailPanel
                edge={detailPanel.item.edge}
                resolveName={resolveName}
                closing={detailPanel.closing}
                onClose={() => setSelectedEdge(null)}
                onDelete={(edge) => setDeleteEdge(edge)}
              />
            )}
          </div>
        )}
      </div>

      {/* ── 新增連線／自訂規則 Dialog（與資源頁共用同一份） ── */}
      {connDialog.open && (
        <ConnectionDialog
          key={dialogPreset ? `${dialogPreset.source}->${dialogPreset.target}` : "manual"}
          nodes={vmNodes}
          initialSource={dialogPreset?.source}
          initialTarget={dialogPreset?.target}
          onDone={handleDialogDone}
          onChanged={() => fetchTopology(true)}
          onClose={() => setShowDialog(false)}
          closing={connDialog.closing}
        />
      )}

      {/* ── 刪除確認 ── */}
      {deleteConfirm.open && (
        <div
          className={`${styles.confirmOverlay} ${deleteConfirm.closing ? styles.confirmOverlayOut : ""}`}
          onClick={() => setDeleteEdge(null)}
        >
          <div className={styles.confirmDialog} onClick={(e) => e.stopPropagation()}>
            <h3 className={styles.confirmTitle}>{t("FirewallPage.deleteConnectionTitle")}</h3>
            <p className={styles.confirmMsg}>
              {t("FirewallPage.deleteConnectionConfirm")}
              {deleteConfirm.item.ports?.length > 0 && (
                <><br /><small>{portLabel(deleteConfirm.item.ports)}</small></>
              )}
            </p>
            <div className={styles.confirmActions}>
              <button type="button" className={styles.btnSecondary} onClick={() => setDeleteEdge(null)}>{t("FirewallPage.cancel")}</button>
              <button type="button" className={styles.btnDanger} onClick={confirmDeleteEdge}>{t("FirewallPage.delete")}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

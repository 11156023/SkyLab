/**
 * FirewallPage
 * 防火牆拓撲頁面，使用 @xyflow/react 繪製互動式節點圖。
 */

import { useState, useEffect, useMemo, useRef, useCallback, useDeferredValue } from "react";
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
import EmptyState       from "../../../components/EmptyState/EmptyState";
import ConnectionDialog from "../../../components/ConnectionDialog/ConnectionDialog";
import { canManageNode, toDialogNodes } from "../../../components/ConnectionDialog/topologyNodes";
import GatewayNode      from "./nodes/GatewayNode";
import VMNode           from "./nodes/VMNode";
import GroupNode        from "./nodes/GroupNode";
import ConnectionEdge   from "./edges/ConnectionEdge";
import ConnectionDetailPanel from "./ConnectionDetailPanel";
import { GATEWAY_KEY, buildFlow, isOutboundEdge, portLabel, routeEdges } from "./utils/buildFlow";
import { mergePendingLayout, toLayoutEntry, topologyView } from "./utils/pageState";
import {
  GROUP_THRESHOLD,
  applyView,
  defaultCollapsed,
  groupLabelOf,
  isGroupNodeId,
  listGroupKeys,
} from "./utils/grouping";
import { useTheme } from "../../../contexts/ThemeContext";
import useAutoRefresh from "../../../hooks/useAutoRefresh";
import LoadingState from "../../../components/LoadingState/LoadingState";
import useDialogPresence from "../../../hooks/useDialogPresence";
import { useToast } from "../../../hooks/useToast";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import styles from "./FirewallPage.module.scss";
import MIcon from "../../../components/MIcon";
import PageHeader from "../../../components/PageHeader/PageHeader";

/* ─── 常數 ──────────────────────────────────────────────── */
const SAVE_DEBOUNCE = 600;
const VM_COL_X      = 160;
const ROW_H         = 160;
const GATEWAY_X     = VM_COL_X + 520;

const NODE_TYPES = { gateway: GatewayNode, vm: VMNode, vmGroup: GroupNode };

/* 縮放到全貌時上下多留空間：畫布左上有兩排工具列、左下有圖例與提示，不留會蓋住最上面的群組標題與最底下的機器 */
const FIT_PADDING = { top: "104px", bottom: "120px", x: "48px" };
/* 篩到只剩一兩台時不要放大到比原尺寸大太多 */
const FIT_MAX_ZOOM = 1.1;

/* 分組模式的群組框／網際網路節點位置：自動排好的版面只是預設，
   使用者拖過的位置存在瀏覽器本機（後端只存單機位置，分組框是純前端的檢視） */
const GROUP_LAYOUT_STORAGE_KEY = "skylab:firewall-group-layout";

function loadGroupLayout() {
  try {
    const parsed = JSON.parse(localStorage.getItem(GROUP_LAYOUT_STORAGE_KEY) ?? "{}");
    return { groups: parsed.groups ?? {}, gateway: parsed.gateway ?? null };
  } catch {
    return { groups: {}, gateway: null };
  }
}

function storeGroupLayout(layout) {
  try {
    localStorage.setItem(GROUP_LAYOUT_STORAGE_KEY, JSON.stringify(layout));
  } catch {
    /* 無痕模式等存不了：只影響下次進來的位置 */
  }
}
const EDGE_TYPES = { connection: ConnectionEdge };

/** ReactFlow 節點 id → ConnectionDialog 的選項 key（網關節點對應 "internet"） */
const toDialogKey = (nodeId) => (nodeId === GATEWAY_KEY ? "internet" : String(nodeId));

/* ─── 主頁面 ─────────────────────────────────────────────── */
export default function FirewallPage() {
  const { t } = useTranslation("network");
  const { t: tc } = useTranslation("components");
  const [guideActive, setGuideActive] = useState(false);
  const { theme } = useTheme();
  const toast = useToast();
  const confirm = useConfirm();
  const [nodes, setNodes, onNodesChange] = useNodesState([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState([]);
  const [topology,     setTopology]     = useState(null);
  const [loading,      setLoading]      = useState(true);
  const [error,        setError]        = useState("");
  const [selectedNode, setSelectedNode] = useState(null);
  const [selectedEdge, setSelectedEdge] = useState(null); // { id, edge }
  const [showDialog,   setShowDialog]   = useState(false);
  const [dialogPreset, setDialogPreset] = useState(null); // 拉線帶入的來源/目標
  /* 刪除連線進行中：避免確認後又被按第二次而送出兩筆刪除 */
  const deletingEdgeRef = useRef(false);
  /* 預設開啟：標籤本身就是「這條線在開什麼」的答案，不該要使用者自己去翻開 */
  const [showLabels,   setShowLabels]   = useState(true);
  const [showMiniMap,  setShowMiniMap]  = useState(true);
  /* 上網線（機器 → 網際網路的出站線）預設隱藏：幾乎每台機器都有一條，
     全畫出來會蓋掉內部互通與對外開放；對外開放是暴露面，不藏 */
  const [showInternet, setShowInternet] = useState(false);
  const [connecting,   setConnecting]   = useState(false);
  const connDialog    = useDialogPresence(showDialog);
  /* 關閉細項面板時先播 0.22s 滑出動畫再卸載，時長需與 SCSS 的 panelOut 一致 */
  const rulesPanel    = useDialogPresence(selectedNode, 220);
  const detailPanel   = useDialogPresence(selectedEdge, 220);
  const rfInstance = useRef(null);
  const saveTimer  = useRef(null);
  const pendingLayout = useRef(new Map());
  /* 重建拓撲時要沿用目前選取的邊，但選取本身不該讓整張圖重排，所以走 ref */
  const selectedEdgeIdRef = useRef(null);
  selectedEdgeIdRef.current = selectedEdge?.id ?? null;
  /* 上網線開關同理：切換由下方的同步 effect 就地套用，不重排、不 fitView */
  const showInternetRef = useRef(showInternet);
  showInternetRef.current = showInternet;

  /* ── 分組／收合／篩選 ──
     分組：null 代表沒手動切過，機器數達門檻就預設分組；
     收合：只記使用者手動切過的群組，其餘照預設（機器多時收、有對外開放的不收），自動刷新不會重設 */
  const [groupedPref, setGroupedPref] = useState(null);
  const [collapsedOverrides, setCollapsedOverrides] = useState(() => new Map());
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);
  const [groupFilter, setGroupFilter] = useState("");
  const [onlyExposed, setOnlyExposed] = useState(false);
  const [viewSummary, setViewSummary] = useState({ total: 0, visible: 0 });
  /* 群組框位置改了不需要重排整張圖，所以走 ref；「自動排列」才遞增 layoutVersion 重建 */
  const groupLayoutRef = useRef(loadGroupLayout());
  const [layoutVersion, setLayoutVersion] = useState(0);
  /* 最近一次畫出來時各群組是否收合，切換按鈕據此取反 */
  const collapsedNowRef = useRef(new Map());
  /* 重建節點後要縮放到全貌，但得等尺寸量好 */
  const pendingFitRef = useRef(false);

  const vmCount = useMemo(
    () => (topology?.nodes ?? []).filter((n) => n.node_type !== "gateway").length,
    [topology],
  );
  const grouped = groupedPref ?? vmCount >= GROUP_THRESHOLD;
  const groupOptions = useMemo(() => listGroupKeys(topology?.nodes), [topology]);
  /* 篩選的群組已經不在了（機器刪光）就當作沒篩 */
  const activeGroupFilter = groupOptions.includes(groupFilter) ? groupFilter : "";
  const filtersActive = Boolean(query.trim() || activeGroupFilter || onlyExposed);

  const groupName = useCallback((key) => {
    const label = groupLabelOf(key);
    return label.type === "class" ? label.name : tc(label.labelKey);
  }, [tc]);

  const toggleGroup = useCallback((key) => {
    setCollapsedOverrides((prev) => new Map(prev).set(key, !collapsedNowRef.current.get(key)));
  }, []);
  const expandGroup = useCallback((key) => {
    setCollapsedOverrides((prev) => new Map(prev).set(key, false));
  }, []);
  const clearFilters = useCallback(() => {
    setQuery("");
    setGroupFilter("");
    setOnlyExposed(false);
  }, []);

  /* ── 點選邊：開啟連線細節面板（與節點面板互斥） ── */
  const handleSelectEdge = useCallback((edge, id) => {
    setSelectedNode(null);
    setSelectedEdge((prev) => (prev?.id === id ? null : { id, edge }));
  }, []);

  /* 導覽開啟時讓連接點常駐可見，步驟聚光才有東西可看 */
  useEffect(() => {
    const handleGuideState = (event) => setGuideActive(Boolean(event.detail?.open && event.detail?.id === "firewall"));
    window.addEventListener("skylab:user-guide-state", handleGuideState);
    return () => window.removeEventListener("skylab:user-guide-state", handleGuideState);
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

  /* ── 載入拓撲（silent = true 時不觸發 loading、失敗也不設 error，供背景自動刷新使用） ── */
  const fetchTopology = useCallback(async (silent = false, signal) => {
    if (!silent) {
      setLoading(true);
      setError("");
    }
    try {
      const data = await getTopology({ signal });
      setTopology(data ?? { nodes: [], edges: [] });
      /* 背景刷新成功也要清掉先前的錯誤，否則首次載入失敗後錯誤畫面會一直留著 */
      setError("");
    } catch (err) {
      if (!silent) setError(err?.message ?? t("FirewallPage.loadTopologyFailed"));
    } finally {
      if (!silent && !signal?.aborted) setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (!topology) return;
    const flow = buildFlow(topology, {
      onSelectEdge: handleSelectEdge,
      showLabel: showLabels,
      showInternet: showInternetRef.current,
      selectedEdgeId: selectedEdgeIdRef.current,
    });
    const view = applyView(flow, topology.edges ?? [], {
      grouped,
      isCollapsed: (group) => (collapsedOverrides.has(group.key)
        ? collapsedOverrides.get(group.key)
        : defaultCollapsed(group, vmCount)),
      filter: { query: deferredQuery, groupKey: activeGroupFilter, onlyExposed },
      positions: groupLayoutRef.current,
      onExpandGroup: expandGroup,
      onToggleGroup: toggleGroup,
    });
    collapsedNowRef.current = new Map(view.groups.map((group) => [group.key, group.collapsed]));
    setViewSummary({ total: view.totalVms, visible: view.visibleVms });
    const { nodes: nextNodes, edges: nextEdges } = view;
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
    setNodes(nextNodes);
    setEdges(nextEdges);
    /* 新節點要等 ReactFlow 量好尺寸才縮放得準（立刻縮會被當成還沒初始化而略過），交給下面的 effect */
    pendingFitRef.current = true;
  }, [
    handleSelectEdge, setEdges, setNodes, showLabels, topology,
    grouped, collapsedOverrides, vmCount, deferredQuery, activeGroupFilter, onlyExposed,
    expandGroup, toggleGroup, layoutVersion,
  ]);

  /* 重建後全部節點都量到尺寸（dimension 變更回填 measured）才縮放到全貌 */
  useEffect(() => {
    if (!pendingFitRef.current || nodes.length === 0) return;
    if (!nodes.every((n) => n.hidden || (n.measured?.width && n.measured?.height))) return;
    pendingFitRef.current = false;
    rfInstance.current?.fitView({ padding: FIT_PADDING, maxZoom: FIT_MAX_ZOOM, duration: 250 });
  }, [nodes]);

  useEffect(() => {
    const controller = new AbortController();
    fetchTopology(false, controller.signal);
    return () => controller.abort();
  }, [fetchTopology]);
  useAutoRefresh(() => fetchTopology(true));

  /* ── 自動排列 ── */
  const autoArrange = useCallback(() => {
    /* 分組模式：丟掉拖過的群組位置，回到依序堆疊的預設版面 */
    if (grouped) {
      groupLayoutRef.current = { groups: {}, gateway: null };
      storeGroupLayout(groupLayoutRef.current);
      setLayoutVersion((v) => v + 1);
      return;
    }
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
        /* 自動排列涵蓋所有節點：丟掉還沒送出的拖曳位置，避免晚一步把排好的位置蓋回去 */
        clearTimeout(saveTimer.current);
        pendingLayout.current.clear();
        const layoutNodes = arranged.map((n) => toLayoutEntry(n, GATEWAY_KEY));
        saveLayout(layoutNodes).catch(() => {});
        rfInstance.current?.fitView({ padding: FIT_PADDING, maxZoom: FIT_MAX_ZOOM, duration: 400 });
      }, 50);

      return arranged;
    });
  }, [grouped, setNodes]);

  /* ── 節點拖曳結束 → debounce 儲存佈局 ── */
  /* debounce 期間累積所有被拖過的節點，計時到了一次送出，前一次拖曳的位置不會被丟掉 */
  const flushPendingLayout = useCallback(() => {
    clearTimeout(saveTimer.current);
    saveTimer.current = null;
    if (pendingLayout.current.size === 0) return;
    const layoutNodes = [...pendingLayout.current.values()];
    pendingLayout.current.clear();
    saveLayout(layoutNodes).catch(() => {});
  }, []);

  const onNodeDragStop = useCallback((_, __, draggedNodes) => {
    /* 分組模式：機器固定在群組框的格子裡，能拖的只有群組框與網際網路，位置存本機 */
    if (grouped) {
      const layout = groupLayoutRef.current;
      for (const node of draggedNodes ?? []) {
        if (node.type === "vmGroup") layout.groups[node.data.groupKey] = { ...node.position };
        else if (node.id === GATEWAY_KEY) layout.gateway = { ...node.position };
      }
      storeGroupLayout(layout);
      return;
    }
    mergePendingLayout(pendingLayout.current, draggedNodes, GATEWAY_KEY);
    clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(flushPendingLayout, SAVE_DEBOUNCE);
  }, [flushPendingLayout, grouped]);

  /* 離開頁面時把還沒送出的佈局存掉 */
  useEffect(() => flushPendingLayout, [flushPendingLayout]);

  /* ── 點擊節點：開啟規則面板（與連線面板互斥） ── */
  const onNodeClick = useCallback((_, node) => {
    setSelectedEdge(null);
    /* 群組框本身沒有規則可看；收合切換由標題上的按鈕負責 */
    if (node.type === "gateway" || isGroupNodeId(node.id)) { setSelectedNode(null); return; }
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
     連線會同時寫兩端的規則，任一端是唯讀的課堂機（學生視角）就擋在這裡，
     不讓人填完表單才吃 403 */
  const onConnect = useCallback((conn) => {
    if (!conn?.source || !conn?.target || conn.source === conn.target) return;
    const byId = new Map((topology?.nodes ?? []).map((n) => [String(n.vmid), n]));
    const readOnly = [conn.source, conn.target]
      .map((id) => byId.get(id))
      .find((n) => n && !canManageNode(n));
    if (readOnly) {
      toast.error(t("FirewallPage.readOnlyNode", { name: readOnly.name }));
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

  /* ── 刪除邊：先確認再送出；送出期間上鎖，連按兩下不會送兩筆 ── */
  const requestDeleteEdge = async (edge) => {
    if (!edge || deletingEdgeRef.current) return;
    const ports = edge.ports?.length > 0 ? portLabel(edge.ports) : "";
    const ok = await confirm({
      title: t("FirewallPage.deleteConnectionTitle"),
      message: ports
        ? `${t("FirewallPage.deleteConnectionConfirm")}\n${ports}`
        : t("FirewallPage.deleteConnectionConfirm"),
      confirmText: t("FirewallPage.delete"),
      cancelText: t("FirewallPage.cancel"),
      danger: true,
    });
    if (!ok) return;
    deletingEdgeRef.current = true;
    try {
      await deleteConnection({
        source_vmid: edge.source_vmid,
        target_vmid: edge.target_vmid,
        ports: null,
      });
      setSelectedEdge(null);
      fetchTopology();
    } catch (err) {
      toast.error(err?.message ?? t("FirewallPage.deleteFailed"));
    } finally {
      deletingEdgeRef.current = false;
    }
  };

  /* 已有拓撲時重新載入不卸載整張圖（規則面板、連線面板一併保留） */
  const contentView = topologyView({ loading, error, topology });

  return (
    <div className={styles.page}>
      {/* ── Header ── */}
      <PageHeader title={t("FirewallPage.title")}>
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
        {contentView === "spinner" && (
          <div className={styles.centerState}>
            <LoadingState text={t("FirewallPage.loadingTopology")} />
          </div>
        )}

        {contentView === "error" && (
          <div className={styles.centerState}>
            <MIcon name="error_outline" size={36} />
            <span>{error}</span>
            {/* 不能直接綁 fetchTopology：MouseEvent 會被當成 silent=true，error 永遠清不掉 */}
            <button type="button" className={styles.btnSecondary} onClick={() => fetchTopology()}>
              {t("FirewallPage.retry")}
            </button>
          </div>
        )}

        {contentView === "graph" && (
          <div
            className={`${styles.flowWrap} ${connecting || guideActive ? styles.connecting : ""}`}
            data-guide="firewall-map"
          >
            {guideActive && nodes.length === 0 && (
              <div className={styles.guideTopologyDemo} aria-label={t("FirewallPage.guideDemoAriaLabel")}>
                <div className={`${styles.guideDemoNode} ${styles.guideDemoNodeA}`}>
                  <span className={styles.guideDemoHandleIn} data-firewall-handle="target" />
                  <MIcon name="terminal" size={23} /><strong>demo-web-01</strong><small>10.20.0.24</small>
                  <span className={styles.guideDemoHandleOut} data-firewall-handle="source" data-guide="firewall-drag-start" />
                </div>
                <div className={`${styles.guideDemoNode} ${styles.guideDemoNodeB}`}>
                  <span className={styles.guideDemoHandleIn} data-firewall-handle="target" data-guide="firewall-drag-end" />
                  <MIcon name="storage" size={23} /><strong>demo-db-01</strong><small>10.20.0.31</small>
                  <span className={styles.guideDemoHandleOut} data-firewall-handle="source" />
                </div>
                <span className={styles.guideDemoBadge}>{t("FirewallPage.guideDemoBadge")}</span>
              </div>
            )}
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
              fitViewOptions={{ padding: FIT_PADDING, maxZoom: FIT_MAX_ZOOM }}
              colorMode={theme}
              proOptions={{ hideAttribution: true }}
            >
              <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
              <Controls />
              {showMiniMap && <MiniMap zoomable pannable />}


              <Panel position="top-left">
                <div className={styles.toolbarStack}>
                  <div className={styles.toolbar} data-guide="firewall-tools">
                    <button
                      type="button"
                      className={styles.toolbarBtn}
                      onClick={autoArrange}
                    >
                      {/* 分組模式位置由程式算，這顆只是丟掉拖過的群組位置 */}
                      <MIcon name={grouped ? "restart_alt" : "dashboard"} size={16} />
                      {t(grouped ? "FirewallPage.resetLayout" : "FirewallPage.autoArrange")}
                    </button>
                    {/* 分組：班級／機器類型各包成一個群組框，可收合（機器多時預設分組） */}
                    <button
                      type="button"
                      className={`${styles.toolbarBtn} ${grouped ? styles.toolbarBtnActive : ""}`}
                      aria-pressed={grouped}
                      onClick={() => setGroupedPref(!grouped)}
                    >
                      <MIcon name={grouped ? "workspaces" : "scatter_plot"} size={16} />
                      {t("FirewallPage.groupToggle")}
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
                      <MIcon name={showMiniMap ? "map" : "location_off"} size={16} />
                      {t("FirewallPage.miniMap")}
                    </button>
                    {/* 只看有暴露面的機器（網際網路開進來的）：防火牆頁真正該盯的就是這些 */}
                    <button
                      type="button"
                      className={`${styles.toolbarBtn} ${onlyExposed ? styles.toolbarBtnActive : ""}`}
                      aria-pressed={onlyExposed}
                      onClick={() => setOnlyExposed((v) => !v)}
                    >
                      <MIcon name={onlyExposed ? "filter_alt" : "filter_alt_off"} size={16} />
                      {t("FirewallPage.onlyExposed")}
                    </button>
                  </div>

                </div>
              </Panel>

              {/* 篩選列放右上角，跟左上的工具列分開；選了機器時規則面板會佔住右上，
                  篩選列改排到工具列下方（往左讓位會跟工具列撞在一起） */}
              <Panel
                position="top-right"
                className={`${styles.filterPanel} ${rulesPanel.open ? styles.filterPanelBelow : ""}`}
              >
                {/* 篩選列：搜尋名稱／IP、只看某個群組，兩個控制項各自成框 */}
                <div className={styles.filterBar} role="search">
                  <label className={styles.filterSearch}>
                    <MIcon name="search" size={16} />
                    <input
                      type="search"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder={t("FirewallPage.searchPlaceholder")}
                      aria-label={t("FirewallPage.searchPlaceholder")}
                    />
                  </label>
                  {groupOptions.length > 1 && (
                    <select
                      className={styles.filterSelect}
                      value={activeGroupFilter}
                      onChange={(e) => setGroupFilter(e.target.value)}
                      aria-label={t("FirewallPage.groupFilterLabel")}
                    >
                      <option value="">{t("FirewallPage.groupFilterAll")}</option>
                      {groupOptions.map((key) => (
                        <option key={key} value={key}>{groupName(key)}</option>
                      ))}
                    </select>
                  )}
                </div>
              </Panel>

              <Panel position="bottom-left" style={{ marginLeft: 60 }}>
                <div className={styles.bottomStack}>
                  {/* 有機器被篩掉時講出台數，不然會以為機器不見了 */}
                  {filtersActive && viewSummary.visible > 0 && viewSummary.visible < viewSummary.total && (
                    <div className={styles.filteredNote}>
                      <MIcon name="filter_alt" size={14} />
                      {t("FirewallPage.hiddenCount", { count: viewSummary.total - viewSummary.visible })}
                      <button type="button" onClick={clearFilters}>{t("FirewallPage.clearFilters")}</button>
                    </div>
                  )}
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

            {/* 畫布空狀態：共用 EmptyState 置中浮在畫布上（放在 ReactFlow 外，才不會跟工具列擠在頂端） */}
            {viewSummary.total > 0 && viewSummary.visible === 0 && (
              <div className={styles.emptyOverlay}>
                <EmptyState
                  icon="filter_alt_off"
                  title={t("FirewallPage.noMatch")}
                  action={<button type="button" className={styles.btnSecondary} onClick={clearFilters}><MIcon name="filter_alt_off" size={16} />{t("FirewallPage.clearFilters")}</button>}
                />
              </div>
            )}
            {/* 看全部機器是不是零台，不看畫面上的節點數（篩光時節點也是零，但那是上面那個空狀態） */}
            {viewSummary.total === 0 && !guideActive && (
              <div className={styles.emptyOverlay}>
                <EmptyState icon="security" title={t("FirewallPage.emptyTitle")} description={t("FirewallPage.emptyDesc")} />
              </div>
            )}

            {rulesPanel.open && (
              <RulesPanel
                key={rulesPanel.item.id}
                node={{ vmid: Number(rulesPanel.item.id), name: rulesPanel.item.data.name }}
                canManage={canManageNode(rulesPanel.item.data)}
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
                onDelete={requestDeleteEdge}
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

    </div>
  );
}

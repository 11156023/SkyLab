import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Background,
  Handle,
  Panel,
  Position,
  ReactFlow,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import LoadingState from "../../../components/LoadingState/LoadingState";
import MIcon from "../../../components/MIcon";
import { useConfirm } from "../../../components/ConfirmDialog/ConfirmProvider";
import { CourseEnvironmentsService } from "../../../services/courseEnvironments";
import { apiGet } from "../../../services/api";
import { focusInvalidField } from "../../../utils/focusField";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import EmptyState from "../../../components/EmptyState/EmptyState";
import { TemplatesService } from "../../../services/templates";
import ConnectionEdge from "../../network/firewall/edges/ConnectionEdge";
import styles from "../CourseOperations.module.scss";
import PageHeader from "../../../components/PageHeader/PageHeader";
import i18n from "../../../i18n";
import { AuthStorage } from "../../../services/auth";
import { createEnvironmentAutosave } from "./environmentAutosave";

const TABS = [
  ["basic", "CourseTemplateEditorPage.tabBasicLabel"],
  ["machines", "CourseTemplateEditorPage.tabMachinesLabel"],
];

function makeEmptyTemplate() {
  return { id: "new", name: "", description: "", usageScope: "course", status: "draft", classes: 0, updatedAt: i18n.t("CourseTemplateEditorPage.notSavedYet", { ns: "teaching" }), nodes: [], edges: [], publications: [] };
}

const FIREWALL_PROTOCOLS = ["tcp", "udp", "icmp", "icmpv6", "sctp"];

/** 規格滑桿範圍；後端上限為 64 核 / 128 GB RAM / 2000 GB Disk，這裡取教學情境的保守值。 */
const CPU_RANGE = [1, 32];
const MEMORY_RANGE = [1, 64];
const LXC_DISK_RANGE = [1, 1000];
const VM_DISK_RANGE = [10, 1000];

/** 一條連線實際授予的方向：單向一個，雙向兩個。 */
function edgeGrants(edge) {
  const pairs = [[edge.source, edge.target]];
  if (edge.direction === "bidirectional") pairs.push([edge.target, edge.source]);
  return pairs.map(([source, target]) => ({ source, target, protocol: edge.protocol, port: edge.port }));
}

/**
 * 這條連線是否與既有連線重疊。
 * 比對授予的方向而非欄位組合，才抓得到「A→B 單向」被「A↔B 雙向」涵蓋、
 * 以及「A↔B」與「B↔A」其實是同一件事。舊資料的 "any" 不分協定與 port。
 */
function overlapsExistingEdge(candidate, existingEdges) {
  const wanted = edgeGrants(candidate);
  return existingEdges.some((edge) => edge.id !== candidate.id && edgeGrants(edge).some((granted) => wanted.some((want) => (
    granted.source === want.source
    && granted.target === want.target
    && (granted.protocol === "any" || want.protocol === "any"
      || (granted.protocol === want.protocol && Number(granted.port) === Number(want.port)))
  ))));
}

/** 主機名樣板用的機器代稱：取名稱的前兩段，避免整串映像檔名進網址。 */
function hostnameSlug(name) {
  const parts = String(name).toLowerCase().replace(/[^a-z0-9]+/g, "-").split("-").filter(Boolean);
  return parts.slice(0, 2).join("-").slice(0, 20).replace(/-$/, "") || "app";
}

/** LXC 映像是 tarball，檔名直接當機器名稱又臭又長，去掉封裝副檔名。 */
function stripImageExtension(name) {
  return String(name).replace(/\.tar(\.(gz|xz|zst|bz2|lzo))?$/i, "");
}

function TopologyMachineNode({ data, selected, isConnectable }) {
  const { t } = useTranslation("teaching");
  const node = data.node;
  return <div className={`${styles.flowMachineNode} ${selected ? styles.flowMachineNodeSelected : ""}`}>
    <Handle type="target" position={Position.Left} isConnectable={isConnectable} />
    <div className={styles.flowNodeIcon}><MIcon name={node.type === "lxc" ? "terminal" : "dns"} size={18} /></div>
    <div className={styles.flowNodeLabel}>
      <strong title={node.name}>{node.name}</strong>
      <span>{node.sourceType === "custom" ? t("CourseTemplateEditorPage.sourceCustomShort") : t("CourseTemplateEditorPage.sourceTemplateShort")} · {node.type === "lxc" ? t("CourseTemplateEditorPage.typeContainerLxc") : t("CourseTemplateEditorPage.typeVm")}</span>
      <small>{node.cpu} CPU · {node.memory} GB RAM · {node.disk} GB</small>
    </div>
    <Handle type="source" position={Position.Right} isConnectable={isConnectable} />
  </div>;
}

const TOPOLOGY_NODE_TYPES = { courseMachine: TopologyMachineNode };
const TOPOLOGY_EDGE_TYPES = { connection: ConnectionEdge };

/** 對外服務的設定對話框：欄位放這裡，側欄只留一行摘要。 */
function PublicationDialog({ draft, zones, siblings, closing = false, onChange, onSave, onClose }) {
  const { t } = useTranslation("teaching");

  useEffect(() => {
    const onKeyDown = (event) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const isDomain = draft.mode === "domain";
  const duplicated = isDomain && siblings.some((item) => (
    item.id !== draft.id && item.mode === "domain" && item.hostnamePrefix === draft.hostnamePrefix
  ));
  const missingPlaceholder = isDomain && (
    !draft.hostnamePrefix.includes("{class}") || !draft.hostnamePrefix.includes("{student}")
  );
  const hostnameValid = !isDomain
    || (!missingPlaceholder && Boolean(draft.zoneId) && !duplicated);
  const zone = zones.find((item) => item.id === draft.zoneId);
  const preview = `${String(draft.hostnamePrefix || "").replace("{class}", "linux101-a1b2c3").replace("{student}", "s8f21c4a2")}${zone ? `.${zone.name}` : ""}`;

  /* 玻璃卡工作區的 backdrop-filter 會困住 fixed 遮罩，portal 到 body 才能全頁覆蓋 */
  return createPortal(<div className={`${styles.createDialogOverlay} ${closing ? styles.createDialogOverlayOut : ""}`} role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section className={`${styles.createDialog} ${styles.publicationDialog}`} role="dialog" aria-modal="true" aria-labelledby="publication-dialog-title">
      <header className={styles.createDialogHeader}>
        <h2 id="publication-dialog-title">{t("CourseTemplateEditorPage.publicAccessLabel")}</h2>
        <button type="button" className={styles.iconBtn} aria-label={t("CourseTemplateEditorPage.closeAriaLabel")} onClick={onClose}><MIcon name="close" size={19} /></button>
      </header>
      <div className={styles.publicationDialogBody}>
        <div className={styles.inspectorSplit}>
          <label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldInternalPort")}</span><input type="number" min="1" max="65535" value={draft.port} onChange={(event) => onChange({ port: Number(event.target.value) })} /></label>
          <label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldPublishMode")}</span><select value={draft.mode} onChange={(event) => onChange({ mode: event.target.value })}><option value="domain" disabled={!zones.length}>{t("CourseTemplateEditorPage.publishModeDomain")}</option><option value="firewall_only">{t("CourseTemplateEditorPage.publishModeFirewallOnly")}</option></select></label>
        </div>
        {!zones.length && <p className={styles.inspectorHint}>{t("CourseTemplateEditorPage.noZoneHint")}</p>}
        {isDomain && <>
          <label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldHostnameTemplate")}</span><input value={draft.hostnamePrefix} onChange={(event) => onChange({ hostnamePrefix: event.target.value })} placeholder="{class}-{student}-app" /></label>
          <label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldZone")}</span><select value={draft.zoneId} onChange={(event) => onChange({ zoneId: event.target.value })}>{zones.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
          <p className={styles.inspectorHint}>{missingPlaceholder ? t("CourseTemplateEditorPage.hostnamePlaceholderRequired") : duplicated ? t("CourseTemplateEditorPage.duplicateHostnameHint") : t("CourseTemplateEditorPage.hostnameTemplateHint", { example: preview })}</p>
        </>}
      </div>
      <footer className={styles.createDialogFooter}>
        <button type="button" className={styles.btnSecondary} onClick={onClose}>{t("CourseTemplateEditorPage.cancelBtn")}</button>
        <button type="button" className={styles.btnPrimary} disabled={!hostnameValid} onClick={() => onSave(draft)}>{t("CourseTemplateEditorPage.confirmBtn")}</button>
      </footer>
    </section>
  </div>, document.body);
}

function MachineEditor({ value, edges, publications, onChange, onEdgesChange, onPublicationsChange, pveTemplates, vmImages, lxcImages, zones, sourceNotice, locked = false, actions = null }) {
  const { t } = useTranslation("teaching");
  const [sourceMode, setSourceMode] = useState("template");
  const [sourceId, setSourceId] = useState("");
  const [customType, setCustomType] = useState("qemu");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [selectedEdgeId, setSelectedEdgeId] = useState("");
  const [flowNodes, setFlowNodes, onFlowNodesChange] = useNodesState([]);
  const [topologyNotice, setTopologyNotice] = useState("");
  const [publicationDraft, setPublicationDraft] = useState(null);
  /* 關閉時先播離場動畫再卸載（樣式規範的標準作法） */
  const publicationPresence = useDialogPresence(publicationDraft);
  const sourceOptions = sourceMode === "template" ? pveTemplates : (customType === "lxc" ? lxcImages : vmImages);
  const atLimit = value.length >= 3;

  function addMachine() {
    if (atLimit || !sourceId) return;
    const nodeId = `node-${Date.now()}`;
    if (sourceMode === "template") {
      const source = pveTemplates.find((item) => String(item.id) === sourceId);
      if (!source) return;
      onChange([...value, {
        id: nodeId, sourceType: "template", sourceTemplateId: source.id, name: source.name, role: t("CourseTemplateEditorPage.defaultMachineRole"),
        type: String(source.resource_type).toLowerCase() === "lxc" ? "lxc" : "qemu", image: source.name, cpu: source.default_cores ?? 2,
        memory: Math.max(1, Math.round((source.default_memory ?? 2048) / 1024)), disk: source.default_disk ?? 24,
        network: "lab-net", icon: "dns", positionX: 60 + value.length * 260, positionY: 120,
      }]);
    } else {
      const source = (customType === "lxc" ? lxcImages : vmImages).find((item) => String(item.value) === sourceId);
      if (!source) return;
      onChange([...value, {
        id: nodeId, sourceType: "custom", sourceTemplateId: null, customImageRef: source.value,
        customUsername: "student", customUnprivileged: true,
        name: stripImageExtension(source.label.split(" · ")[0]), role: t("CourseTemplateEditorPage.defaultMachineRole"), type: customType, image: source.label,
        cpu: customType === "lxc" ? 2 : (source.cores ?? 2),
        memory: customType === "lxc" ? 2 : Math.max(1, Math.round((source.memoryMb ?? 2048) / 1024)),
        disk: customType === "lxc" ? 8 : Math.max(VM_DISK_RANGE[0], source.diskGb ?? 20),
        network: "lab-net", icon: "dns",
        positionX: 60 + value.length * 260, positionY: 120,
      }]);
    }
    setSelectedNodeId(nodeId);
    setSelectedEdgeId("");
    setSourceId("");
  }

  function removeMachine(nodeId) {
    onChange(value.filter((item) => item.id !== nodeId));
    onEdgesChange(edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
    onPublicationsChange(publications.filter((item) => item.nodeKey !== nodeId));
    setSelectedNodeId("");
  }

  function newPublication(node) {
    const used = new Set(publications.filter((item) => item.nodeKey === node.id).map((item) => `${item.port}/${item.protocol}`));
    const port = [80, 443, 8080, 3000, 5678].find((candidate) => !used.has(`${candidate}/tcp`)) ?? 8000;
    return {
      id: `publication-${Date.now()}`,
      nodeKey: node.id,
      mode: zones.length ? "domain" : "firewall_only",
      port,
      protocol: "tcp",
      // 樣板必須帶 {student}，否則全班會搶同一個網址；同一份環境裡也不能重複，
      // 一個網址只能指向一個 port
      hostnamePrefix: uniqueHostnamePrefix(`{class}-{student}-${hostnameSlug(node.name)}`, port),
      zoneId: zones[0]?.id ?? "",
      enableHttps: true,
    };
  }

  /** 樣板撞到既有的就補上 port，避免多條網址指向同一個位址。 */
  function uniqueHostnamePrefix(base, port) {
    const taken = new Set(publications.filter((item) => item.mode === "domain").map((item) => item.hostnamePrefix));
    return taken.has(base) ? `${base}-${port}` : base;
  }

  function savePublication(draft) {
    const exists = publications.some((item) => item.id === draft.id);
    onPublicationsChange(exists
      ? publications.map((item) => item.id === draft.id ? draft : item)
      : [...publications, draft]);
    setPublicationDraft(null);
  }

  function removePublication(publicationId) {
    onPublicationsChange(publications.filter((item) => item.id !== publicationId));
  }

  function connect(connection) {
    if (locked || connection.source === connection.target) return;
    const edge = {
      id: `edge-${Date.now()}`,
      source: connection.source,
      target: connection.target,
      direction: "one_way",
      protocol: "tcp",
      port: 22,
    };
    if (overlapsExistingEdge(edge, edges)) {
      setTopologyNotice(t("CourseTemplateEditorPage.overlappingEdgeNotice"));
      return;
    }
    setTopologyNotice("");
    onEdgesChange([...edges, edge]);
    setSelectedEdgeId(edge.id);
    setSelectedNodeId("");
  }

  function patchNode(nodeId, patch) {
    onChange(value.map((item) => item.id === nodeId ? { ...item, ...patch } : item));
  }

  function patchEdge(patch) {
    const current = edges.find((edge) => edge.id === selectedEdgeId);
    if (!current) return;
    const next = { ...current, ...patch };
    // 改成雙向或換 port 都可能撞到既有連線，改之前先擋，別等存檔才失敗。
    if (overlapsExistingEdge(next, edges)) {
      setTopologyNotice(t("CourseTemplateEditorPage.overlappingEdgeNotice"));
      return;
    }
    setTopologyNotice("");
    onEdgesChange(edges.map((edge) => edge.id === selectedEdgeId ? next : edge));
  }

  function removeEdge(edgeId) {
    onEdgesChange(edges.filter((edge) => edge.id !== edgeId));
    setSelectedEdgeId("");
  }

  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId);
  const selectedNode = value.find((node) => node.id === selectedNodeId) ?? (!selectedEdge ? value[0] : null);
  // 來自 PVE 範本的機器沿用範本規格，只有自訂規格可調整。
  const specLocked = locked || selectedNode?.sourceType !== "custom";
  // 自訂規格的 VM 其實也是克隆一台 PVE 範本機，磁碟不可小於該範本。
  const customVmImage = selectedNode?.sourceType === "custom" && selectedNode?.type !== "lxc"
    ? vmImages.find((item) => item.value === String(selectedNode.customImageRef))
    : null;
  const vmDiskFloor = Math.max(VM_DISK_RANGE[0], Number(customVmImage?.diskGb) || 0);
  const diskRange = selectedNode?.type === "lxc"
    ? LXC_DISK_RANGE
    : [vmDiskFloor, Math.max(VM_DISK_RANGE[1], vmDiskFloor)];

  const nodePublications = publications.filter((item) => item.nodeKey === selectedNode?.id);

  /** 給老師看的示範網址：使用課堂代號與匿名學生識別碼。 */
  function previewDomain(publication) {
    const zone = zones.find((item) => item.id === publication.zoneId);
    const hostname = String(publication.hostnamePrefix || "").replace("{class}", "linux101-a1b2c3").replace("{student}", "s8f21c4a2");
    return zone ? `${hostname}.${zone.name}` : hostname;
  }

  // 範本清單是非同步載入的，既有節點可能存著低於下限的磁碟值，補正一次。
  useEffect(() => {
    if (specLocked || !selectedNode || selectedNode.disk >= diskRange[0]) return;
    patchNode(selectedNode.id, { disk: diskRange[0] });
  }, [specLocked, selectedNode, diskRange[0]]);
  // 畫布節點交給 ReactFlow 自己維護：拖曳時只更新畫布，不會讓整個編輯器重繪。
  // 已在畫布上的節點沿用當下位置，避免規格變更把拖到一半的節點彈回去。
  useEffect(() => {
    setFlowNodes((previous) => {
      const placed = new Map(previous.map((item) => [item.id, item.position]));
      return value.map((node, index) => ({
        id: String(node.id),
        type: "courseMachine",
        position: placed.get(String(node.id)) ?? {
          x: Number(node.positionX ?? (60 + index * 260)),
          y: Number(node.positionY ?? (120 + (index % 2) * 45)),
        },
        data: { node },
        selected: selectedNode?.id === node.id,
      }));
    });
  }, [value, selectedNode?.id, setFlowNodes]);

  // 位置只在放開滑鼠時回寫，一次拖曳只產生一筆變更。
  const commitNodePositions = useCallback((_event, _node, draggedNodes) => {
    const moved = new Map(draggedNodes.map((item) => [item.id, item.position]));
    onChange(value.map((node) => {
      const position = moved.get(String(node.id));
      return position
        ? { ...node, positionX: Math.round(position.x), positionY: Math.round(position.y) }
        : node;
    }));
  }, [onChange, value]);

  const graphEdges = useMemo(() => edges.map((edge) => ({
    ...edge,
    type: "connection",
    data: {
      edge: {
        course_edge_id: edge.id,
        source_vmid: edge.source,
        target_vmid: edge.target,
      },
      label: `${edge.direction === "bidirectional" ? t("CourseTemplateEditorPage.directionBidirectional") : t("CourseTemplateEditorPage.directionOneWay")} · ${edge.protocol}${edge.port ? `/${edge.port}` : ""}`,
      showLabel: true,
      onSelect: () => { setSelectedEdgeId(edge.id); setSelectedNodeId(""); },
      onDelete: locked ? null : () => removeEdge(edge.id),
    },
    zIndex: 5,
  })), [edges, locked, t]);

  return <section className={`${styles.card} ${styles.templateMachineWorkspace}`}>
      {sourceNotice && <p className={styles.persistentFeedback}><MIcon name="info" size={17} />{sourceNotice}</p>}
      {topologyNotice && <p className={styles.persistentFeedback}><MIcon name="info" size={17} />{topologyNotice}</p>}
      <div className={styles.machineAddBar}>
        <label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldSourceMode")}</span><select value={sourceMode} disabled={locked || atLimit} onChange={(event) => { setSourceMode(event.target.value); setSourceId(""); }}><option value="template">{t("CourseTemplateEditorPage.sourceModeTemplateOption")}</option><option value="custom">{t("CourseTemplateEditorPage.sourceModeCustomOption")}</option></select></label>
        {sourceMode === "custom" && <label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldMachineType")}</span><select value={customType} disabled={locked || atLimit} onChange={(event) => { setCustomType(event.target.value); setSourceId(""); }}><option value="qemu">VM</option><option value="lxc">LXC</option></select></label>}
        <label className={styles.field}><span>{sourceMode === "template" ? t("CourseTemplateEditorPage.sourceExistingTemplate") : t("CourseTemplateEditorPage.fieldBaseImage")}</span><select value={sourceId} disabled={locked || atLimit} onChange={(event) => setSourceId(event.target.value)}><option value="">{locked ? t("CourseTemplateEditorPage.publishedLockedOption") : atLimit ? t("CourseTemplateEditorPage.atLimitOption") : sourceOptions.length === 0 ? t("CourseTemplateEditorPage.noSourceOption") : t("CourseTemplateEditorPage.pleaseSelectOption")}</option>{sourceMode === "template" ? sourceOptions.map((source) => <option key={source.id} value={source.id}>{source.name} · {source.resource_type ?? "VM"}</option>) : sourceOptions.map((source) => <option key={source.value} value={source.value}>{source.label}</option>)}</select></label>
        <button type="button" className={styles.btnPrimary} disabled={locked || atLimit || !sourceId} onClick={addMachine}><MIcon name={atLimit ? "check" : "add"} size={16} />{atLimit ? t("CourseTemplateEditorPage.atLimitBtn") : t("CourseTemplateEditorPage.addMachineBtn")}</button>
      </div>
      {value.length ? <>
        <div className={styles.topologyWorkspace}>
          <div className={styles.topologyCanvas}><ReactFlow
            nodes={flowNodes}
            edges={graphEdges}
            nodeTypes={TOPOLOGY_NODE_TYPES}
            edgeTypes={TOPOLOGY_EDGE_TYPES}
            onConnect={connect}
            onNodesChange={onFlowNodesChange}
            onNodeDragStop={commitNodePositions}
            onNodeClick={(_, node) => { setSelectedNodeId(node.id); setSelectedEdgeId(""); }}
            onEdgeClick={(_, edge) => { setSelectedEdgeId(edge.id); setSelectedNodeId(""); }}
            nodesDraggable={!locked}
            nodesConnectable={!locked}
            connectionLineStyle={{ stroke: "var(--color-primary)", strokeWidth: 3 }}
            elementsSelectable
            minZoom={0.7}
            maxZoom={1.4}
            fitView
            fitViewOptions={{ padding: 0.22, maxZoom: 1.1 }}
            proOptions={{ hideAttribution: true }}
          ><Background gap={20} size={1} /><Panel position="top-right"><span className={styles.nodeLimit}>{t("CourseTemplateEditorPage.nodeLimitLabel", { count: value.length })}</span></Panel></ReactFlow></div>
          <aside className={styles.topologyInspector}>
            {selectedEdge ? <>
              <div className={styles.inspectorTitle}><MIcon name="link" size={18} /><div><strong>{t("CourseTemplateEditorPage.connectionRuleTitle")}</strong><small>{value.find((node) => node.id === selectedEdge.source)?.name} → {value.find((node) => node.id === selectedEdge.target)?.name}</small></div></div>
              <label>{t("CourseTemplateEditorPage.fieldDirection")}<select disabled={locked} value={selectedEdge.direction} onChange={(event) => patchEdge({ direction: event.target.value })}><option value="one_way">{t("CourseTemplateEditorPage.directionOneWay")}</option><option value="bidirectional">{t("CourseTemplateEditorPage.directionBidirectional")}</option></select></label>
              <div className={styles.inspectorSplit}>
                <label>{t("CourseTemplateEditorPage.fieldProtocol")}<select disabled={locked} value={selectedEdge.protocol} onChange={(event) => patchEdge({ protocol: event.target.value })}>{selectedEdge.protocol === "any" && <option value="any">{t("CourseTemplateEditorPage.protocolAnyLegacy")}</option>}{FIREWALL_PROTOCOLS.map((protocol) => <option key={protocol} value={protocol}>{protocol.toUpperCase()}</option>)}</select></label>
                <label>{t("CourseTemplateEditorPage.fieldPort")}<input disabled={locked || selectedEdge.protocol === "any"} type="number" min="1" max="65535" value={selectedEdge.port ?? ""} onChange={(event) => patchEdge({ port: event.target.value })} /></label>
              </div>
              {!locked && <button type="button" className={styles.inspectorDanger} onClick={() => removeEdge(selectedEdge.id)}><MIcon name="delete_outline" size={16} />{t("CourseTemplateEditorPage.deleteConnectionBtn")}</button>}
            </> : selectedNode ? <>
              <div className={styles.inspectorTitle}>
                <MIcon name="dns" size={18} />
                <div><strong>{selectedNode.sourceType === "custom" ? t("CourseTemplateEditorPage.sourceCustomSpec") : t("CourseTemplateEditorPage.sourceExistingTemplate")}</strong><small>{selectedNode.type === "lxc" ? t("CourseTemplateEditorPage.typeContainerLxc") : t("CourseTemplateEditorPage.typeVm")}</small></div>
                {!locked && <button type="button" className={styles.inspectorTitleAction} onClick={() => removeMachine(selectedNode.id)}>{t("CourseTemplateEditorPage.removeNodeBtn")}</button>}
              </div>
              <label>{t("CourseTemplateEditorPage.fieldName")}<input disabled={locked} value={selectedNode.name} onChange={(event) => patchNode(selectedNode.id, { name: event.target.value })} /></label>
              <label>{t("CourseTemplateEditorPage.fieldRole")}<input disabled={locked} value={selectedNode.role} onChange={(event) => patchNode(selectedNode.id, { role: event.target.value })} /></label>
              <div className={styles.inspectorSliders}>
                <label><span className={styles.sliderLabel}>CPU<em>{t("CourseTemplateEditorPage.cpuValue", { count: selectedNode.cpu })}</em></span><input disabled={specLocked} type="range" step="1" min={Math.min(CPU_RANGE[0], selectedNode.cpu)} max={Math.max(CPU_RANGE[1], selectedNode.cpu)} value={selectedNode.cpu} onChange={(event) => patchNode(selectedNode.id, { cpu: Number(event.target.value) })} /></label>
                <label><span className={styles.sliderLabel}>RAM<em>{t("CourseTemplateEditorPage.memoryValue", { count: selectedNode.memory })}</em></span><input disabled={specLocked} type="range" step="1" min={Math.min(MEMORY_RANGE[0], selectedNode.memory)} max={Math.max(MEMORY_RANGE[1], selectedNode.memory)} value={selectedNode.memory} onChange={(event) => patchNode(selectedNode.id, { memory: Number(event.target.value) })} /></label>
                <label><span className={styles.sliderLabel}>Disk<em>{t("CourseTemplateEditorPage.diskValue", { count: selectedNode.disk })}</em></span><input disabled={specLocked} type="range" step="1" min={Math.min(diskRange[0], selectedNode.disk)} max={Math.max(diskRange[1], selectedNode.disk)} value={selectedNode.disk} onChange={(event) => patchNode(selectedNode.id, { disk: Number(event.target.value) })} /></label>
              </div>
              <div className={styles.publicationSection}>
                <div className={styles.publicationHead}>
                  <span>{t("CourseTemplateEditorPage.publicAccessLabel")}</span>
                  {!locked && <button type="button" className={styles.publicationAddBtn} onClick={() => setPublicationDraft(newPublication(selectedNode))}><MIcon name="add" size={14} />{t("CourseTemplateEditorPage.addPublicationBtn")}</button>}
                </div>
                {nodePublications.length === 0
                  ? <p className={styles.inspectorHint}>{t("CourseTemplateEditorPage.noPublicationHint")}</p>
                  : <ul className={styles.publicationList}>{nodePublications.map((publication) => <li key={publication.id}>
                      <button type="button" className={styles.publicationItem} disabled={locked} onClick={() => setPublicationDraft({ ...publication })}>
                        <strong>{t(publication.mode === "domain" ? "CourseTemplateEditorPage.publicationSummaryDomain" : "CourseTemplateEditorPage.publicationSummaryFirewall", { port: publication.port })}</strong>
                        <small>{publication.mode === "domain" ? previewDomain(publication) : t("CourseTemplateEditorPage.publicationInternalOnly")}</small>
                      </button>
                      {!locked && <button type="button" className={styles.iconBtnDanger} aria-label={t("CourseTemplateEditorPage.removePublicationBtn")} onClick={() => removePublication(publication.id)}><MIcon name="close" size={15} /></button>}
                    </li>)}</ul>}
              </div>
            </> : null}
          </aside>
        </div>
      </> : <EmptyState icon="dns" title={t("CourseTemplateEditorPage.emptyNodesTitle")} />}
      {publicationPresence.open && <PublicationDialog
        draft={publicationPresence.item}
        closing={publicationPresence.closing}
        zones={zones}
        siblings={publications}
        onChange={(patch) => setPublicationDraft((current) => ({ ...current, ...patch }))}
        onSave={savePublication}
        onClose={() => setPublicationDraft(null)}
      />}
      {actions && <div className={styles.actionFooter}>{actions}</div>}
  </section>;
}

/** 只允許站內相對路徑（以單一 "/" 開頭、不含 scheme 或 "//"），其餘視為無效。 */
function sanitizeReturnTo(value) {
  if (typeof value !== "string" || !value) return null;
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return null;
  try {
    const url = new URL(value, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    return `${url.pathname}${url.search}${url.hash}`;
  } catch {
    return null;
  }
}

export default function CourseTemplateEditorPage() {
  const { t } = useTranslation("teaching");
  const confirm = useConfirm();
  const toast = useToast();
  const { templateId } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const requestedTab = params.get("tab") ?? "basic";
  // 只接受站內相對路徑（單一斜線開頭）：`//evil.com` 或含 scheme 的值會被
  // react-router 交給 window.location.assign，形成 open redirect
  const returnTo = sanitizeReturnTo(params.get("returnTo"));
  const tab = TABS.some(([key]) => key === requestedTab) ? requestedTab : "basic";
  const [template, setTemplate] = useState(() => makeEmptyTemplate());
  const [pveTemplates, setPveTemplates] = useState([]);
  const [vmImages, setVmImages] = useState([]);
  const [lxcImages, setLxcImages] = useState([]);
  const [zones, setZones] = useState([]);
  const [sourceNotice, setSourceNotice] = useState("");
  const [loading, setLoading] = useState(Boolean(templateId));
  const [saving, setSaving] = useState(false);
  const [saveState, setSaveState] = useState("idle");
  const [saveError, setSaveError] = useState("");
  const autosaveRef = useRef(null);
  const templateRef = useRef(template);
  const publishingRef = useRef(false);
  /* 返回時先播離場動畫再導航，比照「我的申請」的表單開合 */
  const [closing, setClosing] = useState(false);
  /* 儲存檢查：未填欄位反紅＋聚焦 */
  const [invalidField, setInvalidField] = useState("");
  const nameRef = useRef(null);
  /* 已發布的環境不能自動儲存，提供方式這組欄位改完要按按鈕才送出 */
  const [offeringDirty, setOfferingDirty] = useState(false);
  async function leaveTo(path) {
    if (publishingRef.current) return;
    await autosaveRef.current?.flush();
    setClosing(true);
    setTimeout(() => navigate(path, { state: { returning: true } }), 180);
  }
  const isNew = !templateId;
  const locked = template.status !== "draft" || saving;
  const duplicatedHostname = (() => {
    const seen = new Set();
    for (const item of template.publications ?? []) {
      if (item.mode !== "domain") continue;
      if (seen.has(item.hostnamePrefix)) return item.hostnamePrefix;
      seen.add(item.hostnamePrefix);
    }
    return "";
  })();
  const invalidTopology = (template.edges ?? []).some((edge) => (
    edge.protocol !== "any"
    && (!Number.isInteger(Number(edge.port)) || Number(edge.port) < 1 || Number(edge.port) > 65535)
  ));
  /* 不小心跳離（點側欄、重新整理）時保留未儲存的編輯：
     每次編輯寫入 sessionStorage，進頁還原，成功儲存／發布才清除 */
  const draftKey = `courseTemplateEditorDraft:${AuthStorage.getSnapshot().sessionId ?? "anonymous"}:${templateId ?? "new"}`;
  function readDraft() {
    try { const raw = sessionStorage.getItem(draftKey); return raw ? JSON.parse(raw) : null; }
    catch { return null; }
  }
  function clearDraft() {
    try { sessionStorage.removeItem(draftKey); } catch { /* sessionStorage 不可用就不保留 */ }
  }

  useEffect(() => {
    const draft = readDraft();
    let active = true;
    let autosave = null;
    function initialize(value, restored) {
      if (value.id === "new" && !value.draftRequestId) value = { ...value, draftRequestId: crypto.randomUUID() };
      templateRef.current = value;
      setTemplate(value);
      setSaveState(value.id === "new" ? "idle" : "saved");
      autosave = createEnvironmentAutosave({
        id: value.id === "new" ? null : value.id,
        save: (id, snapshot) => CourseEnvironmentsService.saveDraft(id, snapshot),
        onState: (state, error) => {
          if (active) { setSaveState(state); setSaveError(error?.message ?? ""); }
        },
        onSaved: (saved, snapshot) => {
          // A response must never replace edits typed while the request ran.
          const next = { ...(active ? templateRef.current : snapshot), id: saved.id, versionId: saved.versionId, version: saved.version, updatedAt: saved.updatedAt };
          if (active || !autosaveRef.current) {
            try { sessionStorage.setItem(draftKey, JSON.stringify(next)); } catch { /* Server copy is saved. */ }
          }
          if (active) { templateRef.current = next; setTemplate(next); }
        },
      });
      autosaveRef.current = autosave;
      if (restored && value.status === "draft") {
        try { sessionStorage.setItem(draftKey, JSON.stringify(value)); } catch { /* Server autosave remains available. */ }
        autosave.schedule(value);
      }
      setLoading(false);
    }
    const existingId = templateId || (draft?.id !== "new" && draft?.id);
    if (!existingId) {
      initialize(draft ?? makeEmptyTemplate(), Boolean(draft));
    } else {
      setLoading(true);
      CourseEnvironmentsService.get(existingId)
      .then((result) => {
        if (!active) return;
        /* 只有草稿可編輯；已發布版本忽略殘留草稿 */
        if (draft && result.status === "draft") {
          initialize({ ...draft, id: result.id }, true);
          toast.success(t("CourseTemplateEditorPage.draftRestoredMsg"));
        } else {
          initialize(result, false);
        }
      })
      .catch((reason) => active && toast.error(reason?.message ?? t("CourseTemplateEditorPage.loadTemplateFailed")))
      .finally(() => active && setLoading(false));
    }
    return () => {
      active = false;
      if (autosaveRef.current === autosave) autosaveRef.current = null;
      if (autosave) void autosave.flush().then((saved) => {
        if (saved && !autosaveRef.current) clearDraft();
        autosave.dispose();
      });
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [templateId]);
  useEffect(() => {
    let active = true;
    TemplatesService.list()
      .then((result) => {
        if (!active) return;
        const rows = result?.data ?? result ?? [];
        const ready = rows.filter((item) => item.status === "ready");
        setPveTemplates(ready);
        if (ready.length) setSourceNotice("");
        else if (rows.some((item) => item.status === "creating" || item.status === "updating")) {
          setSourceNotice(t("CourseTemplateEditorPage.templatesProcessingNotice"));
        } else if (rows.some((item) => item.status === "failed")) {
          setSourceNotice(t("CourseTemplateEditorPage.templatesFailedNotice"));
        } else {
          setSourceNotice("");
        }
      })
      .catch((reason) => {
        if (active) toast.error(reason?.message ?? t("CourseTemplateEditorPage.loadTemplatesFailedFallback"));
      });
    return () => { active = false; };
  }, [toast, t]);
  useEffect(() => {
    let active = true;
    // 反向代理沒設定好時回空陣列，發布方式只留「僅開防火牆」
    apiGet("/api/v1/reverse-proxy/setup-context")
      .then((context) => { if (active) setZones(context?.enabled ? (context.zones ?? []) : []); })
      .catch(() => { if (active) setZones([]); });
    return () => { active = false; };
  }, []);
  // 兩份清單分開載：VM 與 LXC 各自可能失敗，別讓其中一支把另一支也拖成空的
  useEffect(() => {
    let active = true;
    apiGet("/api/v1/vm/templates")
      .then((vms) => {
        if (!active) return;
        setVmImages((vms ?? []).map((item) => ({ value: String(item.vmid), label: t("CourseTemplateEditorPage.vmImageLabel", { name: item.name, vmid: item.vmid, node: item.node }), cores: item.cores, memoryMb: item.memory_mb, diskGb: item.disk_gb })));
      })
      .catch((reason) => {
        if (active) setSourceNotice(reason?.message ?? t("CourseTemplateEditorPage.loadImagesFailed"));
      });
    apiGet("/api/v1/lxc/templates")
      .then((lxcs) => {
        if (!active) return;
        setLxcImages((lxcs ?? []).map((item) => ({ value: item.volid, label: item.volid.split("/").pop() ?? item.volid })));
      })
      .catch((reason) => {
        if (active) toast.error(reason?.message ?? t("CourseTemplateEditorPage.loadImagesFailed"));
      });
    return () => { active = false; };
  }, [toast, t]);
  function update(patch) {
    if (locked || publishingRef.current) return;
    const next = { ...templateRef.current, ...patch };
    templateRef.current = next;
    setTemplate(next);
    try { sessionStorage.setItem(draftKey, JSON.stringify(next)); } catch { /* Autosave still persists to the server. */ }
    autosaveRef.current?.schedule(next);
  }
  /* 套用方式、開放對象、開放班級與同時上限存在環境身分上，不在版本裡：發布凍結
     的是機器設定，不是「誰拿得到」。草稿照原本的自動儲存走；已發布的先改在本地，
     按「儲存開放設定」才送出，免得每動一下就打一次 API。 */
  function updateOffering(patch) {
    if (publishingRef.current || saving) return;
    if (template.status === "draft") { update(patch); return; }
    const next = { ...templateRef.current, ...patch };
    templateRef.current = next;
    setTemplate(next);
    setOfferingDirty(true);
  }

  async function saveOffering() {
    if (publishingRef.current) return;
    const next = templateRef.current;
    setSaving(true);
    try {
      const saved = await CourseEnvironmentsService.setVisibility(next.id, next);
      templateRef.current = saved;
      setTemplate(saved);
      setOfferingDirty(false);
      toast.success(t("CourseTemplateEditorPage.offeringSaved"));
    } catch (reason) {
      toast.error(reason?.message ?? t("CourseTemplateEditorPage.offeringSaveFailed"));
    } finally { setSaving(false); }
  }

  function changeTab(nextTab) { setParams(returnTo ? { tab: nextTab, returnTo } : { tab: nextTab }); }

  /* 儲存前檢查：欄位類問題直接反紅＋聚焦（比照 ClassSetupPage），
     機器配置類問題切到該分頁並 toast 說明 */
  function validateBeforeSave() {
    if (!template.name.trim()) {
      setInvalidField("name");
      changeTab("basic");
      setTimeout(() => focusInvalidField(nameRef.current), 60);
      return false;
    }
    if (template.nodes.length === 0) { changeTab("machines"); toast.error(t("CourseTemplateEditorPage.needAtLeastOneMachineReason")); return false; }
    if (template.nodes.length > 3) { changeTab("machines"); toast.error(t("CourseTemplateEditorPage.maxThreeMachinesReason")); return false; }
    if (invalidTopology) { changeTab("machines"); toast.error(t("CourseTemplateEditorPage.fixPortReason")); return false; }
    if (duplicatedHostname) { changeTab("machines"); toast.error(t("CourseTemplateEditorPage.duplicateHostnameReason", { hostname: duplicatedHostname })); return false; }
    return true;
  }

  async function publish() {
    if (publishingRef.current || !autosaveRef.current || !validateBeforeSave()) return;
    publishingRef.current = true;
    setSaving(true);
    try {
      const ok = await confirm({
        title: t("CourseTemplateEditorPage.publishConfirmTitle"),
        message: t("CourseTemplateEditorPage.publishConfirmMessage"),
        confirmText: t("CourseTemplateEditorPage.publishLabel"),
      });
      if (!ok) return;
      autosaveRef.current.schedule(templateRef.current);
      if (!(await autosaveRef.current.flush())) return;
      const published = await CourseEnvironmentsService.publish(autosaveRef.current.getId());
      clearDraft();
      templateRef.current = published;
      setTemplate(published);
      const destination = template.usageScope === "quick_practice"
        ? t("CourseTemplateEditorPage.destQuickPractice")
        : template.usageScope === "both"
          ? t("CourseTemplateEditorPage.destBoth")
          : t("CourseTemplateEditorPage.destClassManagement");
      toast.success(t("CourseTemplateEditorPage.publishedMsg", { destination }));
      if (returnTo) navigate(returnTo, { state: { createdTemplateId: published.id } });
      else if (isNew) navigate(`/course-template-management/${published.id}`, { replace: true });
    } catch (reason) { toast.error(reason?.message ?? t("CourseTemplateEditorPage.publishFailed")); }
    finally { publishingRef.current = false; setSaving(false); }
  }
  async function newVersion() {
    setSaving(true);
    try {
      const version = await CourseEnvironmentsService.createVersion(template.id);
      templateRef.current = version;
      setTemplate(version);
      setSaveState("saved");
    }
    catch (reason) { toast.error(reason?.message ?? t("CourseTemplateEditorPage.newVersionFailed")); }
    finally { setSaving(false); }
  }
  if (loading) return <LoadingState fullPage text={t("CourseTemplateEditorPage.loadingTemplateText")} />;
  return <div className={`${styles.page} ${tab === "machines" ? styles.editorPageLocked : ""} ${closing ? styles.animSlideOutRight : styles.animSlideInRight}`}>
    <PageHeader title={isNew ? t("CourseTemplateEditorPage.createTemplateTitle") : template.name} subtitle={isNew ? undefined : `v${template.version} · ${template.updatedAt}`}><div className={styles.pageActions}>{template.status !== "draft" && <button type="button" className={styles.btnPrimary} disabled={saving} onClick={newVersion}><MIcon name="content_copy" size={16} />{t("CourseTemplateEditorPage.createNewVersionBtn")}</button>}<button type="button" className={`${styles.btnSecondary} ${styles.backBtn}`} onClick={() => leaveTo(returnTo ?? "/course-template-management")}><MIcon name="arrow_back" size={18} />{t("CourseTemplateEditorPage.backBtn")}</button></div></PageHeader>
    {template.status === "draft" && <p className={styles.persistentFeedback} role="status" aria-live="polite">
      <MIcon name={saveState === "error" ? "cloud_off" : saveState === "saved" ? "cloud_done" : "cloud_sync"} size={17} />
      <span>{t(`CourseTemplateEditorPage.autosave.${saveState}`)}{saveError && ` ${saveError}`}</span>
      {saveState === "error" && <button type="button" className={styles.btnSecondary} disabled={saving} onClick={() => autosaveRef.current?.flush()}>{t("CourseTemplateEditorPage.retryAutosave")}</button>}
    </p>}
    {returnTo && <p className={styles.persistentFeedback}><MIcon name="bookmark_added" size={17} /><span><strong>{t("CourseTemplateEditorPage.classDraftSavedTitle")}</strong>{t("CourseTemplateEditorPage.classDraftSavedDesc")}</span></p>}
    <nav className={styles.envStepper}>
        {TABS.map(([key, labelKey], index) => {
          const activeIndex = TABS.findIndex(([k]) => k === tab);
          const done = index < activeIndex;
          const isActive = key === tab;
          return (
            <button
              type="button"
              key={key}
              className={`${styles.envStep} ${isActive ? styles.envStepActive : ""} ${done ? styles.envStepDone : ""}`}
              aria-current={isActive ? "step" : undefined}
              onClick={() => changeTab(key)}
            >
              <span className={styles.envStepText}>
                <strong>
                  <span className={styles.envStepNum}>{done ? <MIcon name="check" size={14} /> : String(index + 1).padStart(2, "0")}</span>
                  {t(labelKey)}
                </strong>
              </span>
            </button>
          );
        })}
    </nav>
    {tab === "basic" && <section className={styles.card}><div className={styles.formGrid}><label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldEnvName")}</span><input ref={nameRef} className={invalidField === "name" ? styles.fieldInvalid : undefined} aria-invalid={invalidField === "name"} aria-errormessage={invalidField === "name" ? "env-name-error" : undefined} disabled={locked} value={template.name} onChange={(event) => { update({ name: event.target.value }); if (invalidField === "name") setInvalidField(""); }} placeholder={t("CourseTemplateEditorPage.envNamePlaceholder")} />{invalidField === "name" && <em id="env-name-error" className={styles.fieldError}>{t("CourseTemplateEditorPage.nameRequiredError")}</em>}</label><label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldUsageScope")}</span><select disabled={saving} value={template.usageScope ?? "course"} onChange={(event) => updateOffering({ usageScope: event.target.value })}><option value="course">{t("CourseTemplateEditorPage.usageScopeCourseOnly")}</option><option value="quick_practice">{t("CourseTemplateEditorPage.usageScopeQuickPracticeOnly")}</option><option value="both">{t("CourseTemplateEditorPage.usageScopeBoth")}</option></select></label><label className={`${styles.field} ${styles.fieldFull}`}><span>{t("CourseTemplateEditorPage.fieldEnvDescription")}</span><textarea disabled={locked} rows={3} value={template.description ?? ""} onChange={(event) => update({ description: event.target.value })} /></label></div>{template.status !== "draft" && <p className={styles.inspectorHint}>{t("CourseTemplateEditorPage.offeringEditableHint")}</p>}<div className={styles.actionFooter}>{template.status !== "draft" && <button type="button" className={styles.btnPrimary} disabled={saving || !offeringDirty} onClick={saveOffering}><MIcon name="save" size={16} />{t("CourseTemplateEditorPage.saveOfferingBtn")}</button>}<button type="button" className={template.status === "draft" ? styles.btnPrimary : styles.btnSecondary} onClick={() => changeTab("machines")}>{t("CourseTemplateEditorPage.viewMachineConfigBtn")}<MIcon name="arrow_forward" size={16} /></button></div></section>}
    {tab === "machines" && <MachineEditor value={template.nodes} edges={template.edges ?? []} publications={template.publications ?? []} onChange={(nodes) => update({ nodes })} onEdgesChange={(edges) => update({ edges })} onPublicationsChange={(publications) => update({ publications })} pveTemplates={pveTemplates} vmImages={vmImages} lxcImages={lxcImages} zones={zones} sourceNotice={sourceNotice} locked={locked} actions={template.status === "draft" && <button type="button" className={styles.btnPrimary} disabled={saving || closing} onClick={publish}><MIcon name="publish" size={16} />{saving ? t("CourseTemplateEditorPage.publishing") : t("CourseTemplateEditorPage.publishLabel")}</button>} />}
  </div>;
}

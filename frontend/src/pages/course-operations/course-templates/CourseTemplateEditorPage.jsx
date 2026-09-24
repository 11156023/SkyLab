import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  Panel,
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
import { joinList } from "../../../utils/joinList";
import { uploadSequentially } from "../../../utils/uploadSequentially";
import { useToast } from "../../../hooks/useToast";
import useDialogPresence from "../../../hooks/useDialogPresence";
import EmptyState from "../../../components/EmptyState/EmptyState";
import FileDropzone from "../../../components/FileDropzone/FileDropzone";
import { TemplatesService } from "../../../services/templates";
import ConnectionEdge from "../../network/firewall/edges/ConnectionEdge";
import GatewayNode from "../../network/firewall/nodes/GatewayNode";
import ConnectionDetailPanel from "../../network/firewall/ConnectionDetailPanel";
import fwStyles from "../../network/firewall/FirewallPage.module.scss";
import { ThemeContext } from "../../../contexts/ThemeContext";
import NodeHandles from "../../network/firewall/nodes/NodeHandles";
import { describePort, routeEdges } from "../../network/firewall/utils/buildFlow";
import ConnectionDialog, { INTERNET_KEY } from "../../../components/ConnectionDialog/ConnectionDialog";
import { INTENT } from "../../../components/ConnectionDialog/intents";
import { previewTemplateHostname } from "../../../components/ConnectionDialog/connectionPayload";
import { frozenTopologyLayout, publicationLabel } from "../courseTopology";
import styles from "../CourseOperations.module.scss";
import PageHeader from "../../../components/PageHeader/PageHeader";
import i18n from "../../../i18n";
import { AuthStorage } from "../../../services/auth";
import { createEnvironmentAutosave } from "./environmentAutosave";
import useAiScreen from "../../../hooks/useAiScreen";

const TABS = [
  ["basic", "CourseTemplateEditorPage.tabBasicLabel"],
  ["machines", "CourseTemplateEditorPage.tabMachinesLabel"],
];

function makeEmptyTemplate() {
  return { id: "new", name: "", description: "", usageScope: "course", status: "draft", classes: 0, updatedAt: i18n.t("CourseTemplateEditorPage.notSavedYet", { ns: "teaching" }), nodes: [], edges: [], publications: [], peerPolicy: "explicit" };
}

/* 課程環境只有「開放服務」與「互通」：上網預設全開、自己寫規則沒有可樣板化的語意 */
const COURSE_INTENTS = [INTENT.PUBLISH, INTENT.PEER];
/* 網際網路節點沒有存到版本裡，位置放元件內；預設在三台機器的右側 */
const INTERNET_POSITION = { x: 60 + 3 * 300, y: 120 };

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

/** LXC 映像是 tarball，檔名直接當機器名稱又臭又長，去掉封裝副檔名。 */
function stripImageExtension(name) {
  return String(name).replace(/\.tar(\.(gz|xz|zst|bz2|lzo))?$/i, "");
}

/* 節點長得跟防火牆拓撲的 VMNode 一樣：狀態點、名稱、副標、型別圖示、右上角對外數。
   模板機器還沒開出來，狀態點用中性色；副標放規格取代 IP。 */
function TopologyMachineNode({ data, selected }) {
  const node = data.node;
  const exposed = data.exposedCount ?? 0;
  const spec = `${node.cpu} CPU · ${node.memory} GB · ${node.disk} GB`;
  return <div className={`${fwStyles.vmNode} ${styles.courseMachineNode} ${data.frozen ? styles.courseMachineNodeFrozen : ""} ${selected ? fwStyles.nodeSelected : ""}`}>
    <NodeHandles dragStartSide="right" />
    <div className={fwStyles.vmStatus} style={{ background: "var(--color-status-neutral)" }} />
    <div className={fwStyles.vmInfo}>
      <span className={fwStyles.vmName} title={node.name}>{node.name}</span>
      {/* 副標只寫規格；角色與完整規格放滑過提示（規格太長被截斷時也看得到） */}
      <span className={`${fwStyles.vmMeta} ${styles.courseMachineMeta}`} title={node.role ? `${node.role} · ${spec}` : spec}>{spec}</span>
    </div>
    <MIcon name={node.type === "lxc" ? "terminal" : "dns"} size={15} />
    {exposed > 0 && <span className={fwStyles.exposedBadge}><MIcon name="public" size={11} />{exposed}</span>}
  </div>;
}

const TOPOLOGY_NODE_TYPES = { courseMachine: TopologyMachineNode, gateway: GatewayNode };
const TOPOLOGY_EDGE_TYPES = { connection: ConnectionEdge };





function formatFileSize(bytes) {
  const size = Number(bytes ?? 0);
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

/* locked：暫時不能改（含草稿儲存中）；frozen：已發布、機器設定凍結，畫布當唯讀圖顯示 */
function MachineEditor({ value, edges, publications, onChange, onEdgesChange, onPublicationsChange, pveTemplates, vmImages, lxcImages, zones, sourceNotice, locked = false, frozen = false, actions = null }) {
  const { t } = useTranslation("teaching");
  const [sourceMode, setSourceMode] = useState("template");
  const [sourceId, setSourceId] = useState("");
  const [customType, setCustomType] = useState("qemu");
  const [selectedNodeId, setSelectedNodeId] = useState("");
  const [selectedEdgeId, setSelectedEdgeId] = useState("");
  const [selectedPublicationId, setSelectedPublicationId] = useState("");
  /* 上網線（機器 → 網際網路）是預設策略，不是規則：每台都有一條，全畫出來會淹掉
     互通與對外服務，所以跟防火牆頁一樣預設藏起來，要看再開 */
  const [selectedOutboundKey, setSelectedOutboundKey] = useState("");
  const [showInternet, setShowInternet] = useState(false);
  const [flowNodes, setFlowNodes, onFlowNodesChange] = useNodesState([]);
  const [internetPosition, setInternetPosition] = useState(INTERNET_POSITION);
  const [topologyNotice, setTopologyNotice] = useState("");
  /* 與防火牆頁同一個連線對話框：拉線帶入兩端，或按「新增連線」從選意圖開始 */
  const [dialog, setDialog] = useState(null); // { initialSource, initialTarget, service, publicationId }
  const dialogPresence = useDialogPresence(dialog);
  /* 畫布配色跟防火牆頁一樣跟著主題；沒有 provider（測試）就當淺色 */
  const theme = useContext(ThemeContext)?.theme ?? "light";
  const sourceOptions = sourceMode === "template" ? pveTemplates : (customType === "lxc" ? lxcImages : vmImages);
  const atLimit = value.length >= 3;
  /* 對話框的機器清單：模板沒有 vmid，只認 node key */
  const dialogNodes = useMemo(() => value.map((node) => ({ key: String(node.id), vmid: null, name: node.name })), [value]);

  function selectNode(nodeId) { setSelectedNodeId(nodeId); setSelectedEdgeId(""); setSelectedPublicationId(""); setSelectedOutboundKey(""); }
  function selectEdge(edgeId) { setSelectedEdgeId(edgeId); setSelectedNodeId(""); setSelectedPublicationId(""); setSelectedOutboundKey(""); }
  function selectPublication(publicationId) { setSelectedPublicationId(publicationId); setSelectedNodeId(""); setSelectedEdgeId(""); setSelectedOutboundKey(""); }
  function selectOutbound(nodeKey) { setSelectedOutboundKey(nodeKey); setSelectedNodeId(""); setSelectedEdgeId(""); setSelectedPublicationId(""); }
  function toggleInternet() {
    const next = !showInternet;
    setShowInternet(next);
    if (!next && selectedOutboundKey) selectNode("");
  }

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
    selectNode(nodeId);
    setSourceId("");
  }

  function removeMachine(nodeId) {
    onChange(value.filter((item) => item.id !== nodeId));
    onEdgesChange(edges.filter((edge) => edge.source !== nodeId && edge.target !== nodeId));
    onPublicationsChange(publications.filter((item) => item.nodeKey !== nodeId));
    selectNode("");
  }

  function removePublication(publicationId) {
    onPublicationsChange(publications.filter((item) => item.id !== publicationId));
    if (selectedPublicationId === publicationId) selectNode("");
  }


  /** 拉線：機器 → 機器是互通；碰到網際網路（不管哪個方向）都是開放服務。 */
  function connect(connection) {
    if (locked || connection.source === connection.target) return;
    const touchesInternet = connection.source === INTERNET_KEY || connection.target === INTERNET_KEY;
    const machine = connection.source === INTERNET_KEY ? connection.target : connection.source;
    setDialog(touchesInternet
      ? { initialSource: INTERNET_KEY, initialTarget: String(machine) }
      : { initialSource: String(connection.source), initialTarget: String(connection.target) });
  }

  /** 對話框送出：不打 API，收進規格陣列；錯誤用對話框自己的訊息列顯示。 */
  function handleDialogSubmit(request) {
    if (request.kind === "inbound") {
      const nodeKey = String(request.vmKey);
      const editingId = dialog?.publicationId ?? null;
      const others = publications.filter((item) => item.id !== editingId);
      const items = request.publish.map((item, index) => ({
        id: editingId ?? `publication-${Date.now()}-${index}`,
        nodeKey,
        mode: item.mode,
        port: item.port,
        protocol: item.protocol,
        hostnamePrefix: item.hostname_prefix ?? "",
        zoneId: item.zone_id ?? "",
        enableHttps: item.enable_https !== false,
      }));
      const duplicatePort = items.find((item) => others.some((other) => other.nodeKey === item.nodeKey && Number(other.port) === Number(item.port) && other.protocol === item.protocol));
      if (duplicatePort) return { ok: false, error: { text: t("CourseTemplateEditorPage.duplicatePublicationPort", { port: duplicatePort.port }) } };
      /* 一個網址只能指向一個 port：同一份環境裡的樣板不能重複 */
      const duplicateHostname = items.find((item) => item.mode === "domain" && others.some((other) => other.mode === "domain" && other.hostnamePrefix === item.hostnamePrefix));
      if (duplicateHostname) return { ok: false, error: { text: t("CourseTemplateEditorPage.duplicateHostnameHint") } };
      onPublicationsChange([...others, ...items]);
      selectPublication(items[0].id);
      return { ok: true, result: { kind: "publish" } };
    }
    if (request.kind === "edge") {
      /* 課程連線一條一個 port；沒有 port 的協定（icmp）後端不收 */
      if (request.ports.some((port) => !port.port)) {
        return { ok: false, error: { text: t("CourseTemplateEditorPage.portlessUnsupported") } };
      }
      const added = request.ports.map((port, index) => ({
        id: `edge-${Date.now()}-${index}`,
        source: String(request.sourceKey),
        target: String(request.targetKey),
        direction: request.direction,
        protocol: port.protocol,
        port: port.port,
      }));
      const overlapping = added.find((edge, index) => overlapsExistingEdge(edge, [...edges, ...added.slice(0, index)]));
      if (overlapping) return { ok: false, error: { text: t("CourseTemplateEditorPage.overlappingEdgeNotice") } };
      setTopologyNotice("");
      onEdgesChange([...edges, ...added]);
      selectEdge(added[0].id);
      return { ok: true, result: { kind: "connection" } };
    }
    return { ok: false, error: { text: t("CourseTemplateEditorPage.dialogUnsupported") } };
  }

  function patchNode(nodeId, patch) {
    onChange(value.map((item) => item.id === nodeId ? { ...item, ...patch } : item));
  }


  function removeEdge(edgeId) {
    onEdgesChange(edges.filter((edge) => edge.id !== edgeId));
    selectNode("");
  }

  const selectedEdge = edges.find((edge) => edge.id === selectedEdgeId);
  const selectedPublication = publications.find((item) => item.id === selectedPublicationId);
  const selectedNode = value.find((node) => node.id === selectedNodeId) ?? value[0];
  const nameOf = (key) => (key === null || key === undefined || key === INTERNET_KEY
    ? t("GatewayNode.internet", { ns: "network" })
    : (value.find((node) => String(node.id) === String(key))?.name ?? String(key)));
  /* 點線就用防火牆頁同一個細節面板：講清楚開了什麼、往哪個方向，刪除收在裡面。
     模板上的線沒有東西可「編輯」——要改就刪掉重拉，跟防火牆頁一樣。 */
  const detail = useMemo(() => {
    if (selectedOutboundKey) {
      return {
        id: `outbound-${selectedOutboundKey}`,
        edge: { source_vmid: String(selectedOutboundKey), target_vmid: null, direction: "one_way", ports: [] },
        remove: null,
      };
    }
    if (selectedEdge) {
      return {
        id: `edge-${selectedEdge.id}`,
        edge: {
          source_vmid: String(selectedEdge.source),
          target_vmid: String(selectedEdge.target),
          direction: selectedEdge.direction,
          ports: [{ port: selectedEdge.protocol === "any" ? 0 : Number(selectedEdge.port), protocol: selectedEdge.protocol }],
        },
        remove: () => removeEdge(selectedEdge.id),
      };
    }
    if (selectedPublication) {
      const zone = zones.find((item) => item.id === selectedPublication.zoneId);
      return {
        id: `publication-${selectedPublication.id}`,
        edge: {
          source_vmid: null,
          target_vmid: String(selectedPublication.nodeKey),
          direction: "one_way",
          ports: [selectedPublication.mode === "domain"
            ? { port: selectedPublication.port, protocol: "tcp", mode: "domain", domain: previewTemplateHostname(selectedPublication.hostnamePrefix, zone?.name) }
            : { port: selectedPublication.port, protocol: selectedPublication.protocol, mode: "port_forward" }],
        },
        remove: () => removePublication(selectedPublication.id),
      };
    }
    return null;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedEdge, selectedPublication, selectedOutboundKey, zones]);
  const detailPanel = useDialogPresence(detail, 220);
  // 規格只在草稿可調：已發布版本不可變（班級釘住版本，要改規格得開新版本）。
  const specLocked = locked;
  // 規格基準：範本來源以範本自身規格為錨，自訂 VM 以來源映像為錨。
  const sourceTemplate = selectedNode?.sourceType === "template"
    ? pveTemplates.find((item) => String(item.id) === String(selectedNode.sourceTemplateId))
    : null;
  const customVmImage = selectedNode?.sourceType === "custom" && selectedNode?.type !== "lxc"
    ? vmImages.find((item) => item.value === String(selectedNode.customImageRef))
    : null;
  // CPU/RAM 上下皆可調；基準值高於預設上限時把上限撐開，以免拉不回原規格。
  const baseCpu = Number(sourceTemplate?.default_cores) || 0;
  const baseMemoryGb = sourceTemplate?.default_memory
    ? Math.max(1, Math.round(Number(sourceTemplate.default_memory) / 1024))
    : 0;
  const cpuRange = [CPU_RANGE[0], Math.max(CPU_RANGE[1], baseCpu)];
  const memoryRange = [MEMORY_RANGE[0], Math.max(MEMORY_RANGE[1], baseMemoryGb)];
  // 磁碟只能往上：克隆機天生就是來源大小，PVE resize 不支援縮小。
  const isLxcNode = selectedNode?.type === "lxc";
  const diskFloor = Math.max(
    isLxcNode ? LXC_DISK_RANGE[0] : VM_DISK_RANGE[0],
    Number(sourceTemplate?.default_disk) || Number(customVmImage?.diskGb) || 0,
  );
  const diskCeiling = isLxcNode ? LXC_DISK_RANGE[1] : VM_DISK_RANGE[1];
  const diskRange = [diskFloor, Math.max(diskCeiling, diskFloor)];


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
      const exposure = new Map();
      for (const publication of publications) {
        exposure.set(publication.nodeKey, (exposure.get(publication.nodeKey) ?? 0) + 1);
      }
      const machines = value.map((node, index) => {
        const saved = {
          x: Number(node.positionX ?? (60 + index * 300)),
          y: Number(node.positionY ?? (120 + (index % 2) * 45)),
        };
        return {
          id: String(node.id),
          type: "courseMachine",
          // 已發布的版本不能拖，一律從存的座標重算（下面再推開）；可編輯時沿用畫布上的當下位置
          position: frozen ? saved : (placed.get(String(node.id)) ?? saved),
          data: { node, exposedCount: exposure.get(node.id) ?? 0, frozen },
          selected: selectedNode?.id === node.id,
        };
      });
      /* 已發布的版本拖不動：機器靠太近、連線標籤被隔壁那台蓋住時，顯示時推開
         （跟班級上課環境的唯讀圖同一套，只影響畫面，不改存的座標） */
      const layout = frozen ? frozenTopologyLayout(machines) : null;
      const shown = layout ? machines.map((node) => ({ ...node, position: layout.positions.get(node.id) })) : machines;
      /* 網際網路節點跟防火牆頁一樣常駐：拖線到它就是「開放服務給外部」，
         也讓拓撲圖一眼看得出哪幾台對外 */
      return [...shown, {
        id: INTERNET_KEY,
        type: "gateway",
        position: layout ? layout.internet : (placed.get(INTERNET_KEY) ?? internetPosition),
        data: {},
        selected: false,
      }];
    });
  }, [value, publications, selectedNode?.id, setFlowNodes, internetPosition, frozen]);

  // 位置只在放開滑鼠時回寫，一次拖曳只產生一筆變更。
  const commitNodePositions = useCallback((_event, _node, draggedNodes) => {
    const moved = new Map(draggedNodes.map((item) => [item.id, item.position]));
    const internet = moved.get(INTERNET_KEY);
    if (internet) setInternetPosition({ x: Math.round(internet.x), y: Math.round(internet.y) });
    if ([...moved.keys()].every((id) => id === INTERNET_KEY)) return;
    onChange(value.map((node) => {
      const position = moved.get(String(node.id));
      return position
        ? { ...node, positionX: Math.round(position.x), positionY: Math.round(position.y) }
        : node;
    }));
  }, [onChange, value]);

  const graphEdges = useMemo(() => {
    const peerEdges = edges.map((edge) => ({
      id: edge.id,
      source: String(edge.source),
      target: String(edge.target),
      type: "connection",
      data: {
        edge: {
          course_edge_id: edge.id,
          source_vmid: String(edge.source),
          target_vmid: String(edge.target),
          direction: edge.direction,
        },
        label: `${edge.direction === "bidirectional" ? t("CourseTemplateEditorPage.directionBidirectional") : t("CourseTemplateEditorPage.directionOneWay")} · ${describePort({ port: edge.port, protocol: edge.protocol })}`,
        showLabel: true,
        selected: edge.id === selectedEdgeId,
        onSelect: () => selectEdge(edge.id),
        onDelete: locked ? null : () => removeEdge(edge.id),
      },
    }));
    /* 對外服務畫成「網際網路 → 機器」的入站線，與防火牆拓撲同一種語言 */
    const publicationEdges = publications.map((publication) => ({
      id: `publication-edge-${publication.id}`,
      source: INTERNET_KEY,
      target: String(publication.nodeKey),
      type: "connection",
      data: {
        edge: { course_publication_id: publication.id, source_vmid: null, target_vmid: String(publication.nodeKey) },
        label: publicationLabel(t, publication, zones),
        showLabel: true,
        selected: publication.id === selectedPublicationId,
        onSelect: () => selectPublication(publication.id),
        onDelete: locked ? null : () => removePublication(publication.id),
      },
    }));
    /* 上網線：出站綠線、不限通訊埠（標籤留空由 ConnectionEdge 補「不限通訊埠」） */
    const outboundEdges = value.map((node) => ({
      id: `outbound-${node.id}`,
      source: String(node.id),
      target: INTERNET_KEY,
      type: "connection",
      hidden: !showInternet,
      data: {
        edge: { course_outbound: String(node.id), source_vmid: String(node.id), target_vmid: null, direction: "one_way" },
        label: "",
        showLabel: true,
        selected: String(node.id) === selectedOutboundKey,
        onSelect: () => selectOutbound(String(node.id)),
      },
    }));
    return routeEdges([...peerEdges, ...publicationEdges, ...outboundEdges], flowNodes);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, edges, publications, zones, locked, t, selectedEdgeId, selectedPublicationId, selectedOutboundKey, showInternet, flowNodes]);

  return <section className={`${styles.card} ${styles.templateMachineWorkspace}`}>
      {sourceNotice && <p className={styles.persistentFeedback}><MIcon name="info" size={17} />{sourceNotice}</p>}
      {topologyNotice && <p className={styles.persistentFeedback}><MIcon name="info" size={17} />{topologyNotice}</p>}
      <div className={styles.machineAddBar}>
        <label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldSourceMode")}</span><select value={sourceMode} disabled={locked || atLimit} onChange={(event) => { setSourceMode(event.target.value); setSourceId(""); }}><option value="template">{t("CourseTemplateEditorPage.sourceModeTemplateOption")}</option><option value="custom">{t("CourseTemplateEditorPage.sourceModeCustomOption")}</option></select></label>
        {sourceMode === "custom" && <label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldMachineType")}</span><select value={customType} disabled={locked || atLimit} onChange={(event) => { setCustomType(event.target.value); setSourceId(""); }}><option value="qemu">VM</option><option value="lxc">LXC</option></select></label>}
        <label className={styles.field}><span>{sourceMode === "template" ? t("CourseTemplateEditorPage.sourceExistingTemplate") : t("CourseTemplateEditorPage.fieldBaseImage")}</span><select value={sourceId} disabled={locked || atLimit} onChange={(event) => setSourceId(event.target.value)}><option value="">{locked ? t("CourseTemplateEditorPage.publishedLockedOption") : atLimit ? t("CourseTemplateEditorPage.atLimitOption") : sourceOptions.length === 0 ? t("CourseTemplateEditorPage.noSourceOption") : t("CourseTemplateEditorPage.pleaseSelectOption")}</option>{sourceMode === "template" ? sourceOptions.map((source) => <option key={source.id} value={source.id}>{source.name} · {source.resource_type ?? "VM"}</option>) : sourceOptions.map((source) => <option key={source.value} value={source.value}>{source.label}</option>)}</select></label>
        <button type="button" className={styles.btnPrimary} disabled={locked || atLimit || !sourceId} onClick={addMachine}><MIcon name={atLimit ? "check" : "add"} size={16} />{atLimit ? t("CourseTemplateEditorPage.atLimitBtn") : t("CourseTemplateEditorPage.addMachineBtn")}</button>
        <button type="button" className={styles.btnSecondary} disabled={locked || value.length === 0} onClick={() => setDialog({})}><MIcon name="add_link" size={16} />{t("CourseTemplateEditorPage.addConnectionBtn")}</button>
      </div>
      {value.length ? <>
        <div className={styles.topologyWorkspace}>
          {/* 畫布外觀比照防火牆頁：點狀底、Controls、左下圖例 */}
          <div className={`${styles.topologyCanvas} ${frozen ? styles.topologyCanvasFrozen : ""} ${fwStyles.flowWrap}`}><ReactFlow
            nodes={flowNodes}
            edges={graphEdges}
            nodeTypes={TOPOLOGY_NODE_TYPES}
            edgeTypes={TOPOLOGY_EDGE_TYPES}
            onConnect={connect}
            onNodesChange={onFlowNodesChange}
            onNodeDragStop={commitNodePositions}
            onNodeClick={(_, node) => { if (node.id !== INTERNET_KEY) selectNode(node.id); }}
            onEdgeClick={(_, edge) => edge.data?.onSelect?.()}
            onPaneClick={() => selectNode("")}
            isValidConnection={(connection) => Boolean(connection?.source && connection?.target && connection.source !== connection.target)}
            connectionRadius={36}
            nodesDraggable={!locked}
            nodesConnectable={!locked}
            elementsSelectable
            deleteKeyCode={null}
            minZoom={0.5}
            maxZoom={1.5}
            fitView
            /* 自動置中最多放大到 1 倍：機器少時才不會被放大、卡片比其他拓撲大一圈；手動仍可拉近 */
            fitViewOptions={{ padding: 0.2, maxZoom: 1 }}
            colorMode={theme}
            proOptions={{ hideAttribution: true }}
          >
            <Background variant={BackgroundVariant.Dots} gap={20} size={1} />
            <Controls />
            <Panel position="top-left">
              <div className={fwStyles.toolbar}>
                <button type="button" className={`${fwStyles.toolbarBtn} ${showInternet ? fwStyles.toolbarBtnActive : ""}`} onClick={toggleInternet}>
                  <MIcon name={showInternet ? "public" : "public_off"} size={16} />
                  {t("FirewallPage.internetLines", { ns: "network" })}
                </button>
              </div>
            </Panel>
            <Panel position="top-right"><span className={styles.nodeLimit}>{t("CourseTemplateEditorPage.nodeLimitLabel", { count: value.length })}</span></Panel>
            <Panel position="bottom-left" style={{ marginLeft: 60 }}>
              <div className={fwStyles.legend}>
                <span className={fwStyles.legendItem}><i className={`${fwStyles.legendLine} ${fwStyles.legendInbound}`} />{t("FirewallPage.legendInbound", { ns: "network" })}</span>
                {/* 上網線藏起來時圖例變淡，提醒圖上少了這種線（與防火牆頁相同） */}
                <span className={`${fwStyles.legendItem} ${showInternet ? "" : fwStyles.legendItemHidden}`}><i className={`${fwStyles.legendLine} ${fwStyles.legendOutbound}`} />{t("FirewallPage.legendOutbound", { ns: "network" })}</span>
                <span className={fwStyles.legendItem}><i className={`${fwStyles.legendLine} ${fwStyles.legendInternal}`} />{t("FirewallPage.legendInternal", { ns: "network" })}</span>
              </div>
            </Panel>
          </ReactFlow>
          {detailPanel.item && <ConnectionDetailPanel
            edge={detailPanel.item.edge}
            resolveName={nameOf}
            allowOpen={false}
            closing={detailPanel.closing}
            onClose={() => selectNode("")}
            onDelete={locked || !detailPanel.item.remove ? undefined : () => detailPanel.item.remove()}
          />}
          </div>
          <aside className={styles.topologyInspector}>
            {selectedNode ? <>
              <div className={styles.inspectorTitle}>
                <MIcon name="dns" size={18} />
                <div><strong>{selectedNode.sourceType === "custom" ? t("CourseTemplateEditorPage.sourceCustomSpec") : t("CourseTemplateEditorPage.sourceExistingTemplate")}</strong><small>{selectedNode.type === "lxc" ? t("CourseTemplateEditorPage.typeContainerLxc") : t("CourseTemplateEditorPage.typeVm")}</small></div>
                {!locked && <button type="button" className={styles.inspectorTitleAction} onClick={() => removeMachine(selectedNode.id)}>{t("CourseTemplateEditorPage.removeNodeBtn")}</button>}
              </div>
              <label>{t("CourseTemplateEditorPage.fieldName")}<input disabled={locked} value={selectedNode.name} onChange={(event) => patchNode(selectedNode.id, { name: event.target.value })} /></label>
              <label>{t("CourseTemplateEditorPage.fieldRole")}<input disabled={locked} value={selectedNode.role} onChange={(event) => patchNode(selectedNode.id, { role: event.target.value })} /></label>
              <div className={styles.inspectorSliders}>
                <label><span className={styles.sliderLabel}>CPU<em>{t("CourseTemplateEditorPage.cpuValue", { count: selectedNode.cpu })}</em></span><input disabled={specLocked} type="range" step="1" min={Math.min(cpuRange[0], selectedNode.cpu)} max={Math.max(cpuRange[1], selectedNode.cpu)} value={selectedNode.cpu} onChange={(event) => patchNode(selectedNode.id, { cpu: Number(event.target.value) })} /></label>
                <label><span className={styles.sliderLabel}>RAM<em>{t("CourseTemplateEditorPage.memoryValue", { count: selectedNode.memory })}</em></span><input disabled={specLocked} type="range" step="1" min={Math.min(memoryRange[0], selectedNode.memory)} max={Math.max(memoryRange[1], selectedNode.memory)} value={selectedNode.memory} onChange={(event) => patchNode(selectedNode.id, { memory: Number(event.target.value) })} /></label>
                <label><span className={styles.sliderLabel}>Disk<em>{t("CourseTemplateEditorPage.diskValue", { count: selectedNode.disk })}</em></span><input disabled={specLocked} type="range" step="1" min={Math.min(diskRange[0], selectedNode.disk)} max={Math.max(diskRange[1], selectedNode.disk)} value={selectedNode.disk} onChange={(event) => patchNode(selectedNode.id, { disk: Number(event.target.value) })} /></label>
              </div>
            </> : null}
          </aside>
        </div>
      </> : <EmptyState icon="dns" title={t("CourseTemplateEditorPage.emptyNodesTitle")} />}
      {dialogPresence.open && <ConnectionDialog
        key={dialogPresence.item?.publicationId ?? `${dialogPresence.item?.initialSource ?? ""}-${dialogPresence.item?.initialTarget ?? ""}`}
        templateMode
        intents={COURSE_INTENTS}
        nodes={dialogNodes}
        zones={zones}
        initialSource={dialogPresence.item?.initialSource}
        initialTarget={dialogPresence.item?.initialTarget}
        service={dialogPresence.item?.service}
        onSubmit={handleDialogSubmit}
        onDone={() => setDialog(null)}
        onClose={() => setDialog(null)}
        closing={dialogPresence.closing}
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
  /* 已發布的環境不能自動儲存，基本資訊改完要按按鈕才送出 */
  const [basicsDirty, setBasicsDirty] = useState(false);
  /* 說明文件上傳進度（{ current, total }），null = 沒在上傳 */
  const [uploadProgress, setUploadProgress] = useState(null);
  async function leaveTo(path) {
    if (publishingRef.current) return;
    await autosaveRef.current?.flush();
    setClosing(true);
    setTimeout(() => navigate(path, { state: { returning: true } }), 180);
  }
  const isNew = !templateId;
  /* 草稿第一次自動儲存後就有真正的 id，那時候就能掛文件了 */
  const hasEnvironmentId = Boolean(template.id) && template.id !== "new";
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
  useAiScreen(templateId ? "course-template-editor" : "course-template-new", {
    "coursetpl.tab": { value: tab },
    "coursetpl.status": { value: loading ? "loading" : template.status },
    "coursetpl.name": { value: template.name.slice(0, 500), error: invalidField === "name" ? t("CourseTemplateEditorPage.nameRequiredError") : null },
    "coursetpl.usage_scope": { value: template.usageScope ?? "course" },
    "coursetpl.node_count": { value: String(template.nodes?.length ?? 0) },
    "coursetpl.return_to_class": { value: String(Boolean(returnTo)) },
    "coursetpl.publish": { disabled: saving || closing || template.status !== "draft" },
  });
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
  /* 名稱、用途與套用方式存在環境身分上，不在版本裡：發布凍結的是機器設定，不是
     這組環境叫什麼、提供給誰。草稿照原本的自動儲存走；已發布的先改在本地，按
     「儲存基本資訊」才送出，免得每打一個字就打一次 API。 */
  function updateBasics(patch) {
    if (publishingRef.current || saving) return;
    if (template.status === "draft") { update(patch); return; }
    const next = { ...templateRef.current, ...patch };
    templateRef.current = next;
    setTemplate(next);
    setBasicsDirty(true);
  }

  async function saveBasics() {
    if (publishingRef.current) return;
    const next = templateRef.current;
    if (!next.name.trim()) {
      setInvalidField("name");
      setTimeout(() => focusInvalidField(nameRef.current), 60);
      return;
    }
    setSaving(true);
    try {
      const saved = await CourseEnvironmentsService.saveBasics(next.id, next);
      templateRef.current = saved;
      setTemplate(saved);
      setBasicsDirty(false);
      toast.success(t("CourseTemplateEditorPage.basicsSaved"));
    } catch (reason) {
      toast.error(reason?.message ?? t("CourseTemplateEditorPage.basicsSaveFailed"));
    } finally { setSaving(false); }
  }

  /* 文件掛在環境身分上，上傳與刪除立即生效，不跟著基本資訊那顆儲存鈕走。
     環境還沒建立（草稿沒有 id）時不給上傳，否則檔案會沒有歸屬。
     可一次選多個：後端一次收一個檔，依序上傳，傳完一個清單就先更新 */
  async function uploadFiles(files) {
    if (saving) return;
    setSaving(true);
    const { failed, lastError } = await uploadSequentially(
      files,
      async (file) => {
        const saved = await CourseEnvironmentsService.uploadFile(template.id, file);
        templateRef.current = { ...templateRef.current, files: saved.files };
        setTemplate(templateRef.current);
      },
      setUploadProgress,
    );
    setUploadProgress(null);
    setSaving(false);
    if (failed.length === 0) {
      toast.success(files.length === 1
        ? t("CourseTemplateEditorPage.fileUploaded", { name: files[0].name })
        : t("CourseTemplateEditorPage.filesUploaded", { count: files.length }));
    } else if (files.length === 1) {
      toast.error(lastError?.message ?? t("CourseTemplateEditorPage.fileUploadFailed"));
    } else {
      toast.error(t("CourseTemplateEditorPage.filesUploadPartialFail", { files: joinList(failed) }));
    }
  }

  async function removeFile(file) {
    if (saving) return;
    setSaving(true);
    try {
      const saved = await CourseEnvironmentsService.removeFile(template.id, file.id);
      templateRef.current = { ...templateRef.current, files: saved.files };
      setTemplate(templateRef.current);
    } catch (reason) {
      toast.error(reason?.message ?? t("CourseTemplateEditorPage.fileDeleteFailed"));
    } finally {
      setSaving(false);
    }
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
  if (loading) return <LoadingState fullPage text={t("CourseTemplateEditorPage.loadingTemplateText")} />;
  return <div className={`${styles.page} ${tab === "machines" ? styles.editorPageLocked : ""} ${closing ? styles.animSlideOutRight : styles.animSlideInRight}`}>
    <PageHeader title={isNew ? t("CourseTemplateEditorPage.createTemplateTitle") : (template.name.trim() || t("CourseTemplateEditorPage.unnamedEnv"))} subtitle={isNew ? undefined : `v${template.version} · ${template.updatedAt}`}><div className={styles.pageActions}><button type="button" className={`${styles.btnSecondary} ${styles.backBtn}`} onClick={() => leaveTo(returnTo ?? "/course-template-management")}><MIcon name="arrow_back" size={18} />{t("CourseTemplateEditorPage.backBtn")}</button></div></PageHeader>
    {template.status === "draft" && saveState === "error" && <p className={styles.persistentFeedback} role="alert">
      <MIcon name="cloud_off" size={17} />
      <span>{t("CourseTemplateEditorPage.autosave.error")}{saveError && ` ${saveError}`}</span>
      <button type="button" className={styles.btnSecondary} disabled={saving} onClick={() => autosaveRef.current?.flush()}>{t("CourseTemplateEditorPage.retryAutosave")}</button>
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
    {tab === "basic" && <section className={styles.card}><div className={styles.formGrid}><label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldEnvName")}</span><input ref={nameRef} className={invalidField === "name" ? styles.fieldInvalid : undefined} aria-invalid={invalidField === "name"} aria-errormessage={invalidField === "name" ? "env-name-error" : undefined} disabled={saving} value={template.name} onChange={(event) => { updateBasics({ name: event.target.value }); if (invalidField === "name") setInvalidField(""); }} placeholder={t("CourseTemplateEditorPage.envNamePlaceholder")} />{invalidField === "name" && <em id="env-name-error" className={styles.fieldError}>{t("CourseTemplateEditorPage.nameRequiredError")}</em>}</label><label className={styles.field}><span>{t("CourseTemplateEditorPage.fieldUsageScope")}</span><select disabled={saving} value={template.usageScope ?? "course"} onChange={(event) => updateBasics({ usageScope: event.target.value })}><option value="course">{t("CourseTemplateEditorPage.usageScopeCourseOnly")}</option><option value="quick_practice">{t("CourseTemplateEditorPage.usageScopeQuickPracticeOnly")}</option><option value="both">{t("CourseTemplateEditorPage.usageScopeBoth")}</option></select></label><label className={`${styles.field} ${styles.fieldFull}`}><span>{t("CourseTemplateEditorPage.fieldEnvDescription")}</span><textarea disabled={saving} rows={3} value={template.description ?? ""} onChange={(event) => updateBasics({ description: event.target.value })} /></label>

      {/* 說明文件是基本資訊的一個滿寬欄位：放在 formGrid 裡才吃得到卡片內距、跟上面的欄位對齊 */}
      <div className={`${styles.fileSection} ${styles.fieldFull}`}>
        <span className={styles.fileHeading}>{t("CourseTemplateEditorPage.fieldFiles")}</span>
        {(template.files ?? []).length > 0 && <ul className={styles.fileList}>
          {(template.files ?? []).map((file) => <li key={file.id}>
            <MIcon name="description" size={16} />
            <a href={CourseEnvironmentsService.fileUrl(template.id, file.id)} target="_blank" rel="noreferrer">{file.filename}</a>
            <small>{formatFileSize(file.sizeBytes)}</small>
            <button type="button" className={styles.fileRemove} disabled={saving} aria-label={t("CourseTemplateEditorPage.removeFileAria", { name: file.filename })} onClick={() => removeFile(file)}><MIcon name="close" size={15} /></button>
          </li>)}
        </ul>}
        {/* 環境還沒建立時整塊停用，說明直接寫在上傳區塊裡 */}
        <FileDropzone
          multiple
          disabled={saving || !hasEnvironmentId}
          uploading={uploadProgress !== null}
          progress={uploadProgress}
          hint={hasEnvironmentId ? undefined : t("CourseTemplateEditorPage.filesNeedSaveHint")}
          onFiles={uploadFiles}
        />
      </div>
      {template.status !== "draft" && <p className={`${styles.inspectorHint} ${styles.fieldFull}`}>{t("CourseTemplateEditorPage.basicsEditableHint")}</p>}
      </div>
<div className={styles.actionFooter}>{template.status !== "draft" && <button type="button" className={styles.btnPrimary} disabled={saving || !basicsDirty} onClick={saveBasics}><MIcon name="save" size={16} />{t("CourseTemplateEditorPage.saveBasicsBtn")}</button>}<button type="button" className={template.status === "draft" ? styles.btnPrimary : styles.btnSecondary} onClick={() => changeTab("machines")}>{t("CourseTemplateEditorPage.viewMachineConfigBtn")}<MIcon name="arrow_forward" size={16} /></button></div></section>}
    {tab === "machines" && <MachineEditor value={template.nodes} edges={template.edges ?? []} publications={template.publications ?? []} onChange={(nodes) => update({ nodes })} onEdgesChange={(edges) => update({ edges })} onPublicationsChange={(publications) => update({ publications })} pveTemplates={pveTemplates} vmImages={vmImages} lxcImages={lxcImages} zones={zones} sourceNotice={sourceNotice} locked={locked} frozen={template.status !== "draft"} actions={template.status === "draft" && <button type="button" className={styles.btnPrimary} disabled={saving || closing} onClick={publish}><MIcon name="publish" size={16} />{saving ? t("CourseTemplateEditorPage.publishing") : t("CourseTemplateEditorPage.publishLabel")}</button>} />}
  </div>;
}

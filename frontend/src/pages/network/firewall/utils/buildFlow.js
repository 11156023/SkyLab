const GATEWAY_KEY = "gateway";

export function portLabel(ports) {
  if (!ports?.length) return "";
  return ports
    .map((p) =>
      p.external_port
        ? `${p.external_port}→${p.port}/${p.protocol}`
        : p.port
        ? `${p.port}/${p.protocol}`
        : p.protocol
    )
    .join(", ");
}

export function buildFlow(topology, onDeleteEdge, showLabel) {
  const nodes = (topology.nodes ?? []).map((node) => ({
    id:       node.node_type === "gateway" ? GATEWAY_KEY : String(node.vmid),
    type:     node.node_type === "gateway" ? "gateway" : "vm",
    position: { x: node.position_x, y: node.position_y },
    data:     { ...node },
  }));

  const edges = (topology.edges ?? []).map((edge, i) => {
    const srcKey = edge.source_vmid === null ? GATEWAY_KEY : String(edge.source_vmid);
    const tgtKey = edge.target_vmid === null ? GATEWAY_KEY : String(edge.target_vmid);
    return {
      id:     `edge-${i}-${srcKey}-${tgtKey}`,
      source: srcKey,
      target: tgtKey,
      type:   "connection",
      data:   { label: portLabel(edge.ports), showLabel, edge, onDelete: onDeleteEdge },
    };
  });

  return { nodes, edges };
}

/** UI guard only; the backend ACL and launcher remain authoritative. */
const ipv4 = (host: unknown) => {
  const parts = String(host ?? "").split(".");
  return (
    parts.length === 4 &&
    parts.every(part => /^(0|[1-9]\d{0,2})$/.test(part) && Number(part) <= 255)
  );
};
export function resourceWindowBlock(
  resource: SkyLabResource,
  now = Date.now()
): "window_ended" | "window_not_started" | null {
  if (resource.start_blocked_reason) return resource.start_blocked_reason;
  const end = resource.window_end_at ? Date.parse(resource.window_end_at) : NaN;
  // Date-only expiry values use the same UTC midnight cutoff as the backend.
  const expiry = resource.expiry_date ? Date.parse(resource.expiry_date) : NaN;
  const start = resource.window_start_at
    ? Date.parse(resource.window_start_at)
    : NaN;
  if (
    (Number.isFinite(end) && now >= end) ||
    (Number.isFinite(expiry) && now >= expiry)
  )
    return "window_ended";
  if (Number.isFinite(start) && now < start) return "window_not_started";
  return null;
}
export function resourceTargets(
  resource: SkyLabResource,
  tunnels: SkyLabTunnelInfo[]
) {
  if (resource.vmid == null || resource.is_placeholder) return [];
  return tunnels.filter(
    target =>
      Number(target.vmid) === Number(resource.vmid) &&
      ["ssh", "rdp"].includes(String(target.service).toLowerCase()) &&
      ipv4(target.host) &&
      Number.isInteger(Number(target.port)) &&
      Number(target.port) > 0 &&
      Number(target.port) <= 65535
  );
}
export function canConnectResource(
  resource: SkyLabResource,
  target: SkyLabTunnelInfo,
  running: boolean,
  now = Date.now()
) {
  return (
    running &&
    resource.status === "running" &&
    resource.can_control !== false &&
    !resourceWindowBlock(resource, now) &&
    resourceTargets(resource, [target]).length > 0
  );
}

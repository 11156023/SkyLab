import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import ConnectionDetailPanel from "./ConnectionDetailPanel";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key) => key }),
}));

const baseEdge = {
  source_vmid: 101,
  target_vmid: 102,
  direction: "one_way",
  ports: [{ port: 0, protocol: "icmp" }],
};

function render(edge) {
  return renderToStaticMarkup(
    <ConnectionDetailPanel
      edge={edge}
      resolveName={(vmid) => `vm-${vmid}`}
      onClose={() => {}}
      onDelete={() => {}}
    />,
  );
}

describe("ConnectionDetailPanel managed topology", () => {
  it("marks template-managed edges and hides the delete action", () => {
    const html = render({ ...baseEdge, topology_managed: true });

    expect(html).toContain("ConnectionPanel.topologyManaged");
    expect(html).not.toContain("ConnectionPanel.delete");
  });

  it("keeps the delete action for regular firewall edges", () => {
    const html = render(baseEdge);

    expect(html).not.toContain("ConnectionPanel.topologyManaged");
    expect(html).toContain("ConnectionPanel.delete");
  });
});

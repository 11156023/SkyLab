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

describe("ConnectionDetailPanel course topology", () => {
  it("marks course-managed edges and hides the delete action", () => {
    const html = render({ ...baseEdge, course_managed: true });

    expect(html).toContain("ConnectionPanel.courseManaged");
    expect(html).not.toContain("ConnectionPanel.delete");
  });

  it("keeps the delete action for regular firewall edges", () => {
    const html = render(baseEdge);

    expect(html).not.toContain("ConnectionPanel.courseManaged");
    expect(html).toContain("ConnectionPanel.delete");
  });
});

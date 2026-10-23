// Demo clip: a student draws a connection between two machines on the network map;
// the firewall rules are written for them and the new line appears on the map.
import { firewallMocks, studentTopology } from "../mocks/network.mjs";

const node = (s, vmid) => s.page.locator(`.react-flow__node[data-id="${vmid}"]`);
const dlg = (s) => s.page.getByRole("dialog");

async function center(loc) {
  const b = await loc.boundingBox();
  if (!b) throw new Error(`not visible: ${loc}`);
  return { x: b.x + b.width / 2, y: b.y + b.height / 2 };
}

export default {
  role: "student",
  mocks(m) {
    firewallMocks(m, studentTopology(m));
    // Creating a connection adds it to the map, as the backend does.
    m.use([
      ["POST", /^\/api\/v1\/firewall\/connections$/, ({ body, state }) => {
        const edge = { direction: "one_way", ...body, topology_managed: false };
        state.topo = { ...state.topo, edges: [...state.topo.edges, edge] };
        return edge;
      }],
    ]);
  },
  async run(s, rec) {
    await s.go("/firewall", { wait: 1600 });
    // The map stays at its fitted size: zooming in would cut machines off, and the map fits
    // itself again when the new connection arrives.
    await node(s, 204).waitFor();
    await rec.start();
    await s.page.waitForTimeout(900);
    await rec.moveTo(node(s, 204), { duration: 800 });
    await s.page.waitForTimeout(500);
    const from = await center(node(s, 204).locator('[data-handleid="s-left"]'));
    const to = await center(node(s, 202).locator('[data-handleid="t-right"]'));
    await rec.drag(from, to, { duration: 1300 });
    await dlg(s).locator('[data-guide="connection-dialog-endpoints"]').waitFor();
    await s.page.waitForTimeout(700);
    rec.poster();
    await rec.type(dlg(s).locator("[data-port-input]").first(), "3306", { delay: 120 });
    await rec.click(dlg(s).getByRole("button", { name: s.t("Create connection", "建立連線") }), { wait: 1600 });
    // Point at the new line, halfway between the two machines.
    const a = await center(node(s, 204));
    const b = await center(node(s, 202));
    await rec.moveTo({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 + 14 }, { duration: 900 });
    await rec.stop(2600);
  },
};

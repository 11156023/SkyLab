// Demo clip: an administrator approves a student's machine request, then the student's
// My Resources page shows it being created and turning into a running machine.
import { useRequestReview } from "../mocks/resadmin.mjs";
import { LXC, registerResources, studentMachines } from "../mocks/resources.mjs";

// The student's request (Alex Wang, the mock student account); renamed so it does not
// clash with the flask-blog-lab container the student already has.
const HOST = "flask-blog-final";
const NEW_VMID = 10360;
const row = (s) => s.page.locator('[data-guide="request-review-row"]', { hasText: HOST }).first();
const decision = (s) => s.page.locator('[data-guide="request-review-decision"]');
const resourceRow = (s) => s.page.locator("tbody tr", { hasText: s.t("Final project", "期末專題") }).first();

export default {
  role: "admin",
  mocks(m) {
    registerResources(m);
    useRequestReview(m);
    const req = m.state.vmReqs.find((r) => r.hostname === "flask-blog-lab" && r.status === "pending");
    Object.assign(req, { hostname: HOST, cores: 2, memory: 4096 });
    m.state.list = studentMachines(m);
    // My Resources: the approved request is created over two polls (every 5 s), then becomes a machine.
    let polls = 0;
    m.use([
      ["GET", /^\/api\/v1\/vm-requests\/my$/, () => {
        if (req.status !== "approved") return { data: [], count: 0 };
        polls += 1;
        if (polls === 1) req.provisioning_status = "running";
        if (polls >= 3 && req.vmid == null) {
          Object.assign(req, { provisioning_status: "completed", vmid: NEW_VMID, assigned_node: "pve-01" });
          const lxc = studentMachines(m).find((r) => r.vmid === LXC);
          m.state.list = [...m.state.list, {
            ...lxc, vmid: NEW_VMID, request_id: req.id, name: HOST, node: "pve-01", ip_address: "10.20.3.160",
            environment_type: m.L("Final project", "期末專題"), status: "running", uptime: 40, cpu: 0.04,
            public_urls: [],
          }];
        }
        return { data: [req], count: 1 };
      }],
    ]);
  },
  async run(s, rec) {
    const m = s.m;
    await s.go("/request-review", { wait: 900 });
    await row(s).waitFor();
    await rec.start();
    await s.page.waitForTimeout(800);
    await rec.click(row(s), { wait: 1300 });
    rec.poster();
    await rec.type(decision(s).locator("textarea"),
      s.t("Final project for Web Programming, approved until demo day.", "網頁程式設計期末專題，核准到成果發表日。"),
      { delay: 34 });
    await rec.click(decision(s).getByRole("button", { name: s.t("Approve", "核准"), exact: true }), { wait: 1500 });

    // The student's side: the approved request is being created, then shows up as a machine.
    m.state.role = "student";
    await s.go("/my-resources", { wait: 600 });
    await resourceRow(s).or(s.page.locator("tbody tr", { hasText: HOST })).first().waitFor();
    await rec.moveTo(s.page.locator("tbody tr", { hasText: HOST }).first(), { duration: 800, dx: -120 });
    rec.speed(2.5);
    await resourceRow(s).waitFor({ timeout: 30000 });
    rec.speed(1);
    await s.page.waitForTimeout(600);
    // The row's first cells: moving to the action buttons would scroll the table sideways.
    await rec.moveTo(resourceRow(s).locator("td").nth(1), { duration: 900 });
    await rec.stop(2400);
  },
};

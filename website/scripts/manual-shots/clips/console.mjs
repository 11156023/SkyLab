// Demo clip: a student opens a VM's desktop in the browser, then a container's terminal.
import { VNC_FIX, desktopJpeg, registerResources, vncServer } from "../mocks/resources.mjs";

const prompt = (dir) => `\x1b[1;32mstudent@flask-blog-lab\x1b[0m:\x1b[1;34m${dir}\x1b[0m$ `;
const ok = (name) => `\x1b[32m✔\x1b[0m ${name.padEnd(18, ".")} passed`;
// Command → [output lines, working directory afterwards]
const OUTPUT = {
  "git clone https://github.com/ntub-os-lab/scheduler-lab.git": [[
    "Cloning into 'scheduler-lab'...",
    "remote: Enumerating objects: 48, done.",
    "remote: Counting objects: 100% (48/48), done.",
    "Receiving objects: 100% (48/48), 21.36 KiB | 3.05 MiB/s, done.",
  ], "~"],
  "cd scheduler-lab && make test": [[
    "gcc -O2 -Wall -o sched src/scheduler.c src/queue.c",
    "./tests/run.sh",
    ok("fcfs "), ok("sjf "), ok("round_robin "), ok("priority "),
    "",
    "\x1b[1;32m4 passed\x1b[0m in 0.42s",
  ], "~/scheduler-lab"],
};

// termproxy-style shell: echoes what is typed and answers the commands of the clip line by line.
function shellServer(ws) {
  ws.send("OK" + "Welcome to Ubuntu 24.04.1 LTS (GNU/Linux 6.8.12-4-pve x86_64)\r\n\r\n" + prompt("~"));
  let line = "";
  ws.onMessage((msg) => {
    const mt = (typeof msg === "string" ? msg : msg.toString("utf8")).match(/^0:\d+:([\s\S]*)$/);
    if (!mt) return;
    for (const ch of mt[1]) {
      if (ch !== "\r") {
        line += ch;
        ws.send(ch);
        continue;
      }
      const [out = [], dir = "~"] = OUTPUT[line.trim()] ?? [];
      line = "";
      ws.send("\r\n");
      out.forEach((text, i) => setTimeout(() => ws.send(`${text}\r\n`), 140 * (i + 1)));
      setTimeout(() => ws.send(prompt(dir)), 140 * (out.length + 1));
    }
  });
}

const row = (s, name) => s.page.locator("tbody tr", { hasText: name }).first();

export default {
  role: "student",
  mocks(m) {
    registerResources(m);
  },
  async run(s, rec) {
    const jpg = await desktopJpeg(s);
    s.m.ws = (p, ws) => {
      if (p.startsWith("/ws/vnc/")) vncServer(ws, jpg);
      if (p.startsWith("/ws/terminal/")) shellServer(ws);
    };
    await s.go("/my-resources", { wait: 900 });
    await row(s, "ubuntu-desktop").waitFor();
    await s.page.evaluate(VNC_FIX);
    await s.settle(300);

    await rec.start();
    await s.page.waitForTimeout(700);
    await rec.click(row(s, "ubuntu-desktop").getByRole("button", { name: s.t("Console", "控制台") }), { wait: 300 });
    const dlg = s.page.getByRole("dialog");
    await dlg.getByText(s.t("Connected", "已連接"), { exact: true }).waitFor({ timeout: 10000 });
    await s.page.waitForTimeout(1200);
    rec.poster();
    await rec.moveTo({ x: 700, y: 440 }, { duration: 900 });
    await s.page.waitForTimeout(1200);
    await rec.click(dlg.getByRole("button", { name: s.t("Close", "關閉") }), { wait: 600 });

    await rec.click(row(s, "flask-blog-lab").getByRole("button", { name: s.t("Terminal", "終端機") }), { wait: 300 });
    const screen = s.page.getByRole("dialog").locator(".xterm-screen");
    await screen.waitFor();
    await s.page.waitForTimeout(700);
    await rec.click(screen, { wait: 300 });
    // Out of the way of the text while typing.
    await rec.moveTo({ x: 1120, y: 720 }, { duration: 500 });
    for (const [cmd, [out]] of Object.entries(OUTPUT)) {
      await s.page.keyboard.type(cmd, { delay: 38 });
      await s.page.waitForTimeout(250);
      await s.page.keyboard.press("Enter");
      await s.page.waitForTimeout(140 * (out.length + 1) + 700);
    }
    await rec.stop(2200);
  },
};

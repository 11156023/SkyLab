// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, test, vi } from "vitest";

const socket = vi.hoisted(() => ({ handler: null, connected: false }));
const { getLive } = vi.hoisted(() => ({ getLive: vi.fn() }));

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../services/classroom", () => ({ ClassroomService: { getLive } }));
vi.mock("../../hooks/useClassroomSocket", () => ({
  useClassroomSocket: (handler) => {
    socket.handler = handler;
    return { connected: socket.connected };
  },
}));
vi.mock("./LiveBanner", () => ({
  default: ({ onWatch }) => (
    <button type="button" data-testid="banner" onClick={onWatch}>
      banner
    </button>
  ),
}));
vi.mock("./ClassroomWatchDialog", () => ({
  default: ({ sessionId }) => <div data-testid="watch" data-session={sessionId} />,
}));

import ClassroomStudentLayer, { useClassroomTakeover } from "./ClassroomStudentLayer";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Probe() {
  return <span data-testid="takeover">{String(useClassroomTakeover(101))}</span>;
}

let host;
let root;

beforeEach(() => {
  socket.handler = null;
  socket.connected = false;
  getLive.mockReset();
  getLive.mockResolvedValue({ session: null });
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

const render = () =>
  act(async () =>
    root.render(
      <ClassroomStudentLayer>
        <Probe />
      </ClassroomStudentLayer>,
    ),
  );
const emit = (event) => act(async () => socket.handler(event));
const q = (id) => document.querySelector(`[data-testid='${id}']`);

test("a stale takeover overlay is cleared when the signalling socket reconnects", async () => {
  socket.connected = true;
  await render();
  await emit({ type: "takeover_started", vmid: 101 });
  expect(q("takeover").textContent).toBe("true");

  socket.connected = false;
  await render();
  socket.connected = true;
  await render();
  expect(q("takeover").textContent).toBe("false");
});

test("on reconnect the takeover state follows taken_over_vmids when the backend reports it", async () => {
  socket.connected = true;
  await render();
  socket.connected = false;
  await render();
  getLive.mockResolvedValue({ session: null, taken_over_vmids: [101] });
  socket.connected = true;
  await render();
  expect(q("takeover").textContent).toBe("true");
});

test("live_stopped for another session keeps the current broadcast and its watch dialog", async () => {
  await render();
  await emit({ type: "live_started", session_id: "A" });
  await emit({ type: "live_started", session_id: "B" });
  await act(async () => q("banner").click());
  expect(q("watch").dataset.session).toBe("B");

  await emit({ type: "live_stopped", session_id: "A" });
  expect(q("banner")).not.toBeNull();
  expect(q("watch").dataset.session).toBe("B");
});

test("live_stopped for the current session re-queries so a still-running broadcast takes over", async () => {
  await render();
  await emit({ type: "live_started", session_id: "B" });
  getLive.mockResolvedValue({ session: { id: "A" } });
  await emit({ type: "live_stopped", session_id: "B" });
  expect(q("banner")).not.toBeNull();
  await act(async () => q("banner").click());
  expect(q("watch").dataset.session).toBe("A");
});

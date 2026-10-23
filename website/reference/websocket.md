# WebSocket endpoints

Consoles and live updates use WebSocket connections registered directly on the application (they are not part of the `/api/v1` router and therefore not in the OpenAPI schema). nginx forwards everything under `/ws` to the backend.

| Path | Purpose | Authentication |
| --- | --- | --- |
| `/ws/vnc/{vmid}` | VNC console. The backend opens a VNC ticket on Proxmox and proxies the RFB stream; the browser side is noVNC. Clipboard transfer works when the guest's display is configured with `clipboard=vnc`. | Access token in the `token` query parameter |
| `/ws/terminal/{vmid}` | Terminal for LXC containers. The backend opens a terminal ticket (termproxy) on the Proxmox node that hosts the container and bridges that websocket to xterm.js. | Access token in the `token` query parameter |
| `/ws/jobs` | Live status of the caller's task records (clone, batch provisioning, reset, deletion) for the Jobs page. | Access token in the `token` query parameter |
| `/ws/classroom` | Signalling channel for classroom sessions: teacher broadcast, student screen fan-out and control. | Access token in the `token` query parameter |
| `/ws/classroom/{session_id}/watch` | VNC fan-out of one student's screen to the teacher's classroom view. | Access token in the `token` query parameter |
| `/ws/courses/paths/{path_id}/progress` | Progress updates of a course path for the teacher's dashboard. | Access token in the `token` query parameter |

Notes:

- The token is the same JWT used for REST calls. Query-string transport is used because browsers cannot set headers on WebSocket handshakes.
- The backend refuses a console connection when the caller has no use permission on the machine, and for personal machines also when the machine has expired or is outside its approved usage window. The UI keeps the console button disabled while a machine is still starting (GPU machines report `starting` until the QEMU monitor is ready).
- Proxies between the browser and the backend must allow long-lived connections; the bundled nginx configuration already does.
- Inside the backend each proxy runs two pump tasks that share an `asyncio.Event` to signal disconnects. Contributors adding a WebSocket endpoint should follow the same pattern; see [Backend](/guides/backend#api-overview).

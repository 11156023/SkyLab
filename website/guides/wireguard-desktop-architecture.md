# Desktop WireGuard Architecture and Deployment

SkyLab Connect uses WireGuard to build an encrypted L3 network from the desktop client to the Gateway VM. Once the desktop client has been authorized, it connects directly to the VM's real address and service port.

## Connection flow

1. The Desktop Client generates an X25519 key pair locally; the private key is stored only in encrypted form via Electron `safeStorage`.
2. The Client calls `POST /api/v1/desktop-client/wireguard/connect` with its login token, sending only the device ID and the public key.
3. The Backend uses the existing `resources/my` authorization logic to determine which running VMs the user is currently allowed to control.
4. The Backend adds the WireGuard peer through the Gateway's existing SSH management channel and installs a time-limited nftables ACL.
5. The Client installs a short-lived WireGuard tunnel whose routes cover only the VM subnet; SSH/RDP connect directly to `VM_IP:22` or `VM_IP:3389`.
6. When the user disconnects or logs out, the Client removes the local tunnel and the Backend removes the peer and the ACL at the same time.

The ACL tuple is `client_tunnel_ip . vm_ip . tcp_port`. LXC containers get SSH only; QEMU VMs get SSH and RDP. ACLs expire after eight hours by default, so the Gateway stops forwarding the traffic on its own even if it never receives the disconnect request.

## Gateway VM

Installer defaults (each one can be overridden with the environment variable of the same name at the top of `gateway/install.sh`; the one-click install on the "Install services" tab of the "Gateway VM" page fills them in from the detected interfaces and the backend's `WIREGUARD_*` settings):

- WireGuard interface: `wg0` / `10.250.0.1/16` (`WG_INTERFACE`, `WG_ADDRESS`)
- UDP listen port: `51821` (`WG_LISTEN_PORT`; `51820` is left to NetBird), received on `eth0` (`WG_INGRESS_INTERFACE`)
- VM network: `10.10.0.0/16`, sent out via `eth1` (`WG_VM_SUBNET`, `WG_VM_INTERFACE`)
- SNAT address: `10.10.0.2`, which must already be configured on `eth1` (`WG_SNAT_ADDRESS`)
- nftables table: `inet skylab_wg`
- systemd units: `wg-quick@wg0`, `skylab-wg-firewall.service`

The Gateway must run Debian 13 (trixie); the installer refuses any other system (including Debian 12, whose nftables is too old for `nft destroy table`) before changing anything. On a new Gateway, first confirm that the interfaces, addresses and UDP port do not conflict with anything, then run the installer from the "Install services" tab, or as root on the Gateway:

```bash
sudo ./gateway/install.sh
```

This is the only installation entry point for the Gateway. It installs nginx (port forwarding and domain reverse proxy), WireGuard, the nftables ACL and SNAT, and the Prometheus exporters; the HTTPS certificate is supplied by the administrator. The installer first backs up the network, firewall and service configuration to `/root/skylab-backups/`; it does not remove an existing NetBird installation and does not wipe the whole UFW ruleset. If `/etc/wireguard/wg0.conf` is not a file managed by SkyLab, the installer refuses to overwrite it.

The firewall or NAT upstream of the Gateway must also forward external UDP `51821` to the Gateway. If the Client and the Gateway are on the same routable network, the Gateway's internal address can be used directly.

A Gateway installed by an older version still uses the `campus_cloud_wg` rule table. Deploy the new Backend first (it can still operate the old rules), then reinstall from the "Install services" tab. The installer keeps the WireGuard keys, creates `skylab_wg` and `skylab-wg-firewall.service`, removes the old unit and table, and updates its UFW rules under both the old and the new names; do not rename the service or the table by hand. Reinstalling rebuilds the dynamic ACLs, so wait for the Backend to replay the authorizations or have the app reconnect. In UFW, the IPv4 and `(v6)` rows, and the SSH ACLs for different VM IPs, are separate rules, not duplicates.

## Backend configuration

```dotenv
WIREGUARD_ENDPOINT_HOST=vpn.example.edu      # DNS name or public IP the clients can reach
WIREGUARD_ENDPOINT_PORT=51821
WIREGUARD_INTERFACE=wg0
WIREGUARD_CLIENT_SUBNET=10.250.0.0/16
WIREGUARD_VM_SUBNET=10.10.0.0/16
WIREGUARD_KEEPALIVE_SECONDS=25
WIREGUARD_SESSION_TTL_SECONDS=28800
```

Apart from `WIREGUARD_ENDPOINT_HOST` these are the defaults; the reconciler settings (`WIREGUARD_RECONCILE_*`, `WIREGUARD_REVOKED_RETENTION_DAYS`, `WIREGUARD_ALLOW_INACTIVE_PEER_TRANSFER`) are listed in the [environment variable reference](/operations/environment).

`WIREGUARD_VM_SUBNET` is only a fallback for before IP management is configured. After that, the platform subnet from IP management is the source of truth for desktop routes, connection targets, Gateway ACLs and the SNAT installation.

In production, `WIREGUARD_ENDPOINT_HOST` should be a DNS name or public IP that the Client can reach. When it is empty, clients are given the Gateway address from the "Gateway VM" connection settings, which is the SSH management address and often not reachable from outside.

## Desktop Client

The Windows build uses the tunnel service of the official WireGuard for Windows. The official Setup EXE bundles the official MSI, verified by SHA-256 and Authenticode, and installs it together with SkyLab Connect; if the portable EXE detects that WireGuard is not installed on the system, it prompts for UAC on the first connection and installs the same MSI. Students therefore do not need to download WireGuard separately beforehand.

WireGuard is a shared system networking component, so uninstalling SkyLab Connect does not remove WireGuard, to avoid breaking tunnels used by other applications. The third-party license notices ship alongside in the app resources at `wireguard/THIRD_PARTY_NOTICES.txt`.

The Client never sends its private key to the Backend, and it deletes the temporary plaintext configuration file immediately after generating the tunnel configuration.

## Verification

Gateway health check:

```bash
systemctl is-active wg-quick@wg0 skylab-wg-firewall.service
wg show wg0
nft list set inet skylab_wg allowed_tcp
```

After connecting you should see one peer plus the time-limited ACLs that belong only to that user's VMs. After disconnecting, the peer and its ACLs should disappear immediately. When testing from the Client, connect directly to the VM IP.

# Desktop WireGuard Architecture and Deployment

> **English** | [繁體中文](./wireguard-desktop-architecture.zh-TW.md)

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

The current configuration uses:

- WireGuard interface: `wg0` / `10.250.0.1/16`
- UDP listen port: `51821` (`51820` is reserved for NetBird)
- VM network: `10.10.0.0/16`, sent out via `eth1`
- SNAT address: `10.10.0.2`
- nftables table: `inet skylab_wg`
- systemd units: `wg-quick@wg0`, `skylab-wg-firewall.service`

On a new Gateway, first confirm that the interface, addresses and UDP port do not conflict with anything, then run as root:

```bash
sudo ./gateway/install.sh
```

This is the only installation entry point for the Gateway. It installs nginx (port forwarding and domain reverse proxy), WireGuard, the nftables ACL and SNAT (the HTTPS certificate is supplied by the administrator; certbot is not installed). The installer first backs up the network, firewall and service configuration to `/root/skylab-backups/`; it does not remove an existing NetBird installation and does not wipe the whole UFW ruleset. If `/etc/wireguard/wg0.conf` is not a file managed by SkyLab, the installer refuses to overwrite it.

The firewall or NAT upstream of the Gateway must also forward external UDP `51821` to the Gateway. If the Client and the Gateway are on the same routable network, the Gateway's internal address can be used directly.

For an existing Gateway, deploy the new Backend first, then rerun the installation from the Gateway management page.
Before the migration the Backend can still operate the old `campus_cloud_wg` rules; the installer keeps the WireGuard
keys, creates `skylab_wg` and `skylab-wg-firewall.service`, removes the old service dependencies and
rule table, and updates the managed UFW rules under both the old and the new names. Do not just rename the service or the rule table by hand.
Applying the change rebuilds the dynamic ACLs, so wait for the Backend to replay the authorizations or have the app reconnect.
The IPv4 and `(v6)` rows in UFW, as well as SSH ACLs for different VM IPs, are separate rules.

## Backend configuration

```dotenv
WIREGUARD_ENDPOINT_HOST=192.168.100.143
WIREGUARD_ENDPOINT_PORT=51821
WIREGUARD_INTERFACE=wg0
WIREGUARD_CLIENT_SUBNET=10.250.0.0/16
WIREGUARD_VM_SUBNET=10.10.0.0/16
WIREGUARD_KEEPALIVE_SECONDS=25
WIREGUARD_SESSION_TTL_SECONDS=28800
```

`WIREGUARD_VM_SUBNET` is a bootstrap fallback. Once IP management is configured,
the platform subnet stored in `SubnetConfig.cidr` is the source of truth for
desktop routes, connection targets, Gateway ACLs, and SNAT installation.

In production, `WIREGUARD_ENDPOINT_HOST` should be a DNS name or public IP that the Client can reach, not the SSH address used for management. The Alembic migration that creates the `wireguard_peers` table must be applied before the Backend is deployed.

## Desktop Client

The current Windows build uses the tunnel service of the official WireGuard for Windows. The official Setup EXE bundles the official MSI, verified by SHA-256 and Authenticode, and installs it together with SkyLab Connect; if the portable EXE detects that WireGuard is not installed on the system, it prompts for UAC on the first connection and installs the same MSI. Students therefore do not need to download WireGuard separately beforehand.

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

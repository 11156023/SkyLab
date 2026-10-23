# Backup and restore

What to back up on a SkyLab server, how, and how to put it back. Machines themselves are backed up inside SkyLab (per-machine backups on Proxmox storage); this page is about the platform.

## What matters

| Item | Where | Why |
| --- | --- | --- |
| PostgreSQL database `app` (`POSTGRES_DB`) | Docker volume `<project>_app-db-data` (for example `app_app-db-data` for a checkout in `/opt/skylab/app`), service `db` | Users, requests, machines, classes, courses, firewall rules, settings, audit log, encrypted credentials. Without it SkyLab knows nothing about your machines. |
| `.env` | Repository root | `SECRET_KEY` decrypts the credentials in the database. A dump restored with a different key is useless. |
| LiteLLM database `litellm` and `vllm-service/litellm/.env` | Same PostgreSQL instance; file next to the compose include | AI API keys and model routing, when the AI stack is in use. |
| `vllm-service/models.json` | Repository | Model routing input for `prepare-ai-stack.sh`. |
| Gateway VM | The VM you provide | SkyLab rewrites the nginx rules ("Sync rules to Gateway") and replays the WireGuard peers from the database; the HTTPS certificate and private key are yours and are not stored in SkyLab. |
| Monitoring volumes (`prometheus-data`, `grafana-data`, `loki-data`, `influxdb-data`) | Docker volumes (with the same project prefix) | Optional. Metrics history only; dashboards are provisioned from the repository. |

Redis holds only rate limits, revoked tokens, queues and caches; it is not backed up.

## Backing up the database

Dump through the `db` container; it works while the stack is running. The single quotes make the container's own `POSTGRES_USER` / `POSTGRES_DB` apply, so `.env` does not have to be sourced:

```bash
cd /opt/skylab/app
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > "skylab-$(date +%F).dump"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc litellm' > "litellm-$(date +%F).dump"   # AI stack only
```

`-Fc` produces a compressed custom-format archive that `pg_restore` can replay selectively. The LiteLLM database name is the one in `DATABASE_URL` of `vllm-service/litellm/.env` (`litellm` by default). Copy the dumps together with `.env` and `vllm-service/litellm/.env` to a location outside the server, and keep `.env` as carefully as the dump: it contains the key.

A nightly cron entry on the host is enough for a campus deployment:

```cron
30 3 * * * cd /opt/skylab/app && docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > /var/backups/skylab/skylab-$(date +\%F).dump
```

Alternatively stop the stack and copy the volume; a volume copy is only consistent when the database is stopped. The volume name carries the Compose project prefix, so look it up with `docker volume ls` first:

```bash
docker run --rm -v app_app-db-data:/data -v "$PWD":/out alpine tar czf /out/app-db-data.tgz -C /data .
```

## Restoring the database

1. Restore `.env` (same `SECRET_KEY`) and the repository checkout at the commit the dump was taken from, or a newer one.
2. Start only the database:

   ```bash
   docker compose up -d db
   ```

3. Replace the database contents:

   ```bash
   docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
   docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner' < skylab-2026-10-18.dump
   ```

4. With the AI stack, restore the `litellm` database as well. It must stay owned by its own role, so recreate the empty database and the role first with the (URL-decoded) password from `DATABASE_URL`, and restore **without** `--no-owner`. Pass `--database` / `--role` to the script if `DATABASE_URL` uses names other than `litellm`:

   ```bash
   docker compose exec -T db sh -c 'dropdb -U "$POSTGRES_USER" --if-exists litellm'
   LITELLM_DB_PASSWORD='<password from DATABASE_URL>' bash scripts/init-litellm-db.sh
   docker compose exec -T db sh -c 'pg_restore -U "$POSTGRES_USER" -d litellm' < litellm-2026-10-18.dump
   ```

5. Start the rest (with the AI stack, `bash scripts/prepare-ai-stack.sh --start` instead of the first command). `prestart` applies any migrations newer than the dump, then the backend comes up:

   ```bash
   docker compose up -d --scale litellm=0
   docker compose logs -f prestart
   ```

6. Log in, open "PVE Management" and press **Test** on each connection. If every test fails although PVE is reachable, look for a password decryption error in `docker compose logs backend`: the `SECRET_KEY` in `.env` is not the one the dump was encrypted with.

## After a restore

- Machines on Proxmox are unaffected by a platform restore, but anything created or deleted between the dump and the restore is out of sync: machines in the dump that no longer exist on PVE show with an unknown status, and machines built after the dump (including those of requests approved after it, which are lost) have no record in SkyLab. Administrators can delete both kinds from Resource Management.
- Logins keep working while `SECRET_KEY` is unchanged, except for accounts whose password or sessions were reset after the dump; they log in again with the password as of the dump.
- Press "Sync rules to Gateway" on the "Install services" tab of the "Gateway VM" page so the nginx rules match the restored database. WireGuard peers are replayed to the gateway by the backend's reconciler (every 60 seconds by default).

## Machine backups

Per-machine backups are created from the machine's detail page and stored on the backup storage that an administrator sets for each PVE connection (without one, the feature is not offered). They are Proxmox `vzdump` archives tagged by SkyLab and are deleted together with the machine. Snapshots stay on the machine's own disk storage. Neither is part of the platform backup; include the Proxmox backup storage in your Proxmox backup plan.

## Disaster recovery checklist

1. New server with Docker, same host name or an updated `FRONTEND_HOST`.
2. Clone the repository, restore `.env` and the LiteLLM files.
3. Restore the database (above), start the stack, check `GET /api/v1/utils/health-check/ready`.
4. Test every PVE connection; sync.
5. Sync the rules to the gateway; verify the platform entry answers over HTTPS.
6. Re-run the AI stack helper if models are used.

## Related

- [Upgrading and migrations](/operations/upgrade) — always dump before an upgrade
- [Deployment](/guides/deployment#secrets) — why `SECRET_KEY` must travel with the dump
- [Machine details](/manual/resource-detail) — per-machine backup and restore

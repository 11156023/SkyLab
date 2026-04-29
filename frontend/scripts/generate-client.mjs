#!/usr/bin/env node
/**
 * Wrapper around `openapi-ts -f openapi-ts.config.ts` that preserves the
 * hand-maintained compatibility shims under `src/client/`.
 *
 * Why this exists
 * ---------------
 * `@hey-api/openapi-ts` v0.94+ writes a fresh `index.ts`, removes anything
 * under `src/client/core/` whose name does not end in `.gen.ts`, and never
 * emits `legacy-services.ts`. The application still relies on the older
 * fetch-based `OpenAPI` singleton, the legacy `ApiError` class, and the
 * facaded service classes exported from `legacy-services.ts`. Without this
 * wrapper, a single `bun run generate-client` silently breaks 15+ service
 * files and the entire auth flow.
 *
 * Strategy
 * --------
 * 1. Snapshot every hand-maintained file under `src/client/` into a temp
 *    directory keyed by the file's relative path.
 * 2. Run the real generator via `bun x openapi-ts -f openapi-ts.config.ts`
 *    (works on Linux / macOS / Windows).
 * 3. Restore each snapshotted file, overwriting whatever the generator
 *    produced. Files that the generator deleted are recreated; files that
 *    were not part of the snapshot are left as-is.
 *
 * If the generator fails, the snapshot is still restored so the working
 * tree never ends up in a half-broken state.
 */

import { spawnSync } from "node:child_process"
import fs from "node:fs"
import os from "node:os"
import path from "node:path"
import { fileURLToPath } from "node:url"

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const frontendDir = path.resolve(__dirname, "..")
const clientDir = path.join(frontendDir, "src", "client")

/**
 * Files (relative to `src/client/`) that are NOT generated and must survive
 * a regeneration. Anything inside `src/client/core/` whose name does not
 * end in `.gen.ts` is also preserved automatically (see `collectPreservedFiles`).
 */
const EXPLICIT_PRESERVED_FILES = ["index.ts", "legacy-services.ts"]

function collectPreservedFiles() {
  const preserved = new Set(EXPLICIT_PRESERVED_FILES)
  const coreDir = path.join(clientDir, "core")
  if (fs.existsSync(coreDir)) {
    for (const entry of fs.readdirSync(coreDir, { withFileTypes: true })) {
      if (!entry.isFile()) continue
      if (entry.name.endsWith(".gen.ts")) continue
      preserved.add(path.posix.join("core", entry.name))
    }
  }
  return [...preserved].filter((rel) =>
    fs.existsSync(path.join(clientDir, rel)),
  )
}

function snapshotFiles(relPaths, snapshotDir) {
  for (const rel of relPaths) {
    const src = path.join(clientDir, rel)
    const dst = path.join(snapshotDir, rel)
    fs.mkdirSync(path.dirname(dst), { recursive: true })
    fs.copyFileSync(src, dst)
  }
}

function restoreFiles(relPaths, snapshotDir) {
  for (const rel of relPaths) {
    const src = path.join(snapshotDir, rel)
    const dst = path.join(clientDir, rel)
    if (!fs.existsSync(src)) continue
    fs.mkdirSync(path.dirname(dst), { recursive: true })
    fs.copyFileSync(src, dst)
  }
}

function runGenerator() {
  // Use `bun x` so the package's installed @hey-api/openapi-ts is invoked
  // even when the user runs this from a Node/npm shell. On Windows,
  // spawnSync needs `shell: true` (or .cmd resolution) to find `bun`.
  const result = spawnSync(
    "bun",
    ["x", "openapi-ts", "-f", "openapi-ts.config.ts"],
    {
      cwd: frontendDir,
      stdio: "inherit",
      shell: process.platform === "win32",
    },
  )
  if (result.error) throw result.error
  return result.status ?? 0
}

function main() {
  if (!fs.existsSync(path.join(frontendDir, "openapi.json"))) {
    console.error(
      "frontend/openapi.json not found. Run scripts/generate-client.mjs from the repo root,\n" +
        "or copy a fresh openapi.json into frontend/ before invoking this wrapper.",
    )
    process.exit(1)
  }

  const preserved = collectPreservedFiles()
  if (preserved.length === 0) {
    console.warn(
      "[generate-client] No hand-maintained files found to preserve. Continuing anyway.",
    )
  } else {
    console.log(
      `[generate-client] Preserving ${preserved.length} hand-maintained file(s):`,
    )
    for (const rel of preserved) console.log(`  - ${rel}`)
  }

  const snapshotDir = fs.mkdtempSync(
    path.join(os.tmpdir(), "campuscloud-client-"),
  )

  let exitCode = 0
  try {
    snapshotFiles(preserved, snapshotDir)
    exitCode = runGenerator()
    if (exitCode !== 0) {
      console.error(
        `[generate-client] openapi-ts exited with code ${exitCode}; restoring snapshot.`,
      )
    }
    restoreFiles(preserved, snapshotDir)
  } catch (err) {
    console.error("[generate-client] Failed:", err)
    try {
      restoreFiles(preserved, snapshotDir)
    } catch (restoreErr) {
      console.error("[generate-client] Restore also failed:", restoreErr)
    }
    process.exit(1)
  } finally {
    try {
      fs.rmSync(snapshotDir, { recursive: true, force: true })
    } catch {
      // best-effort cleanup
    }
  }

  process.exit(exitCode)
}

main()

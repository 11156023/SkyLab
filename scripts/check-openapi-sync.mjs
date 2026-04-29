import path from "node:path"
import { spawnSync } from "node:child_process"

const rootDir = process.cwd()

function run(command, args, cwd = rootDir) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    // On Windows, `bun` (and other npm-installed shims) are `.cmd` files
    // and require shell:true for spawnSync to resolve them. Posix shells
    // handle bare names fine.
    shell: process.platform === "win32",
  })

  if (result.error) {
    throw result.error
  }

  if ((result.status ?? 0) !== 0) {
    process.exit(result.status ?? 1)
  }
}

run("node", [path.join("scripts", "generate-client.mjs")])
run("git", ["diff", "--exit-code", "--", "frontend/openapi.json", "frontend/src/client"])

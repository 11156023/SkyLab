import fs from "node:fs"
import path from "node:path"
import { spawnSync } from "node:child_process"

const rootDir = process.cwd()
const backendDir = path.join(rootDir, "backend")
const frontendDir = path.join(rootDir, "frontend")
const frontendOpenApiPath = path.join(frontendDir, "openapi.json")

function assertSupportedNodeVersion() {
  const [major, minor] = process.versions.node.split(".").map(Number)
  if (major > 20 || (major === 20 && minor >= 19)) {
    return
  }

  console.error(
    `OpenAPI code generation requires Node 20.19+; current version is ${process.versions.node}.`,
  )
  process.exit(1)
}

function run(command, args, cwd = rootDir, options = {}) {
  const result = spawnSync(command, args, {
    cwd,
    stdio: "inherit",
    // On Windows, `bun` is a `.cmd` shim; spawnSync needs shell:true to
    // resolve it. Posix shells handle the bare name fine.
    shell: process.platform === "win32",
    ...options,
  })

  if (result.error) {
    throw result.error
  }

  if ((result.status ?? 0) !== 0) {
    process.exit(result.status ?? 1)
  }

  return result
}

assertSupportedNodeVersion()

const openApiResult = spawnSync(
  "uv",
  [
    "run",
    "python",
    "-c",
    "import app.main; import json; print(json.dumps(app.main.app.openapi()))",
  ],
  {
    cwd: backendDir,
    encoding: "utf8",
    shell: false,
  },
)

if (openApiResult.error) {
  throw openApiResult.error
}

if ((openApiResult.status ?? 0) !== 0) {
  process.exit(openApiResult.status ?? 1)
}

fs.writeFileSync(frontendOpenApiPath, openApiResult.stdout ?? "", "utf8")

run("bun", ["run", "generate-client"], frontendDir)

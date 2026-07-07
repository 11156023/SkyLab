import path from "node:path"
import { defineConfig } from "@hey-api/openapi-ts"

export default defineConfig({
  input: path.resolve(__dirname, "./openapi.json"),
  output: path.resolve(__dirname, "./src/client"),

  plugins: [
    "@hey-api/client-axios",
    {
      name: "@hey-api/sdk",
      // NOTE: this doesn't allow tree-shaking
      asClass: true,
      operationId: true,
      classNameBuilder: "{{name}}Service",
    },
    {
      name: "@hey-api/schemas",
      type: "json",
    },
  ],
})

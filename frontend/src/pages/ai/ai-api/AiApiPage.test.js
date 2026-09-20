import { describe, expect, test } from "vitest";

import { buildAiProxyBaseUrl, buildApiExample, buildModelsCommand } from "./AiApiPage";

describe("AI API 文件", () => {
  test("從公開根網址建立可直接給 OpenAI SDK 使用的 Base URL", () => {
    expect(buildAiProxyBaseUrl("https://api.example.edu/"))
      .toBe("https://api.example.edu/api/v1/ai-proxy");
    expect(buildAiProxyBaseUrl("https://api.example.edu/api/v1"))
      .toBe("https://api.example.edu/api/v1/ai-proxy");
    expect(buildAiProxyBaseUrl("https://api.example.edu/api/v1/ai-proxy"))
      .toBe("https://api.example.edu/api/v1/ai-proxy");
  });

  test.each(["javascript", "python", "bash", "cmd"])(
    "%s 範例包含實際端點與必要替換值",
    (language) => {
      const example = buildApiExample(language, "https://api.example.edu");

      expect(example).toContain("https://api.example.edu/api/v1/ai-proxy");
      expect(example).toContain("YOUR_API_KEY");
      expect(example).toContain("MODEL_NAME");
      expect(example).toContain("INPUT");
    },
  );

  test.each(["javascript", "python", "bash", "cmd"])(
    "%s 的 Chat Completions 範例打到 chat 端點，替換值一樣齊全",
    (language) => {
      const example = buildApiExample(language, "https://api.example.edu", "chat");

      expect(example).toContain("https://api.example.edu/api/v1/ai-proxy");
      expect(example).toMatch(/chat[./]completions/);
      expect(example).not.toContain("/responses");
      expect(example).toContain("YOUR_API_KEY");
      expect(example).toContain("MODEL_NAME");
      expect(example).toContain("INPUT");
    },
  );

  test("CMD 範例的 JSON 內層引號有跳脫，貼到命令列才不會被拆開", () => {
    for (const endpoint of ["responses", "chat"]) {
      const body = buildApiExample("cmd", "https://api.example.edu", endpoint).split("-d ")[1];

      expect(body.startsWith('"{\\"model\\":')).toBe(true);
      expect(JSON.parse(body.slice(1, -1).replaceAll('\\"', '"')).model).toBe("MODEL_NAME");
    }
  });

  test.each(["responses", "chat"])("Bash %s 範例保留 JSON 與續行語法", (endpoint) => {
    const example = buildApiExample("bash", "https://api.example.edu", endpoint);
    const [headers, body] = example.split("  -d '");
    expect(headers.split("\n").slice(0, -1).every((line) => line.endsWith("\\"))).toBe(true);
    expect(example).not.toContain("^");
    expect(body.endsWith("'")).toBe(true);
    const payload = JSON.parse(body.slice(0, -1));
    expect(payload).toEqual(endpoint === "chat"
      ? { model: "MODEL_NAME", messages: [{ role: "user", content: "INPUT" }] }
      : { model: "MODEL_NAME", input: "INPUT" });
  });

  test("查模型的指令指向 /models 並帶上金鑰", () => {
    expect(buildModelsCommand("https://api.example.edu"))
      .toBe('curl "https://api.example.edu/api/v1/ai-proxy/models" -H "Authorization: Bearer YOUR_API_KEY"');
  });
});

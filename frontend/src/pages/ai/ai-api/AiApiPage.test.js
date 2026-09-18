import { describe, expect, test } from "vitest";

import { buildAiProxyBaseUrl, buildApiExample } from "./AiApiPage";

describe("AI API 文件", () => {
  test("從公開根網址建立可直接給 OpenAI SDK 使用的 Base URL", () => {
    expect(buildAiProxyBaseUrl("https://api.example.edu/"))
      .toBe("https://api.example.edu/api/v1/ai-proxy");
    expect(buildAiProxyBaseUrl("https://api.example.edu/api/v1"))
      .toBe("https://api.example.edu/api/v1/ai-proxy");
    expect(buildAiProxyBaseUrl("https://api.example.edu/api/v1/ai-proxy"))
      .toBe("https://api.example.edu/api/v1/ai-proxy");
  });

  test.each(["javascript", "python", "cmd"])(
    "%s 範例包含實際端點與必要替換值",
    (language) => {
      const example = buildApiExample(language, "https://api.example.edu");

      expect(example).toContain("https://api.example.edu/api/v1/ai-proxy");
      expect(example).toContain("YOUR_API_KEY");
      expect(example).toContain("MODEL_NAME");
      expect(example).toContain("INPUT");
    },
  );
});

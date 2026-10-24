import { afterEach, expect, test, vi } from "vitest";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.resetModules();
});

async function downloadUrl(apiUrl, override = "") {
  vi.stubEnv("VITE_API_URL", apiUrl);
  vi.stubEnv("VITE_DESKTOP_CLIENT_DOWNLOAD_URL", override);
  return (await import("./desktopClient.js")).DESKTOP_CLIENT_DOWNLOAD_URL;
}

test("same-origin deployments use the backend download setting", async () => {
  expect(await downloadUrl("")).toBe("/api/v1/desktop-client/download");
});

test("split-origin deployments download through their configured API", async () => {
  expect(await downloadUrl("https://api.example.org/")).toBe(
    "https://api.example.org/api/v1/desktop-client/download",
  );
});

test("an explicit installer URL overrides the backend endpoint", async () => {
  const custom = "https://downloads.example.org/SkyLab-Connect-Setup.exe";
  expect(await downloadUrl("https://api.example.org", custom)).toBe(custom);
});

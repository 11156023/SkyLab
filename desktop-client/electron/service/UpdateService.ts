import { app, net } from "electron";

const LATEST_RELEASE_API =
  "https://api.github.com/repos/1Ray0/SkyLab-Connect-Releases/releases/latest";
const SETUP_ASSET_NAME = "SkyLab-Connect-Setup.exe";

type GitHubRelease = {
  tag_name?: string;
  html_url?: string;
  assets?: Array<{ name?: string; browser_download_url?: string }>;
};

type ParsedVersion = {
  core: number[];
  prerelease: Array<number | string>;
};

function parseVersion(value: string): ParsedVersion | null {
  const normalized = value.replace(/^v/i, "").split("+", 1)[0];
  const [coreValue, prereleaseValue = ""] = normalized.split("-", 2);
  if (!/^\d+(?:\.\d+){0,3}$/.test(coreValue)) return null;
  return {
    core: coreValue.split(".").map(Number),
    prerelease: prereleaseValue
      ? prereleaseValue
          .split(".")
          .map(part => (/^\d+$/.test(part) ? Number(part) : part))
      : []
  };
}

function isNewerVersion(candidate: string, current: string): boolean {
  const next = parseVersion(candidate);
  const installed = parseVersion(current);
  if (!next || !installed) return false;
  const length = Math.max(next.core.length, installed.core.length);
  for (let index = 0; index < length; index += 1) {
    const difference = (next.core[index] || 0) - (installed.core[index] || 0);
    if (difference !== 0) return difference > 0;
  }
  if (!next.prerelease.length || !installed.prerelease.length) {
    return !next.prerelease.length && !!installed.prerelease.length;
  }
  const prereleaseLength = Math.max(
    next.prerelease.length,
    installed.prerelease.length
  );
  for (let index = 0; index < prereleaseLength; index += 1) {
    const left = next.prerelease[index];
    const right = installed.prerelease[index];
    if (left === undefined || right === undefined) return right === undefined;
    if (left === right) continue;
    if (typeof left === "number" && typeof right === "number") {
      return left > right;
    }
    if (typeof left === "number") return false;
    if (typeof right === "number") return true;
    return left.localeCompare(right) > 0;
  }
  return false;
}

class UpdateService {
  async check(): Promise<SkyLabUpdateInfo> {
    const currentVersion = app.getVersion();
    const release = await this.fetchLatestRelease();
    const latestVersion = String(release.tag_name || "").replace(/^v/i, "");
    const setup = release.assets?.find(
      asset => asset.name === SETUP_ASSET_NAME
    );
    const downloadUrl = setup?.browser_download_url || release.html_url || "";
    return {
      currentVersion,
      latestVersion,
      updateAvailable:
        !!downloadUrl && isNewerVersion(latestVersion, currentVersion),
      downloadUrl
    };
  }

  private fetchLatestRelease(): Promise<GitHubRelease> {
    return new Promise((resolve, reject) => {
      const request = net.request({ method: "GET", url: LATEST_RELEASE_API });
      request.setHeader("Accept", "application/vnd.github+json");
      request.setHeader("User-Agent", `SkyLab-Connect/${app.getVersion()}`);
      const timeout = setTimeout(() => {
        request.abort();
        reject(new Error("Update check timed out"));
      }, 10_000);
      request.on("response", response => {
        const chunks: Buffer[] = [];
        response.on("data", chunk => chunks.push(chunk));
        response.on("end", () => {
          clearTimeout(timeout);
          const body = Buffer.concat(chunks).toString("utf-8");
          if (response.statusCode !== 200) {
            reject(new Error(`Update check failed (${response.statusCode})`));
            return;
          }
          try {
            resolve(JSON.parse(body) as GitHubRelease);
          } catch {
            reject(new Error("Update service returned invalid JSON"));
          }
        });
        response.on("error", error => {
          clearTimeout(timeout);
          reject(error);
        });
      });
      request.on("error", error => {
        clearTimeout(timeout);
        reject(error);
      });
      request.end();
    });
  }
}

export { isNewerVersion };
export default UpdateService;

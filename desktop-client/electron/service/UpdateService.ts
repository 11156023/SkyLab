import { app, net, shell } from "electron";
import { createHash } from "node:crypto";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const RELEASES_API =
  "https://api.github.com/repos/1Ray0/SkyLab-Connect-Releases/releases?per_page=30";
const SETUP_ASSET_NAME = "SkyLab-Connect-Setup.exe";
const MAX_INSTALLER_SIZE = 512 * 1024 * 1024;
const DOWNLOAD_HOSTS = new Set([
  "github.com",
  "release-assets.githubusercontent.com",
  "objects.githubusercontent.com"
]);

type GitHubRelease = {
  tag_name?: string;
  html_url?: string;
  draft?: boolean;
  assets?: GitHubAsset[];
};

type GitHubAsset = {
  name?: string;
  browser_download_url?: string;
  digest?: string | null;
  size?: number;
  state?: string;
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

function validDownloadUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "https:" && DOWNLOAD_HOSTS.has(url.hostname);
  } catch {
    return false;
  }
}

function setupAsset(release: GitHubRelease): GitHubAsset | undefined {
  return release.assets?.find(
    asset =>
      asset.name === SETUP_ASSET_NAME &&
      asset.state === "uploaded" &&
      Number.isSafeInteger(asset.size) &&
      Number(asset.size) > 0 &&
      Number(asset.size) <= MAX_INSTALLER_SIZE &&
      /^sha256:[a-f\d]{64}$/i.test(asset.digest || "") &&
      validDownloadUrl(asset.browser_download_url || "")
  );
}

function selectNewestInstallableRelease(
  releases: GitHubRelease[]
): GitHubRelease | null {
  let selected: GitHubRelease | null = null;
  for (const release of releases) {
    if (release.draft || !release.tag_name || !parseVersion(release.tag_name))
      continue;
    if (!setupAsset(release)) continue;
    if (!selected || isNewerVersion(release.tag_name, selected.tag_name || ""))
      selected = release;
  }
  return selected;
}

class UpdateService {
  private installing = false;

  async check(): Promise<SkyLabUpdateInfo> {
    const currentVersion = app.getVersion();
    const release = selectNewestInstallableRelease(await this.fetchReleases());
    const latestVersion = String(release?.tag_name || currentVersion).replace(
      /^v/i,
      ""
    );
    const downloadUrl = release
      ? setupAsset(release)?.browser_download_url || ""
      : "";
    return {
      currentVersion,
      latestVersion,
      updateAvailable:
        !!release && isNewerVersion(latestVersion, currentVersion),
      downloadUrl
    };
  }

  async install(
    progress: (status: SkyLabUpdateProgress) => void
  ): Promise<void> {
    if (this.installing) throw new Error("An update is already in progress");
    this.installing = true;
    let directory = "";
    try {
      const release = selectNewestInstallableRelease(
        await this.fetchReleases()
      );
      const asset = release && setupAsset(release);
      if (
        !release ||
        !asset?.browser_download_url ||
        !asset.digest ||
        !asset.size ||
        !isNewerVersion(release.tag_name || "", app.getVersion())
      ) {
        throw new Error("No newer verified installer is available");
      }
      directory = await fs.mkdtemp(join(tmpdir(), "SkyLab-Connect-update-"));
      const installer = join(directory, SETUP_ASSET_NAME);
      progress({ stage: "downloading", received: 0, total: asset.size });
      await this.downloadInstaller(asset, installer, progress);
      progress({ stage: "launching", received: asset.size, total: asset.size });
      const launchError = await shell.openPath(installer);
      if (launchError)
        throw new Error(`Could not open installer: ${launchError}`);
    } catch (error) {
      if (directory)
        await fs
          .rm(directory, { recursive: true, force: true })
          .catch(() => {});
      throw error;
    } finally {
      this.installing = false;
    }
  }

  private downloadInstaller(
    asset: GitHubAsset,
    installer: string,
    progress: (status: SkyLabUpdateProgress) => void
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const size = asset.size as number;
      const digest = asset.digest as string;
      const request = net.request({
        method: "GET",
        url: asset.browser_download_url as string,
        redirect: "manual"
      });
      request.setHeader("User-Agent", `SkyLab-Connect/${app.getVersion()}`);
      let redirects = 0;
      let settled = false;
      const timeout = setTimeout(() => {
        fail(new Error("Update download timed out"));
        request.abort();
      }, 30 * 60_000);
      const fail = (error: Error) => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        reject(error);
      };
      const finish = () => {
        if (settled) return;
        settled = true;
        clearTimeout(timeout);
        resolve();
      };
      request.on("redirect", (_status, _method, redirectUrl) => {
        redirects += 1;
        if (redirects > 5 || !validDownloadUrl(redirectUrl)) {
          fail(new Error("Update download redirected to an untrusted host"));
          request.abort();
          return;
        }
        // Electron requires this call synchronously inside the redirect event.
        request.followRedirect();
      });
      request.on("response", response => {
        if (response.statusCode !== 200) {
          fail(new Error(`Update download failed (${response.statusCode})`));
          request.abort();
          return;
        }
        const contentLength = Number(response.headers["content-length"]);
        if (contentLength && contentLength !== size) {
          fail(new Error("Installer size differs from the release metadata"));
          request.abort();
          return;
        }
        void (async () => {
          const file = await fs.open(installer, "wx");
          const hash = createHash("sha256");
          let received = 0;
          let lastProgressAt = 0;
          try {
            for await (const chunk of response as unknown as AsyncIterable<Buffer>) {
              const data = Buffer.from(chunk);
              received += data.byteLength;
              if (received > size)
                throw new Error("Installer exceeds expected size");
              hash.update(data);
              await file.writeFile(data);
              if (Date.now() - lastProgressAt > 200) {
                progress({ stage: "downloading", received, total: size });
                lastProgressAt = Date.now();
              }
            }
          } finally {
            await file.close();
          }
          progress({ stage: "verifying", received, total: size });
          if (
            received !== size ||
            hash.digest("hex").toLowerCase() !== digest.slice(7).toLowerCase()
          ) {
            throw new Error("Installer integrity check failed");
          }
        })().then(finish, error => {
          fail(error as Error);
          request.abort();
        });
      });
      request.on("error", fail);
      request.end();
    });
  }

  private fetchReleases(): Promise<GitHubRelease[]> {
    return new Promise((resolve, reject) => {
      const request = net.request({ method: "GET", url: RELEASES_API });
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
            const releases = JSON.parse(body);
            if (!Array.isArray(releases))
              throw new Error("Invalid release list");
            resolve(releases as GitHubRelease[]);
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

export { isNewerVersion, selectNewestInstallableRelease };
export default UpdateService;

import { spawnSync } from "node:child_process";

const severityRank = {
  info: 0,
  low: 1,
  moderate: 2,
  high: 3,
  critical: 4
};

// No patched release exists yet. This dependency is only used by
// electron-builder while downloading build artifacts, not by the shipped app.
const temporaryAllowlist = new Set(["GHSA-ch52-4w7c-c8xp"]);

const npmCli = process.env.npm_execpath;
const command = npmCli ? process.execPath : "npm";
const args = npmCli ? [npmCli, "audit", "--json"] : ["audit", "--json"];
const result = spawnSync(command, args, {
  cwd: process.cwd(),
  encoding: "utf8"
});

if (result.error) {
  console.error(`Unable to run npm audit: ${result.error.message}`);
  process.exit(1);
}

let report;
try {
  report = JSON.parse(result.stdout);
} catch {
  console.error(
    result.stderr || result.stdout || "npm audit returned no report."
  );
  process.exit(1);
}

const vulnerabilities = report.vulnerabilities ?? {};

function advisoryId(advisory) {
  const match = advisory.url?.match(/GHSA-[\w-]+$/);
  return match?.[0] ?? String(advisory.source);
}

function advisoriesFor(name, visited = new Set()) {
  if (visited.has(name)) return new Set();

  const vulnerability = vulnerabilities[name];
  if (!vulnerability) return new Set();

  visited.add(name);
  const advisories = new Set();
  for (const cause of vulnerability.via) {
    if (typeof cause === "string") {
      for (const id of advisoriesFor(cause, visited)) advisories.add(id);
    } else {
      advisories.add(advisoryId(cause));
    }
  }
  return advisories;
}

function isAllowed(name) {
  const advisories = advisoriesFor(name);
  return (
    advisories.size > 0 &&
    [...advisories].every(id => temporaryAllowlist.has(id))
  );
}

const blocking = Object.values(vulnerabilities).filter(
  vulnerability =>
    severityRank[vulnerability.severity] >= severityRank.high &&
    !isAllowed(vulnerability.name)
);

if (blocking.length > 0) {
  console.error("npm audit found unapproved high or critical vulnerabilities:");
  for (const vulnerability of blocking) {
    console.error(`- ${vulnerability.name}: ${vulnerability.severity}`);
  }
  process.exit(1);
}

const allowed = Object.values(vulnerabilities).filter(vulnerability =>
  isAllowed(vulnerability.name)
);

if (allowed.length > 0) {
  console.warn(
    `Temporarily allowed ${temporaryAllowlist.size} unpatched build-time advisory: ${[
      ...temporaryAllowlist
    ].join(", ")}`
  );
}

console.log("npm audit found no unapproved high or critical vulnerabilities.");

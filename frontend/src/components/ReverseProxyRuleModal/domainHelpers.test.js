import { describe, expect, test } from "vitest";
import { COMMON_PORTS, extractHostnamePrefix, findZoneByDomain } from "./domainHelpers";

describe("findZoneByDomain", () => {
  const zones = [
    { id: "a", name: "example.com" },
    { id: "b", name: "lab.example.com" },
  ];

  test("prefers the longest (most specific) matching zone", () => {
    expect(findZoneByDomain("app.lab.example.com", zones)?.id).toBe("b");
    expect(findZoneByDomain("app.example.com", zones)?.id).toBe("a");
  });

  test("matches the apex domain itself", () => {
    expect(findZoneByDomain("lab.example.com", zones)?.id).toBe("b");
  });

  test("does not match a mere string suffix without a dot boundary", () => {
    expect(findZoneByDomain("badexample.com", zones)).toBeUndefined();
  });

  test("does not reorder the caller's array", () => {
    findZoneByDomain("x.example.com", zones);
    expect(zones.map((z) => z.id)).toEqual(["a", "b"]);
  });

  test("defaults to no zones", () => {
    expect(findZoneByDomain("x.example.com")).toBeUndefined();
  });
});

describe("extractHostnamePrefix", () => {
  test("strips the zone suffix", () => {
    expect(extractHostnamePrefix("app.lab.example.com", "example.com")).toBe("app.lab");
  });

  test("returns empty string for the apex", () => {
    expect(extractHostnamePrefix("example.com", "example.com")).toBe("");
  });

  test("returns the domain unchanged when it is outside the zone", () => {
    expect(extractHostnamePrefix("other.org", "example.com")).toBe("other.org");
  });
});

test("COMMON_PORTS starts with 80 and every entry carries a label key", () => {
  expect(COMMON_PORTS[0].value).toBe("80");
  for (const port of COMMON_PORTS) expect(port.labelKey).toMatch(/^ReverseProxyRuleModal\.port\d+$/);
});

import { expect, test, vi } from "vitest";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../services/quotas", () => ({ QuotasService: {} }));

import QuotaUsageBar from "./QuotaUsageBar";

test("QuotaUsageBar is exported from components/QuotaUsageBar", () => {
  expect(typeof QuotaUsageBar).toBe("function");
});

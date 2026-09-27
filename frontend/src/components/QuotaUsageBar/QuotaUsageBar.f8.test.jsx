import { expect, test, vi } from "vitest";

vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key) => key }) }));
vi.mock("../../services/quotas", () => ({ QuotasService: {} }));

import QuotaUsageBar from "./QuotaUsageBar";
import LegacyQuotaUsageBar from "../Teaching/QuotaUsageBar";

test("the old components/Teaching path re-exports the moved component", () => {
  expect(typeof QuotaUsageBar).toBe("function");
  expect(LegacyQuotaUsageBar).toBe(QuotaUsageBar);
});

// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, test } from "vitest";
import ResetPasswordRedirect, {
  buildResetRedirectTarget,
  hasResetToken,
} from "./ResetPasswordRedirect";

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let host;
let root;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  document.body.innerHTML = "";
});

function LoginProbe() {
  const location = useLocation();
  return <div id="probe">{`${location.pathname}${location.search}`}</div>;
}

test("信件連結 /reset-password?token= 導到 /login 時保留 token", async () => {
  await act(async () =>
    root.render(
      <MemoryRouter initialEntries={["/reset-password?token=abc.def"]}>
        <Routes>
          <Route path="/reset-password" element={<ResetPasswordRedirect />} />
          <Route path="/login" element={<LoginProbe />} />
        </Routes>
      </MemoryRouter>,
    ),
  );
  expect(document.getElementById("probe")?.textContent).toBe("/login?token=abc.def");
});

test("buildResetRedirectTarget 保留查詢字串", () => {
  expect(buildResetRedirectTarget("?token=x")).toEqual({ pathname: "/login", search: "?token=x" });
  expect(buildResetRedirectTarget(undefined)).toEqual({ pathname: "/login", search: "" });
});

test("hasResetToken 判斷查詢字串是否帶 token", () => {
  expect(hasResetToken("?token=x")).toBe(true);
  expect(hasResetToken("?device_code=y")).toBe(false);
  expect(hasResetToken("?token=")).toBe(false);
  expect(hasResetToken("")).toBe(false);
});

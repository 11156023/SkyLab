import { expect, test } from "vitest";
import appSource from "../../App.jsx?raw";

/* 重設密碼信的連結是 {FRONTEND_HOST}/reset-password?token=...（backend/app/utils/email.py）。
   App.jsx 若沒註冊這條路由，未登入者會被 path="*" 導到 /login 並丟掉查詢字串，
   已登入者則落到 NotFoundPage，token 永遠到不了 LoginPage。這裡直接檢查真正的路由表。 */

test("App.jsx 在登入分支之前註冊 /reset-password 並交給 ResetPasswordRedirect", () => {
  const routeMatch = appSource.match(
    /<Route\s+path="\/reset-password"\s+element=\{\s*<ResetPasswordRedirect\s*\/>\s*\}/,
  );
  expect(routeMatch).not.toBeNull();

  // 必須放在 `{user ? (` 分支之前，否則未登入時會先被 path="*" 攔下
  const userBranch = appSource.indexOf("{user ? (");
  expect(userBranch).toBeGreaterThan(-1);
  expect(routeMatch.index).toBeLessThan(userBranch);

  expect(appSource).toMatch(
    /import\s+ResetPasswordRedirect\s*,\s*\{\s*hasResetToken\s*\}\s+from\s+"\.\/pages\/login\/ResetPasswordRedirect"/,
  );
});

test("已登入者帶 ?token= 開 /login 時不會被導去 /dashboard", () => {
  const loginRoute = appSource.match(/path="\/login"[\s\S]*?<LoginRoute\s*\/>/);
  expect(loginRoute).not.toBeNull();
  expect(loginRoute[0]).toMatch(/!hasResetToken\(window\.location\.search\)/);
});

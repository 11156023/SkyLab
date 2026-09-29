import { Navigate, useLocation } from "react-router-dom";

/** 查詢字串是否帶重設密碼 token（信件連結 /reset-password?token=...） */
export function hasResetToken(search) {
  return Boolean(new URLSearchParams(search ?? "").get("token"));
}

/** 重設密碼信的連結是 /reset-password?token=...；導到 /login 時必須保留查詢字串，
    否則 LoginPage 讀不到 token，只會顯示一般登入表單。 */
export function buildResetRedirectTarget(search) {
  return { pathname: "/login", search: search ?? "" };
}

export default function ResetPasswordRedirect() {
  const { search } = useLocation();
  return <Navigate to={buildResetRedirectTarget(search)} replace />;
}

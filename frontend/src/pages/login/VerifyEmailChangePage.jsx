import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "../../contexts/AuthContext";
import { AccountService } from "../../services/account";
import PageShell from "./PageShell";

export default function VerifyEmailChangePage() {
  const { t } = useTranslation("personal");
  const { user } = useAuth();
  const [status, setStatus] = useState("checking");

  useEffect(() => {
    const url = new URL(window.location.href);
    const token = url.searchParams.get("token");
    url.searchParams.delete("token");
    window.history.replaceState(window.history.state, "", url);
    if (!token) {
      setStatus("error");
      return;
    }
    AccountService.confirmEmailChange(token)
      .then(() => setStatus("success"))
      .catch(() => setStatus("error"));
  }, []);

  return (
    <PageShell>
      <h1>{t("ProfileTab.emailVerifyTitle")}</h1>
      <p role="status">
        {status === "checking"
          ? t("ProfileTab.emailVerifying")
          : status === "success"
            ? t("ProfileTab.emailVerified")
            : t("ProfileTab.emailVerifyFailed")}
      </p>
      <a href={user ? "/account" : "/login"}>{t("ProfileTab.emailVerifyContinue")}</a>
    </PageShell>
  );
}

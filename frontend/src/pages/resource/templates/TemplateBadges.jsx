import { useTranslation } from "react-i18next";
import styles from "./TemplatesPage.module.scss";

export function useTemplateStatusLabel() {
  const { t } = useTranslation("resource");
  return {
    creating: t("TemplateBadges.statusCreating"),
    ready: t("TemplateBadges.statusReady"),
    updating: t("TemplateBadges.statusUpdating"),
    failed: t("TemplateBadges.statusFailed"),
    deleted: t("TemplateBadges.statusDeleted"),
  };
}

const TEMPLATE_STATUS_CLASS = {
  creating: "badge_info",
  ready: "badge_success",
  updating: "badge_info",
  failed: "badge_danger",
  deleted: "badge_muted",
};

export function TemplateStatusBadge({ status }) {
  const templateStatusLabel = useTemplateStatusLabel();
  return (
    <span className={`${styles.badge} ${styles[TEMPLATE_STATUS_CLASS[status] ?? "badge_muted"]}`}>
      {templateStatusLabel[status] ?? status}
    </span>
  );
}


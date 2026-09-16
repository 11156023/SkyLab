/**
 * MachineKindBadge
 * 機器來源徽章：個人申請／共享給我／班級機器／學生機器／快速練習／課程實驗／老師開放。
 * 我的資源、資源管理、資源詳情、防火牆拓撲共用同一個元件，看到同一種顏色就是同一種機器。
 *
 * props
 * - kind            後端 machine_kind（或 teacher_open）
 * - classRelation   班級機：student / teacher
 * - ownerName       不是自己的機器時的擁有者（共享、學生機器、老師開放會顯示）
 * - teachingClassName  班級名稱，只進 tooltip
 * - size            "md"（預設）| "sm"（表格次行、拓撲節點）
 * - solid           實心版（拓撲節點掛在畫布上要夠醒目）
 * - readOnly        另掛鎖頭，表示看得到但不能改
 * - showOwner       強制顯示擁有者（管理員清單每台都標）
 * - title           覆寫 tooltip
 */

import { useTranslation } from "react-i18next";
import MIcon from "../MIcon";
import { KIND_META, resolveKind } from "./machineKind";
import styles from "./MachineKindBadge.module.scss";

export default function MachineKindBadge({
  kind,
  classRelation = null,
  ownerName = null,
  teachingClassName = null,
  size = "md",
  solid = false,
  readOnly = false,
  showOwner,
  title,
  className = "",
}) {
  const { t } = useTranslation("components");
  const key = resolveKind({ kind, classRelation });
  const meta = KIND_META[key];
  const owner = (showOwner ?? meta.showOwner) ? ownerName : null;
  const hint = title ?? t(meta.hintKey, { owner: ownerName ?? "", cls: teachingClassName ?? "" });
  const classes = [
    styles.badge,
    styles[meta.variant],
    size === "sm" ? styles.sm : "",
    solid ? styles.solid : "",
    className,
  ].filter(Boolean).join(" ");

  return (
    <span className={classes} title={hint}>
      <MIcon name={meta.icon} size={size === "sm" ? 11 : 13} />
      <span className={styles.label}>{t(meta.labelKey)}</span>
      {owner && <span className={styles.owner}>· {owner}</span>}
      {readOnly && <MIcon name="lock" size={size === "sm" ? 10 : 12} className={styles.lock} />}
    </span>
  );
}

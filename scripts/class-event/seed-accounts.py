#!/usr/bin/env python3
"""在 backend 容器內直接建立一次課程活動的導師與學生帳號。

用系統自己的 User model 與密碼雜湊（pwdlib Argon2）寫入資料庫，建出來的
帳號和從 API 建的完全相同，但不會寄出含密碼的開通信。已存在的帳號一律沿用、
不改密碼；角色不符時直接失敗，不去動別人的帳號。

在 runner 上以 stdin 傳進容器執行（不必重打 image）：

    docker exec -i -e STUDENT_COUNT -e STUDENT_EMAIL_PATTERN ... <backend> \
        python - < scripts/class-event/seed-accounts.py

所有設定都走環境變數，密碼不會出現在指令列或 log：

- STUDENT_COUNT             學生人數（預設 50）
- STUDENT_EMAIL_PATTERN     例如 ``student{n:03d}@gmail.com``
- STUDENT_PASSWORD_PATTERN  例如 ``ntubstudent{n:03d}@!``（放 GitHub Secret）
- TEACHER_EMAIL             例如 ``teacher001@gmail.com``
- TEACHER_PASSWORD          導師密碼（放 GitHub Secret）
- DRY_RUN                   ``true`` 時只列出會建立哪些帳號
"""

from __future__ import annotations

import os
import sys

from pydantic import ValidationError
from sqlmodel import Session

from app.core.db import engine
from app.models import UserRole
from app.repositories import user as user_repo
from app.schemas import UserCreate
from app.services.user import audit_service


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if value is None or not value.strip():
        sys.exit(f"缺少環境變數 {name}")
    return value.strip()


def _planned_accounts() -> list[tuple[str, str, UserRole, str]]:
    count = int(_env("STUDENT_COUNT", "50"))
    if not 1 <= count <= 500:
        sys.exit(f"STUDENT_COUNT 必須介於 1～500，收到 {count}")
    email_pattern = _env("STUDENT_EMAIL_PATTERN")
    password_pattern = _env("STUDENT_PASSWORD_PATTERN")
    accounts = [
        (
            _env("TEACHER_EMAIL").lower(),
            _env("TEACHER_PASSWORD"),
            UserRole.teacher,
            "導師",
        )
    ]
    for n in range(1, count + 1):
        accounts.append(
            (
                email_pattern.format(n=n).lower(),
                password_pattern.format(n=n),
                UserRole.student,
                f"學生 {n:03d}",
            )
        )
    emails = [email for email, *_ in accounts]
    if len(set(emails)) != len(emails):
        sys.exit("帳號樣板產生了重複的 email，請確認 STUDENT_EMAIL_PATTERN 含 {n}")
    return accounts


def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() == "true"
    accounts = _planned_accounts()
    created = existing = 0
    conflicts: list[str] = []

    with Session(engine) as session:
        for email, password, role, full_name in accounts:
            user = user_repo.get_user_by_email(session=session, email=email)
            if user is not None:
                if user.role != role:
                    conflicts.append(f"{email}（現有角色 {user.role.value}，需要 {role.value}）")
                else:
                    existing += 1
                continue
            try:
                user_in = UserCreate(
                    email=email, password=password, role=role, full_name=full_name
                )
            except ValidationError as exc:
                sys.exit(f"{email} 不符合帳號格式：{exc.errors()[0]['msg']}")
            if dry_run:
                print(f"[dry-run] 會建立 {role.value:<7} {email}")
                created += 1
                continue
            user = user_repo.create_user(session=session, user_create=user_in)
            audit_service.log_action(
                session=session,
                user_id=None,
                action="user_create",
                details=f"Created user: {email}, role: {role.value} (class-event seed)",
                commit=False,
            )
            session.commit()
            created += 1

    verb = "會建立" if dry_run else "已建立"
    print(f"帳號：{verb} {created} 個，沿用既有 {existing} 個，共 {len(accounts)} 個")
    if conflicts:
        print("以下帳號已存在但角色不符，未做任何修改：", file=sys.stderr)
        for line in conflicts:
            print(f"  - {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

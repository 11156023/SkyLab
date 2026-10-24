"""Roster CSV parsing shared by class rosters and user groups.

One account per row in the first column: an e-mail, or a 學號 that becomes
``<學號>@<student e-mail domain>`` (the administrator sets the domain in the
registration settings). An optional header row (email／學號／帳號) is skipped.
"""

import csv
import io

from fastapi import UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlmodel import Session

from app.core.i18n import t
from app.exceptions import BadRequestError
from app.services.user import registration_policy_service

# 一行一個帳號，1 MB 已足夠放上萬筆
MAX_ROSTER_CSV_BYTES = 1024 * 1024
_HEADER_VALUES = {"email", "學號", "帳號"}
# 錯誤訊息裡最多列出幾個沒有網域可補的學號
_MAX_LISTED_STUDENT_IDS = 5


def student_email_domain(session: Session) -> str | None:
    return registration_policy_service.get_policy(session).student_email_domain


async def read_roster_upload(file: UploadFile, session: Session) -> list[str]:
    try:
        raw = await file.read(MAX_ROSTER_CSV_BYTES + 1)
    finally:
        await file.close()
    if len(raw) > MAX_ROSTER_CSV_BYTES:
        raise BadRequestError(
            t(
                "teachingClasses.csvTooLarge",
                size=MAX_ROSTER_CSV_BYTES // (1024 * 1024),
            )
        )
    # 設定查詢是同步 DB 工作，不放在 event loop 上跑
    domain = await run_in_threadpool(student_email_domain, session)
    return parse_roster_csv(raw, student_email_domain=domain)


def parse_roster_csv(raw: bytes, *, student_email_domain: str | None) -> list[str]:
    content = None
    # UTF-8 要先試：Excel「CSV UTF-8」開頭的 BOM 加一個英文字母剛好是兩個
    # 合法的 Big5 字，先用 cp950 解不會報錯，第一格就被解成亂碼（標題列
    # 沒被跳過、或第一位學生被默默漏掉）。真正的 Big5 位元組幾乎一定不是
    # 合法的 UTF-8，所以舊的 cp950 檔案仍會落到第二順位解出來。
    for encoding in ("utf-8-sig", "cp950"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if content is None:
        raise BadRequestError(t("teachingClasses.csvDecodeFailed"))
    emails = []
    bare_ids = []
    for index, row in enumerate(csv.reader(io.StringIO(content))):
        if not row:
            continue
        value = row[0].strip().lstrip("﻿").strip()
        if index == 0 and value.lower() in _HEADER_VALUES:
            continue
        if not value:
            continue
        if "@" in value:
            emails.append(value)
        elif student_email_domain:
            emails.append(f"{value}@{student_email_domain}")
        else:
            bare_ids.append(value)
    # 沒設定網域就不猜：整份退回，讓老師改填完整信箱或請管理員設定網域
    if bare_ids:
        raise BadRequestError(
            t(
                "teachingClasses.csvStudentIdNeedsDomain",
                ids=", ".join(bare_ids[:_MAX_LISTED_STUDENT_IDS]),
                count=len(bare_ids),
            )
        )
    return emails

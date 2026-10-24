"""Roster CSV parsing shared by class rosters and user groups.

One account per row in the first column: an e-mail, or a 學號 that becomes
``<學號>@ntub.edu.tw``. An optional header row (email／學號／帳號) is skipped.
"""

import csv
import io

from fastapi import UploadFile

from app.core.i18n import t
from app.exceptions import BadRequestError

# 一行一個帳號，1 MB 已足夠放上萬筆
MAX_ROSTER_CSV_BYTES = 1024 * 1024
_HEADER_VALUES = {"email", "學號", "帳號"}
_STUDENT_ID_DOMAIN = "ntub.edu.tw"


async def read_roster_upload(file: UploadFile) -> list[str]:
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
    return parse_roster_csv(raw)


def parse_roster_csv(raw: bytes) -> list[str]:
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
    for index, row in enumerate(csv.reader(io.StringIO(content))):
        if not row:
            continue
        value = row[0].strip().lstrip("﻿").strip()
        if index == 0 and value.lower() in _HEADER_VALUES:
            continue
        if value:
            emails.append(value if "@" in value else f"{value}@{_STUDENT_ID_DOMAIN}")
    return emails

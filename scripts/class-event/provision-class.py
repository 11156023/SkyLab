#!/usr/bin/env python3
"""一次課程活動的建班流程：課程環境 → 班級 → 建機 → 審核 → 確認整組開機。

帳號由 seed-accounts.py 先在容器內建好；這支只走 API，所以權限檢查、容量
預留、建機佇列與排程都和老師在網頁上操作完全相同。只用標準庫，可以直接在
runner 上執行。每一步都先看現況再決定要不要做，失敗後直接重跑就會從中斷的
地方接著走。

時段設計（見 class_provision_service.recurrence_rule）：班級每週一個
00:00–23:59 的時段，建機完成後排程器在一分鐘內把目前時段的機器開起來；
時段外機器保留、學生可自行開機；班級到期時系統自動封存並回收機器。

設定全部走環境變數（密碼只從 GitHub Secret 帶入）：

- SKYLAB_API_URL              預設 http://127.0.0.1:8000/api/v1
- SKYLAB_ADMIN_EMAIL / SKYLAB_ADMIN_PASSWORD / SKYLAB_ADMIN_TOTP_SECRET（選填）
- TEACHER_EMAIL / TEACHER_PASSWORD
- STUDENT_COUNT / STUDENT_EMAIL_PATTERN
- CLASS_NAME / CLASS_TERM
- START_DATE（預設今天，Asia/Taipei）/ END_DATE（預設開始日加兩個月）
- LXC_IMAGE（PVE 上的 LXC 映像：完整 volid 如 local:vztmpl/debian-12-...，或檔名關鍵字
  如 ubuntu-24.04；空白自動挑，優先 Ubuntu、其次 Debian 的最新版）
- LXC_CPU / LXC_MEMORY_MB / LXC_DISK_GB
- PROVISION_TIMEOUT_MINUTES（預設 90）/ BOOT_TIMEOUT_MINUTES（預設 10）
- DRY_RUN                     ``true`` 時只登入管理員、挑範本、印出計畫
"""

from __future__ import annotations

import base64
import calendar
import hmac
import json
import os
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import sha1
from zoneinfo import ZoneInfo

TIMEZONE = "Asia/Taipei"
NODE_KEYS = ("lxc1", "lxc2")
# LXC_IMAGE 空白時依序找這些系列，同系列取檔名排序最後（版本最新）的一份
PREFERRED_IMAGE_PREFIXES = ("ubuntu-", "debian-")
# 後端批次 API 在同一個請求裡逐台開機，小批送才不會撞到代理逾時
POWER_CHUNK_SIZE = 10
POLL_SECONDS = 30


class ScriptError(Exception):
    """可預期的失敗：訊息直接印給操作者看，不印 traceback。"""


class ApiError(ScriptError):
    def __init__(self, method: str, path: str, status: int, detail: str):
        super().__init__(f"{method} {path} → HTTP {status}：{detail}")
        self.status = status
        self.detail = detail


# ─── 設定 ─────────────────────────────────────────────────────────────────────


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, "").strip() or default
    if value is None:
        raise ScriptError(f"缺少環境變數 {name}")
    return value


def add_months(value: date, months: int) -> date:
    """加月份；目標月份沒有那一天時落在月底（1/31 + 1 個月 → 2/28）。"""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


@dataclass(frozen=True)
class Config:
    api_url: str
    admin_email: str
    admin_password: str
    admin_totp_secret: str | None
    teacher_email: str
    teacher_password: str | None
    student_emails: list[str]
    class_name: str
    class_term: str
    start_date: date
    end_date: date
    lxc_image: str | None
    lxc_cpu: int
    lxc_memory_mb: int
    lxc_disk_gb: int
    provision_timeout: timedelta
    boot_timeout: timedelta
    dry_run: bool

    @property
    def weekday(self) -> int:
        return self.start_date.weekday()

    @property
    def environment_name(self) -> str:
        return f"{self.class_name} 環境"


def load_config() -> Config:
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() == "true"
    today = datetime.now(ZoneInfo(TIMEZONE)).date()
    start_date = date.fromisoformat(_env("START_DATE", today.isoformat()))
    end_date = date.fromisoformat(_env("END_DATE", add_months(start_date, 2).isoformat()))
    if end_date < start_date:
        raise ScriptError(f"END_DATE {end_date} 早於 START_DATE {start_date}")
    count = int(_env("STUDENT_COUNT", "50"))
    pattern = _env("STUDENT_EMAIL_PATTERN")
    return Config(
        api_url=_env("SKYLAB_API_URL", "http://127.0.0.1:8000/api/v1").rstrip("/"),
        admin_email=_env("SKYLAB_ADMIN_EMAIL"),
        admin_password=_env("SKYLAB_ADMIN_PASSWORD"),
        admin_totp_secret=os.environ.get("SKYLAB_ADMIN_TOTP_SECRET", "").strip() or None,
        teacher_email=_env("TEACHER_EMAIL").lower(),
        # dry-run 不以導師登入，允許先不設密碼
        teacher_password=_env("TEACHER_PASSWORD", "" if dry_run else None) or None,
        student_emails=[pattern.format(n=n).lower() for n in range(1, count + 1)],
        class_name=_env("CLASS_NAME"),
        class_term=_env("CLASS_TERM", "課程活動"),
        start_date=start_date,
        end_date=end_date,
        lxc_image=os.environ.get("LXC_IMAGE", "").strip() or None,
        lxc_cpu=int(_env("LXC_CPU", "1")),
        lxc_memory_mb=int(_env("LXC_MEMORY_MB", "512")),
        lxc_disk_gb=int(_env("LXC_DISK_GB", "8")),
        provision_timeout=timedelta(minutes=int(_env("PROVISION_TIMEOUT_MINUTES", "90"))),
        boot_timeout=timedelta(minutes=int(_env("BOOT_TIMEOUT_MINUTES", "10"))),
        dry_run=dry_run,
    )


# ─── HTTP 與登入 ──────────────────────────────────────────────────────────────


class Api:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: dict | list | None = None,
        form: dict | None = None,
    ):
        headers = {"Accept": "application/json"}
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ApiError(method, path, exc.code, _error_detail(exc.read())) from None
        except urllib.error.URLError as exc:
            raise ScriptError(f"連不到 {self.base_url}：{exc.reason}") from None
        return json.loads(raw) if raw else None


def _error_detail(raw: bytes) -> str:
    try:
        detail = json.loads(raw).get("detail")
    except (ValueError, AttributeError):
        return raw.decode(errors="replace")[:300]
    if isinstance(detail, list):
        return "; ".join(str(item.get("msg", item)) for item in detail)
    return str(detail)


def totp_code(secret: str, now: float | None = None) -> str:
    """RFC 6238（SHA1、6 位數、30 秒），與 app/utils/totp.py 相同參數。"""
    normalized = secret.strip().replace(" ", "").upper()
    key = base64.b32decode(normalized + "=" * (-len(normalized) % 8), casefold=True)
    counter = int((time.time() if now is None else now) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), sha1).digest()
    offset = digest[-1] & 0x0F
    binary = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % 10**6).zfill(6)


def login(api: Api, email: str, password: str, totp_secret: str | None = None) -> str:
    result = api.call(
        "POST", "/login/access-token", form={"username": email, "password": password}
    )
    if result.get("totp_required"):
        if not totp_secret:
            raise ScriptError(f"{email} 已啟用兩步驟驗證，請設定 SKYLAB_ADMIN_TOTP_SECRET")
        result = api.call(
            "POST",
            "/login/totp",
            body={"totp_token": result["totp_token"], "code": totp_code(totp_secret)},
        )
    return result["access_token"]


# ─── 課程環境 ─────────────────────────────────────────────────────────────────


def _image_file(volid: str) -> str:
    return volid.rsplit("/", 1)[-1]


def resolve_lxc_image(api: Api, admin_token: str, wanted: str | None) -> str:
    """挑課程環境要用的 LXC 映像，回傳 PVE volid（local:vztmpl/...）。

    和網頁上課程環境編輯器「來源方式：VM／LXC → LXC → 選映像」同一份清單
    （GET /lxc/templates，跨連線彙總 PVE 上的 vztmpl）。
    """
    images = sorted(
        {item["volid"] for item in api.call("GET", "/lxc/templates", token=admin_token)},
        key=lambda volid: (_image_file(volid), volid),
    )
    listing = "、".join(_image_file(volid) for volid in images) or "（沒有）"
    print(f"可用的 LXC 映像：{listing}")
    if not images:
        raise ScriptError("PVE 上沒有任何 LXC 映像（vztmpl），請先在 PVE storage 下載 CT 範本")
    if wanted:
        matches = [
            volid
            for volid in images
            if volid == wanted or wanted.lower() in _image_file(volid).lower()
        ]
        if not matches:
            raise ScriptError(f"找不到 LXC 映像「{wanted}」；可用映像：{listing}")
        return matches[-1]
    for prefix in PREFERRED_IMAGE_PREFIXES:
        family = [volid for volid in images if _image_file(volid).lower().startswith(prefix)]
        if family:
            return family[-1]
    return images[-1]


def environment_body(cfg: Config, image: str) -> dict:
    nodes = [
        {
            "node_key": key,
            "source_type": "custom",
            "custom_image_ref": image,
            "custom_unprivileged": True,
            "name": f"LXC {index + 1}",
            "role": "lab",
            "resource_type": "lxc",
            "cpu": cfg.lxc_cpu,
            "memory_mb": cfg.lxc_memory_mb,
            "disk_gb": cfg.lxc_disk_gb,
            "network": "lab-net",
            "position_x": 80.0 + index * 280,
            "position_y": 120.0,
        }
        for index, key in enumerate(NODE_KEYS)
    ]
    return {
        "name": cfg.environment_name,
        "description": "每位學生兩台互通的 LXC（由 class-event CI 建立）",
        "usage_scope": "course",
        "nodes": nodes,
        # 兩台之間雙向全通；peer_policy=explicit 只開這條線，其餘隔離
        "edges": [
            {
                "source_node_key": NODE_KEYS[0],
                "target_node_key": NODE_KEYS[1],
                "direction": "bidirectional",
                "protocol": "any",
                "port": None,
            }
        ],
        "publications": [],
        "peer_policy": "explicit",
    }


def ensure_environment(api: Api, token: str, cfg: Config, body: dict) -> str:
    """回傳已發布的課程環境版本 id。"""
    existing = [
        item
        for item in api.call("GET", "/course-environments", token=token)
        if item["name"] == cfg.environment_name
    ]
    if existing:
        environment = existing[0]
        print(f"沿用課程環境「{environment['name']}」v{environment['version']}")
    else:
        environment = api.call("POST", "/course-environments", token=token, body=body)
        print(f"已建立課程環境「{environment['name']}」")
    if environment["status"] != "published":
        environment = api.call(
            "POST", f"/course-environments/{environment['id']}/publish", token=token
        )
        print("課程環境已發布")
    return environment["version_id"]


# ─── 班級 ─────────────────────────────────────────────────────────────────────


def ensure_class(api: Api, token: str, cfg: Config) -> dict:
    for item in api.call("GET", "/teaching-classes", token=token):
        if (
            item["name"] == cfg.class_name
            and item["start_date"] == cfg.start_date.isoformat()
            and item["status"] != "archived"
        ):
            print(f"沿用班級「{item['name']}」（{item['status']}）")
            return api.call("GET", f"/teaching-classes/{item['id']}", token=token)
    created = api.call(
        "POST",
        "/teaching-classes",
        token=token,
        body={
            "name": cfg.class_name,
            "term": cfg.class_term,
            "start_date": cfg.start_date.isoformat(),
            "end_date": cfg.end_date.isoformat(),
            "weekday": cfg.weekday,
            "start_time": "00:00:00",
            "end_time": "23:59:00",
            "timezone": TIMEZONE,
            "boot_lead_minutes": 0,
            "shutdown_grace_minutes": 0,
        },
    )
    print(f"已建立班級「{created['name']}」")
    return created


def prepare_and_submit(api: Api, token: str, cfg: Config, item: dict, version_id: str) -> dict:
    """planning 狀態：綁環境、加學生、開老師機器、預檢容量、送出建機。"""
    class_path = f"/teaching-classes/{item['id']}"
    if item.get("course_version_id") != version_id:
        api.call("PUT", f"{class_path}/course", token=token, body={"course_version_id": version_id})
        print("班級已綁定課程環境")

    result = api.call(
        "POST", f"{class_path}/students", token=token, body={"emails": cfg.student_emails}
    )
    if result["not_found"] or result["invalid_role"]:
        raise ScriptError(
            "有學生無法加入班級："
            f"找不到 {result['not_found'][:5]}…，角色不符 {result['invalid_role'][:5]}…"
            "（請先執行 seed-accounts.py）"
        )
    print(f"學生：本次加入 {result['added']} 位，班上共 {result['class']['member_count']} 位")

    try:
        item = api.call(
            "PUT", f"{class_path}/instructor-machine", token=token, body={"enabled": True}
        )
    except ApiError as exc:
        if exc.status in (404, 405) and exc.detail in ("Not Found", "Method Not Allowed"):
            raise ScriptError(
                "後端還沒有 instructor-machine 端點，請先部署班級擁有者機器的後端修改"
            ) from None
        raise
    print("已開啟導師機器")

    capacity = api.call("GET", f"{class_path}/capacity-preview", token=token)
    if not capacity.get("ready"):
        raise ScriptError("容量預檢未通過：" + "；".join(capacity.get("issues") or ["原因不明"]))
    print(
        f"容量預檢通過：{capacity.get('machine_count')} 台、"
        f"{capacity.get('cpu_cores')} 核、{capacity.get('memory_mb')} MB、"
        f"{capacity.get('disk_gb')} GB、IP 剩 {capacity.get('available_ips')} 個"
    )
    item = api.call("POST", f"{class_path}/provision", token=token)
    print(f"已送出建機，班級狀態：{item['status']}")
    return item


def wait_until_provisioned(api: Api, token: str, cfg: Config, class_id: str) -> dict:
    deadline = datetime.now() + cfg.provision_timeout
    last = None
    while True:
        item = api.call("POST", f"/teaching-classes/{class_id}/reconcile", token=token)
        progress = (item["status"], item["ready_machines"], item["total_machines"])
        if progress != last:
            print(f"建機進度：{item['ready_machines']}/{item['total_machines']}（{item['status']}）")
            last = progress
        if item["status"] in ("active", "partial_failed"):
            return item
        if datetime.now() >= deadline:
            raise ScriptError(f"等待建機逾時（{cfg.provision_timeout}），請到班級頁查看進度後重跑")
        time.sleep(POLL_SECONDS)


# ─── 整組開機檢查 ─────────────────────────────────────────────────────────────


def class_vmids(item: dict) -> list[int]:
    members = list(item.get("students") or [])
    if item.get("instructor_machine"):
        members.append(item["instructor_machine"])
    return [
        machine["vmid"]
        for member in members
        for machine in member.get("machines") or []
        if machine.get("vmid")
    ]


def in_class_window(cfg: Config, now: datetime) -> bool:
    today = now.date()
    return cfg.start_date <= today <= cfg.end_date and today.weekday() == cfg.weekday


def running_vmids(api: Api, token: str, class_id: str) -> set[int]:
    usage = api.call("GET", f"/teaching-classes/{class_id}/resource-usage", token=token)
    return {row["vmid"] for row in usage["items"] if row["status"] == "running"}


def verify_power(api: Api, token: str, cfg: Config, item: dict) -> bool:
    """以導師身分確認整組開機；排程器逾時沒開的，每 10 台一批補開。"""
    vmids = class_vmids(item)
    if not in_class_window(cfg, datetime.now(ZoneInfo(TIMEZONE))):
        print("目前不在上課時段，略過開機檢查（下一個上課日排程器會自動開機）")
        return True

    def wait_for_boot(limit: timedelta) -> set[int]:
        deadline = datetime.now() + limit
        while True:
            running = running_vmids(api, token, item["id"]) & set(vmids)
            print(f"開機進度：{len(running)}/{len(vmids)}")
            if len(running) == len(vmids) or datetime.now() >= deadline:
                return running
            time.sleep(POLL_SECONDS)

    running = wait_for_boot(cfg.boot_timeout)
    stopped = [vmid for vmid in vmids if vmid not in running]
    if stopped:
        print(f"排程器逾時仍有 {len(stopped)} 台未開機，以導師身分分批補開")
        for index in range(0, len(stopped), POWER_CHUNK_SIZE):
            chunk = stopped[index : index + POWER_CHUNK_SIZE]
            result = api.call(
                "POST", "/resources/batch", token=token, body={"vmids": chunk, "action": "start"}
            )
            for row in result["results"]:
                if not row["success"]:
                    print(f"  vmid {row['vmid']} 開機失敗：{row['message']}")
        running = wait_for_boot(timedelta(minutes=5))
    missing = sorted(set(vmids) - running)
    if missing:
        print(f"仍未開機：{missing}")
    return not missing


# ─── 主流程 ───────────────────────────────────────────────────────────────────


def print_plan(cfg: Config, image: str) -> None:
    disk_gb = cfg.lxc_disk_gb
    sessions = []
    current = cfg.start_date
    while current <= cfg.end_date:
        sessions.append(current.isoformat())
        current += timedelta(days=7)
    machines = (len(cfg.student_emails) + 1) * len(NODE_KEYS)
    print("─" * 60)
    print(f"班級：{cfg.class_name}（{cfg.class_term}）")
    print(f"期間：{cfg.start_date} → {cfg.end_date}，每週{'一二三四五六日'[cfg.weekday]} 00:00–23:59 自動開機")
    print(f"自動開機日（{len(sessions)} 次）：{', '.join(sessions)}")
    print(f"到期：{cfg.end_date} 23:59 自動封存並刪除機器")
    print(f"導師：{cfg.teacher_email}")
    print(f"學生：{cfg.student_emails[0]} ～ {cfg.student_emails[-1]}（{len(cfg.student_emails)} 位）")
    print(f"機器：每人 2 台互通 LXC（映像 {image}，{cfg.lxc_cpu} 核 / {cfg.lxc_memory_mb} MB / {disk_gb} GB）")
    print(f"合計：{machines} 台、{machines * cfg.lxc_cpu} 核、{machines * cfg.lxc_memory_mb} MB、{machines * disk_gb} GB")
    print("─" * 60)


def run(cfg: Config) -> int:
    api = Api(cfg.api_url)
    admin_token = login(api, cfg.admin_email, cfg.admin_password, cfg.admin_totp_secret)
    image = resolve_lxc_image(api, admin_token, cfg.lxc_image)
    print_plan(cfg, image)
    if cfg.dry_run:
        print("dry-run：不建立任何東西")
        return 0

    teacher_token = login(api, cfg.teacher_email, cfg.teacher_password or "")
    version_id = ensure_environment(api, teacher_token, cfg, environment_body(cfg, image))
    item = ensure_class(api, teacher_token, cfg)

    if item["status"] == "planning":
        item = prepare_and_submit(api, teacher_token, cfg, item, version_id)
    if item["status"] == "pending_review":
        api.call(
            "POST",
            f"/batch-provision/class/{item['id']}/review",
            token=admin_token,
            body={"decision": "approved", "review_comment": "class-event CI"},
        )
        print("管理員已核准建機")

    item = wait_until_provisioned(api, teacher_token, cfg, item["id"])
    item = api.call("GET", f"/teaching-classes/{item['id']}", token=teacher_token)
    powered = verify_power(api, teacher_token, cfg, item)

    print("─" * 60)
    print(f"班級 id：{item['id']}（狀態 {item['status']}）")
    print(f"機器：{item['ready_machines']}/{item['total_machines']} 台建好")
    if item["status"] == "partial_failed":
        print("部分機器建立失敗：到班級頁按「重試失敗」，或修正後重跑這個 workflow")
        return 1
    return 0 if powered else 1


def main() -> int:
    try:
        return run(load_config())
    except ScriptError as exc:
        print(f"失敗：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

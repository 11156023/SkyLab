"""每個畫面的完整導覽：什麼時候用、功能怎麼用、會跳出哪些視窗、該改去哪一頁。

``surfaces.py`` 講「這頁是什麼、每個元素是什麼」；這裡講「怎麼用」。分開放是因為
這份內容長，混在元素定義裡兩邊都難讀。``surfaces.py`` 載入時依 surface id 把
這裡的內容併進 ``SurfaceSpec``。

寫作規則（違反了測試會紅燈，或使用者會照著錯的說明找）：

1. **不寫版面位置。** 沒有「右上角」「下方」「左側」。按鈕就用畫面上的文字指認：
   「按『新增連線』」。這條跟 ``surfaces.py`` 一樣。
2. **視窗標題與開啟按鈕用畫面原文。** 測試會拿它們比對前端語系檔；UI 改字時要
   一起改。標題帶插值（「克隆『範本名稱』」）的列在測試的豁免清單。
3. **欄位限制要對得上前端驗證。** 寫「至少 10 個字」之前先看元件的 validate。
4. **相關頁面只能指向導覽目錄裡的路徑**，助手才帶得過去；權限由執行時過濾。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.ai.contextual_help.schemas import DialogField, DialogSpec, RelatedPage


@dataclass(frozen=True)
class SurfaceGuide:
    when_to_use: str
    features: tuple[str, ...] = ()
    dialogs: tuple[DialogSpec, ...] = ()
    related: tuple[RelatedPage, ...] = ()


def _f(label: str, help: str = "", *, required: bool = False) -> DialogField:
    return DialogField(label=label, help=help, required=required)


# ── 共用的視窗 ────────────────────────────────────────────────────────

# 防火牆頁、教學環境的機器配置、資源詳細的進階設定共用同一個 ConnectionDialog。
_CONNECTION_DIALOG_FIELDS = (
    _f(
        "你要做什麼？",
        "先選一種：讓機器能上網、讓兩台機器互通、開放服務給外部，或自己寫規則（進階）。"
        "後面的欄位會依選擇改變。",
        required=True,
    ),
    _f("哪台機器", "要套用這條規則的機器。「讓兩台機器互通」改填「從哪台」與「連到哪台」。", required=True),
    _f(
        "怎麼開放",
        "只在「開放服務給外部」出現：用網址（填網址開頭、選網址結尾、填服務 port，可勾 HTTPS）、"
        "用對外 port（填對外 port 與內部 port 的對應），或僅開放防火牆。",
    ),
    _f("你的服務跑在哪個 port？", "服務在機器裡監聽的 port，例如網站常見 80、Node.js 3000、Flask 5000。"),
    _f("方向", "兩台互通時選單向或雙向；自己寫規則時選進站或出站。"),
    _f("Port", "自己寫規則時填，可以是單一 port（22）或範圍（8000:8010）；協定選任意時不需要。"),
    _f("來源 IP 或網段", "自己寫規則時填，留空代表不限制；例如 10.10.0.0/24 只允許校內網段。"),
)

_CONNECTION_DIALOG_NOTES = (
    "網址或 port 已被使用時會提示換一個。",
    "網址選項無法使用時，代表管理員還沒完成網域或 Gateway 設定，只能先用對外 port。",
)


GUIDES: dict[str, SurfaceGuide] = {}


def _add(surface_id: str, guide: SurfaceGuide) -> None:
    assert surface_id not in GUIDES, surface_id
    GUIDES[surface_id] = guide


# ── 所有登入者 ────────────────────────────────────────────────────────

_add("dashboard", SurfaceGuide(
    when_to_use="剛登入、想知道今天該做什麼，或想從總覽直接跳到要處理的地方。",
    features=(
        "學生：看目前的課程、本週任務與可以直接進入的練習環境。",
        "教師：看學生的 checkpoint 完成度與近期課堂，點進去就是對應的班級。",
        "管理者：「待確認的問題」列出需要優先處理的事項，點一下前往對應頁面；也可以直接問維運助手。",
    ),
    related=(
        RelatedPage("/my-resources", "要操作自己的機器（開關機、連線）"),
        RelatedPage("/my-requests", "要申請新機器或查看申請進度"),
        RelatedPage("/quick-create", "想立刻開一組練習環境，不等審核"),
        RelatedPage("/class-management", "老師要管理班級與上課"),
    ),
))

_add("courses", SurfaceGuide(
    when_to_use="學生想看自己修了哪些課，或要進入某一門課看本週任務。",
    features=(
        "每張卡片是一門老師已發布的課程，點進去看任務與課堂機器。",
        "看不到課程時，代表老師還沒發布或還沒把你加入班級。",
    ),
    related=(
        RelatedPage("/my-resources", "要直接開關或連線到課堂機器"),
        RelatedPage("/dashboard", "回首頁看今天的總覽"),
    ),
))

_add("course", SurfaceGuide(
    when_to_use="上課或做作業時，要看這門課現在要做什麼、到哪一週，以及連進課堂機器。",
    features=(
        "「截至今天的所有任務」依週列出老師發布的任務與檢查項目；勾選只記錄你的完成狀態，不會啟動 AI導師檢查。",
        "「你的課堂機器」直接點機器就能進入桌面或終端機。",
        "點某一週可以看那週的檢查回饋、指定機器與教材。",
    ),
    related=(
        RelatedPage("/my-resources", "機器要開機、重開或查看完整設定"),
    ),
))

_add("course-week", SurfaceGuide(
    when_to_use="想看某一週的檢查結果、老師評語，或這週指定要用哪台機器。",
    features=(
        "「檢查回饋」顯示你最新的檢查結果、分數與老師公開的評語。",
        "「本週機器」列出這週要用的機器；有網址可以直接開啟網站，或按「機器與主控台」進入機器。",
        "按「返回課程」回到課程頁。",
    ),
    related=(
        RelatedPage("/my-resources", "要開關或重開這台機器"),
    ),
))

_add("my-resources", SurfaceGuide(
    when_to_use="要使用已經開好的機器：開關機、連進去操作、看 IP 或對外網址，或不需要時刪除。",
    features=(
        "「控制台」（虛擬機）或「終端機」（容器）直接在瀏覽器連進機器；機器要在執行中才能開。",
        "⋯「更多操作」裡有電源控制：啟動、關機、重新啟動，以及刪除。",
        "點機器名稱進入詳細頁，可以調規格、建快照或備份、改防火牆、重設密碼與申請延長。",
        "同一組課程或快速練習的機器會收成一列，可以整組開機、關機或結束練習。",
        "還在建立中的申請也會列出來，可以取消；「下載連線 App」可安裝桌面連線工具。",
    ),
    dialogs=(
        DialogSpec(
            id="convert-template",
            title="轉成範本",
            opened_by="轉成範本",
            purpose="把調好的機器變成範本，讓學生在申請時直接選用。",
            fields=(
                _f("範本名稱", required=True),
                _f("說明", "這個範本裝了什麼、適合哪門課（選填）。"),
                _f("可見範圍", "「只有我看得到」或「所有人都能選用」。"),
                _f("請輸入機器名稱以確認", "照提示輸入這台機器的名稱，才能按「轉成範本」。", required=True),
            ),
            notes=(
                "系統會先把機器關機再轉換，這個動作無法復原。",
                "轉換後這台機器會從資源列表消失，改出現在「機器範本」。",
                "轉換時會偵測機器有沒有 cloud-init；沒有的話範本會標示「密碼無法由平台設定」，開出來的機器沿用範本內的帳號密碼。",
            ),
            access="staff",
        ),
    ),
    related=(
        RelatedPage("/my-requests", "還沒有機器，或要追蹤申請進度"),
        RelatedPage("/quick-create", "想立刻開一組練習環境"),
        RelatedPage("/firewall", "要讓機器互通或把服務開放給外部"),
        RelatedPage("/jobs", "要看刪除、備份或建立等背景任務的進度"),
    ),
))

_add("resource-detail", SurfaceGuide(
    when_to_use="要針對單一台機器做細部設定：看監控、調規格、建快照或備份、改防火牆或登入憑證、延長到期日、共享給別人。",
    features=(
        "「總覽」：連線資訊、帳號、IP 與範本附的使用手冊。",
        "「監控」：CPU、記憶體、網路、磁碟的趨勢，可切換時間範圍。",
        "「規格」：調 CPU、記憶體與磁碟（磁碟只能放大），填申請原因後送出；審核通過後按「套用新規格」，機器會關機、套用再開機。",
        "「快照」：「建立快照」保存目前磁碟狀態，可「還原」；有初始快照時可「一鍵重置」回到剛開通的狀態。",
        "「備份」：儲存空間不支援快照的機器改用備份當還原點，建立與還原都要幾分鐘。",
        "「操作紀錄」：這台機器的開關機與設定變更紀錄。",
        "「進階設定」：生命週期（到期日、申請延長）、防火牆規則與對外網址、開機順序與 ISO、登入憑證、共享與轉移擁有者。",
    ),
    dialogs=(
        DialogSpec(
            id="snapshot",
            title="建立快照",
            opened_by="建立快照",
            purpose="保留目前磁碟狀態，之後可以隨時還原。",
            fields=(
                _f("名稱", "例如 snap-2026-07-04，用來辨認這個快照。", required=True),
                _f("描述", "選填，例如：升級套件前的備份。"),
            ),
            notes=("還原到快照後，快照之後的變更都會消失。",),
        ),
        DialogSpec(
            id="backup",
            title="建立備份",
            opened_by="建立備份",
            purpose="完整保存這台機器目前的磁碟內容，之後可以整台還原。",
            fields=(_f("描述", "選填，例如：升級套件前。"),),
            notes=(
                "備份數量有上限，到上限要先刪除舊備份。",
                "容器備份期間會暫時關機，完成後自動開回；進度可在「背景任務」查看。",
            ),
        ),
        DialogSpec(
            id="reset-password",
            title="重設登入密碼",
            opened_by="重設密碼",
            purpose="讓系統產生一組新密碼，或自己指定。",
            fields=(
                _f("自己指定密碼", "不勾選時由系統產生，新密碼只顯示一次。"),
                _f("新密碼", "勾選自己指定時才要填：至少 8 個字，不能含空白。"),
            ),
            notes=("機器執行中時，重設後會自動重新開機套用，請先存好機器內的工作。",),
        ),
        DialogSpec(
            id="extend",
            title="申請延長到期日",
            opened_by="申請延長到期日",
            purpose="送出後由管理員審核，核准後到期日直接更新。",
            fields=(
                _f("新的到期日", "必須晚於目前的到期日，一次最多延長一年。", required=True),
                _f("申請原因", "說明為什麼需要延長（課程進度、專題時程等），至少 10 個字。", required=True),
            ),
            notes=("同一台機器一次只能有一張延期申請在審核中，可以撤銷。",),
        ),
        DialogSpec(
            id="firewall-rule",
            title="新增防火牆規則",
            opened_by="新增規則",
            purpose="替這台機器加一條防火牆規則，或切到「開放服務給外部」發布網址與 port。",
            fields=_CONNECTION_DIALOG_FIELDS,
            notes=_CONNECTION_DIALOG_NOTES,
        ),
        DialogSpec(
            id="transfer",
            title="轉移擁有者",
            opened_by="轉移",
            purpose="把這台機器交給另一個帳號。",
            fields=(
                _f("新擁有者的登入信箱", "對方登入 SkyLab 用的 Email。", required=True),
                _f("轉移後仍保留我的開關機權限", "勾選後你還能開關機與使用主控台。"),
                _f("請輸入機器名稱以確認", "照提示輸入機器名稱才能送出。", required=True),
            ),
            notes=("轉移後你會失去所有設定權限，無法自行取回。",),
        ),
    ),
    related=(
        RelatedPage("/my-requests", "要追蹤規格調整或延期申請的審核狀態"),
        RelatedPage("/firewall", "要用拓撲圖一次看所有機器之間的連線"),
        RelatedPage("/jobs", "要看備份、還原或重置任務的進度"),
    ),
))

_add("my-requests", SurfaceGuide(
    when_to_use="要申請新機器，或想知道申請審核到哪、為什麼被退回、開通失敗怎麼辦。",
    features=(
        "按「申請資源」開啟申請表單。",
        "每筆申請顯示審核狀態與開通進度；被退回時會附上審核者的原因。",
        "還沒進入開通的申請可以「撤銷」；開通失敗的申請可以「重試」。",
        "「規格調整申請」審核通過後，由你自己按「套用」，執行中的虛擬機會先關機、套用後再開機。",
        "已開通的申請可以按「前往我的資源」直接操作機器。",
    ),
    related=(
        RelatedPage("/my-resources", "申請已開通，要開始使用機器"),
        RelatedPage("/quick-create", "老師已經準備好練習環境，不想等審核"),
        RelatedPage("/jobs", "想看開通任務的詳細進度"),
    ),
))

_add("request-form", SurfaceGuide(
    when_to_use="需要一台自己的虛擬機或容器（做作業、專題、架服務），而且沒有現成的快速練習環境可用。",
    features=(
        "資源設定：「資源名稱」只能用小寫英文、數字與連字符，不能以連字符開頭或結尾；「作業系統」可選老師準備的範本或映像。需要 GPU、圖形介面或 Windows 時選虛擬機範本。",
        "帳號：虛擬機要填「使用者名稱」；Windows 範本固定為 Admin、容器固定為 root。「密碼」至少 8 個字元，由你自己設定。",
        "硬體配置：CPU、記憶體、硬碟已帶入範本建議值，可以調整；可調範圍受你的剩餘配額限制，硬碟不能小於範本本身。",
        "使用時段：立即模式送出後就開始部署；預約模式要填開始與結束時間，結束必須晚於開始。需要 GPU 時要先決定時段才會載入可用的 GPU。",
        "「申請原因」至少 10 個字元，審核時會看這一欄；按「送出申請」後等管理員審核。",
        "不知道選什麼規格時，可以請 AI 助手規劃配置，它會直接填進這張表。",
    ),
    related=(
        RelatedPage("/quick-create", "只是要練習，老師已經發布練習環境"),
        RelatedPage("/my-resources", "已經有機器，只是要調規格或延長使用"),
    ),
))

_add("quick-create", SurfaceGuide(
    when_to_use="想馬上有一組練習環境、不想等審核，而且老師已經發布了合適的練習模板。",
    features=(
        "每張卡片是一組老師發布的練習環境，標示會建立幾台機器，以及「免人工審核」。",
        "按「立即建立」進到啟動確認頁，還不會真的開始建立。",
        "練習環境有時數限制，時間到由系統停止；需要長期使用的機器請改走一般申請。",
    ),
    related=(
        RelatedPage("/my-requests", "需要自己決定規格或長期使用的機器"),
        RelatedPage("/my-resources", "已經啟動，要開始使用機器"),
    ),
))

_add("quick-template-form", SurfaceGuide(
    when_to_use="已經選好一組快速練習環境，要確認內容後啟動。",
    features=(
        "「環境說明」列出規則：固定配置不能改規格、不用等待審核、有時數限制。",
        "「本次會建立的機器」與「環境合計」顯示每台機器與整組會用掉的 CPU、記憶體、磁碟。",
        "確認後按啟動，系統會一次建立整組機器。",
    ),
    related=(
        RelatedPage("/my-resources", "啟動後要連進機器"),
        RelatedPage("/quick-create", "想換一組練習環境"),
    ),
))

_add("account", SurfaceGuide(
    when_to_use="要改姓名、Email、頭像或密碼，設定兩步驟驗證，調整介面主題與語言，或刪除帳號。",
    features=(
        "「個人資料」：按「編輯」修改姓名、Email 與頭像網址；改 Email 要到新信箱收驗證信才會生效。LDAP 帳號的資料由學校目錄管理。",
        "「變更密碼」：填目前密碼與新密碼；新密碼至少 8 個字元，且要同時有大寫、小寫、數字與特殊符號。",
        "「兩步驟驗證」：按「綁定驗證 App」用 Authenticator 掃 QR code；管理員要求啟用的帳號無法自行停用。",
        "「外觀」：切換介面主題與顯示語言。",
        "「關於」：版本、授權與開源元件清單。",
    ),
    dialogs=(
        DialogSpec(
            id="totp-enable",
            title="綁定 Google Authenticator",
            opened_by="綁定驗證 App",
            purpose="讓登入時多一道驗證碼保護帳號。",
            fields=(
                _f("QR code", "用 Google Authenticator、Microsoft Authenticator 或 Authy 掃描；無法掃描時改用手動輸入金鑰。"),
                _f("驗證碼", "輸入 App 顯示的 6 位數驗證碼完成綁定。", required=True),
            ),
        ),
        DialogSpec(
            id="totp-disable",
            title="停用兩步驟驗證",
            opened_by="停用兩步驟驗證",
            purpose="停用後登入只需要密碼。",
            fields=(_f("驗證碼", "輸入 Authenticator App 目前顯示的 6 位數驗證碼。", required=True),),
        ),
        DialogSpec(
            id="delete-account",
            title="確定要刪除帳號嗎？",
            opened_by="刪除帳號",
            purpose="永久刪除帳號，無法復原。",
            fields=(_f("確認文字", "照提示輸入「刪除」才能按「確定刪除」。", required=True),),
            notes=("仍持有已開通的資源時系統會拒絕刪除，請先清除資源。",),
        ),
    ),
    related=(
        RelatedPage("/my-resources", "刪除帳號前要先刪掉自己的機器"),
    ),
))

_add("jobs", SurfaceGuide(
    when_to_use="想知道建立、刪除、備份、還原、範本轉換或批次開通等長時間工作跑到哪，或為什麼失敗。",
    features=(
        "用類型與狀態篩選任務，「進行中」包含等待中、執行中與受阻。",
        "點一筆任務開啟詳細視窗，看訊息、輸出與錯誤內容。",
        "失敗的任務會顯示錯誤；處理方式通常要回到原本的頁面重試（例如「我的申請」的重試）。",
    ),
    related=(
        RelatedPage("/my-requests", "開通失敗要重試申請"),
        RelatedPage("/my-resources", "任務完成，要使用機器"),
    ),
))

_add("firewall", SurfaceGuide(
    when_to_use="要讓機器上網、讓兩台機器互通、把機器上的網站或服務開放給外部，或整體檢查目前開了哪些連線。",
    features=(
        "拓撲圖上每個節點是一台機器，線就是一條連線規則；點節點看它的防火牆規則，點線看開放了什麼。",
        "按「新增連線」開啟設定視窗；也可以直接把一個節點拖到另一個節點上建立連線。",
        "「分組」「連線標籤」「上網線」「地圖」「只看對外開放」切換顯示方式，只影響檢視、不改規則。",
        "機器很多時可以搜尋機器名稱或 IP，或依群組篩選。",
        "要刪除連線就點那條線，再按刪除。",
    ),
    dialogs=(
        DialogSpec(
            id="connection",
            title="新增連線",
            opened_by="新增連線",
            purpose="建立一條連線規則：上網、互通、對外發布服務，或自訂規則。",
            fields=_CONNECTION_DIALOG_FIELDS,
            notes=_CONNECTION_DIALOG_NOTES,
        ),
    ),
    related=(
        RelatedPage("/my-resources", "要先確認服務已在機器裡跑起來"),
    ),
))

_add("ai-api", SurfaceGuide(
    when_to_use="想在自己的程式或專題裡呼叫平台提供的 AI 模型，需要 API 金鑰，或想直接跟模型聊天、查看自己的用量。",
    features=(
        "按「申請金鑰」送出申請，管理員核准後金鑰會出現在「API 金鑰」分頁。",
        "點金鑰名稱開啟詳細資料，可以複製 API Key、Base URL 與 cURL 指令；⋯ 選單可重新命名、重新產生金鑰或刪除。",
        "「API 快速開始」提供 Base URL、查模型的指令，以及 JavaScript、Python、CMD / cURL 範例程式。",
        "「API 聊天」直接用你的金鑰跟模型對話，紀錄只存在這個瀏覽器。",
        "「申請紀錄」看每次申請的審核結果；「我的用量」看呼叫次數與 Tokens，以及逐筆紀錄。",
    ),
    dialogs=(
        DialogSpec(
            id="apply-key",
            title="申請金鑰",
            opened_by="申請金鑰",
            purpose="送出 AI API 金鑰申請，由管理員審核。",
            fields=(
                _f("金鑰名稱", "給自己辨認用，例如：課程專案用、測試用、我的 App；最多 20 字。", required=True),
                _f("申請目的", "說明要拿金鑰做什麼，至少 10 字，審核時會看這一欄。", required=True),
                _f("金鑰有效期限", "學生可選 1 天、1 週、1 個月或 90 天；教師與管理員可選 1 天、1 週、1 個月或永不過期。"),
            ),
        ),
        DialogSpec(
            id="quick-start",
            title="API 快速開始",
            opened_by="API 快速開始",
            purpose="串接說明：複製連線資訊、查可用的模型、執行範例程式。",
            fields=(
                _f("複製連線資訊", "Base URL 與 API Key；還沒有可用金鑰時要先申請。"),
                _f("查可用的模型", "複製指令到終端機執行，列出能用的模型名稱。"),
                _f("執行範例", "選 API 類型與程式語言，複製範例程式。"),
            ),
            notes=("遇到 401 代表金鑰錯誤、過期或已被替換；429 是請求太頻繁。",),
        ),
    ),
    related=(
        RelatedPage("/my-resources", "要在自己的機器上跑串接 AI 的程式"),
    ),
))

# ── 教師與管理者 ──────────────────────────────────────────────────────

_add("templates", SurfaceGuide(
    when_to_use="已經在一台機器裝好課程要用的軟體，想讓學生直接選用；或要更新、克隆、刪除既有範本。",
    features=(
        "按「從 VM 建立範本」把裝好環境的母機轉成範本。",
        "每個範本的 ⋯ 選單：「克隆開通」複製出一台可用的機器；「編輯 / 可見範圍」改名稱、說明、可見範圍與預設規格；「使用手冊」下載附件。",
        "可見範圍設為「全部可見」後，學生就能在申請表單選用；「私人」只有自己看得到。",
        "要更新範本內容：「開始更新循環」會開出一台暫存母機，改完回來按「完成更新（轉為新版）」；不改了就「取消更新循環」。",
        "轉換失敗時可以「重新轉換」；「刪除範本」無法復原，還有機器從它克隆出來時會被拒絕。",
    ),
    dialogs=(
        DialogSpec(
            id="create",
            title="把 VM 轉為範本",
            opened_by="從 VM 建立範本",
            purpose="選一台已裝好環境的母機轉成範本。",
            fields=(
                _f("來源機器", "選要轉換的 VM/LXC。", required=True),
                _f("範本名稱", "例如 Ubuntu 22.04 + Docker 實驗環境。", required=True),
                _f("說明（選填）", "這個範本裝了什麼、適合哪些課程。"),
                _f("可見範圍", "私人或全部可見。"),
                _f("這個範本需要 GPU", "用它開機器時一定要選一張 GPU；只有 VM 範本能設定。"),
                _f("自訂預設規格", "不勾選時沿用來源機器的 CPU 與記憶體；勾選後可設預設 CPU（1–8 核）與記憶體。"),
                _f("使用手冊 / 附件", "選填，最多 10 個，單檔 50MB 內；學生在克隆視窗可下載。"),
            ),
            notes=(
                "轉換會先關機並移除來源機的所有快照，完成後原 VM 變成唯讀範本、無法再直接開機。",
                "轉換時會偵測來源機有沒有 cloud-init；沒有的話範本會標示「密碼無法由平台設定」，開出來的機器沿用範本內的帳號密碼。",
            ),
        ),
        DialogSpec(
            id="edit",
            title="編輯範本",
            opened_by="編輯 / 可見範圍",
            purpose="更新範本的名稱、說明、可見範圍、克隆政策與預設規格。",
            fields=(
                _f("範本名稱", required=True),
                _f("說明（選填）"),
                _f("可見範圍", "設為全部可見，學生才能在申請表單選用。"),
                _f("自訂預設規格"),
                _f("使用手冊 / 附件", "可以補傳或刪除附件。"),
            ),
        ),
        DialogSpec(
            id="clone",
            title="克隆範本",
            opened_by="克隆開通",
            purpose="從範本複製出可用的機器，系統會自動設好 IP 與防火牆。",
            fields=(
                _f("主機名稱（選填）", "留空時使用範本名稱。"),
                _f("數量", "一次 1 到 50 台。"),
                _f("CPU 核心數"),
                _f("記憶體 (RAM)"),
                _f("硬碟空間", "跟隨範本，不可調整。"),
                _f("登入密碼（選填）", "留空由系統產生隨機密碼；自己填的密碼平台不會保存，忘記只能重設。範本標示「密碼無法由平台設定」時不能填。"),
                _f("GPU（此範本必須配置）", "範本需要 GPU 時才出現，要選一張可用的 GPU 與 vGPU 規格。"),
                _f("克隆完成後自動開機"),
            ),
            notes=("送出後進度在「背景任務」，完成的機器會出現在「我的資源」。",),
        ),
    ),
    related=(
        RelatedPage("/my-resources", "要先準備一台母機並裝好軟體"),
        RelatedPage("/course-template-management", "要把範本組成多台機器的教學環境給班級使用"),
        RelatedPage("/jobs", "要看轉換或克隆的進度"),
    ),
))

_add("class-management", SurfaceGuide(
    when_to_use="老師要開新班級、繼續還沒設定完的班級，或進入已就緒的班級上課。",
    features=(
        "按「建立班級」進入一鍵建立班級的精靈。",
        "用狀態篩選：準備中（還沒送出建機）、建置中（等待審核或正在建立，不需要做事）、需要處理（有機器建立失敗）、可以上課。",
        "搜尋班級名稱；已結束的班級預設不列出，勾「顯示已結束」才看得到。",
        "點班級卡片進入班級，管理學生、上課環境、每週內容、上課監看與 AI導師檢查。",
    ),
    related=(
        RelatedPage("/class-setup", "要建立新班級"),
        RelatedPage("/course-template-management", "還沒有可以給班級用的教學環境"),
        RelatedPage("/course-cms", "要編輯課程的學習路徑與題目"),
    ),
))

_CLASS_WORKSPACE_GUIDE = SurfaceGuide(
    when_to_use="已經建立的班級要補學生、看建機進度、安排每週內容、上課時監看全班機器，或設定 AI導師檢查。",
    features=(
        "「班級總覽」：建機準備清單、本週課程、班級資訊；完成學生名單與上課環境後按「確認並送出建機」，送出後設定會鎖定並等待審核。",
        "「加入學生」：貼上 Email 或「匯入 CSV」；找不到或不是學生身分的帳號會列出來。送出建機後名單就鎖定。",
        "「上課環境」：選已發布的教學環境版本，看每位學生的機器與網路拓撲。",
        "「每週內容」：填每週主題、指定本週機器、上傳任務檔案，設為「發布」學生才看得到；記得按「儲存每週內容」。",
        "「上課監看」：學生使用率熱力圖每 10 秒更新，點執行中的機器可直接開啟；可以全班開機、全班關機，或選一台 VM 直播示範給全班看。",
        "「AI導師檢查」：建立檢查表、產生並執行檢查腳本，核查學生結果。",
        "頁首的 ⋯ 選單：編輯班級與課表、延長課程日期、封存並回收（不可逆，會刪除班上的機器）。",
    ),
    dialogs=(
        DialogSpec(
            id="add-students",
            title="加入學生",
            opened_by="加入學生",
            purpose="把學生帳號加入這個班級。",
            fields=(
                _f("學生 Email", "一行一個，例如 student01@example.edu；帳號必須已存在而且是學生身分。", required=True),
            ),
            notes=("重複的帳號會自動略過；也可以改用「匯入 CSV」一次加入。",),
        ),
        DialogSpec(
            id="edit-class",
            title="編輯班級與課表",
            opened_by="編輯班級與課表",
            purpose="修改班級資料與固定上課時段。",
            fields=(
                _f("班級名稱", "例如：Linux 系統管理｜114-1。", required=True),
                _f("學期"),
                _f("上課地點", "例如：電腦教室 A，會顯示在學生的課表。"),
                _f("開始日期", required=True),
                _f("結束日期", required=True),
                _f("每週星期", required=True),
                _f("上課時間", "開始與結束時間。", required=True),
                _f("提前開機", "上課前多久把機器開好。"),
                _f("下課後關機", "下課後多留多久才自動關機，讓學生收尾。"),
            ),
        ),
        DialogSpec(
            id="extend-class",
            title="延長課程日期",
            opened_by="延長課程日期",
            purpose="把班級的結束日期往後延。",
            fields=(_f("延長班級到期日", "必須晚於目前的結束日期。", required=True),),
            notes=("延長後會自動補上新的課次，機器的到期日也一併順延。",),
        ),
    ),
    related=(
        RelatedPage("/class-management", "回到班級清單"),
        RelatedPage("/course-template-management", "上課環境沒有合適的版本，要建立或發布新環境"),
        RelatedPage("/course-cms", "要編輯學習路徑、任務與題目"),
    ),
)
_add("class-workspace", _CLASS_WORKSPACE_GUIDE)
_add("class-workspace-section", _CLASS_WORKSPACE_GUIDE)

_add("ai-judge", SurfaceGuide(
    when_to_use="想用 AI導師檢查處理全班學生機器上的作業（例如環境有沒有裝好、服務有沒有跑起來），再由老師核查結果。",
    features=(
        "按「新增檢查」建立一份空白檢查表，填檢查項目與評分方式。",
        "「檢查設定」編輯檢查項目；「腳本總覽」查看 AI 產生的檢查腳本並審查；「導師核查」確認或修改每位學生的結果。",
        "腳本集審查通過後按「一次執行」，在每位學生的對應機器上跑全部檢查；機器要開著才能檢查。",
        "檢查要出現在哪一週，可以用「調整週次」修改。",
    ),
    dialogs=(
        DialogSpec(
            id="new-check",
            title="新增檢查",
            opened_by="新增檢查",
            purpose="輸入名稱後，直接建立一份空白檢查表。",
            fields=(
                _f("檢查名稱", "例如：期中 Python 環境檢查。", required=True),
                _f("週次", "選這份檢查屬於哪一週；週次要先在班級的「每週內容」填上標題才能選。"),
            ),
        ),
        DialogSpec(
            id="run-all",
            title="一次執行整組檢查點",
            opened_by="一次執行",
            purpose="在每位學生的對應機器上執行全部檢查腳本。",
            fields=(
                _f("選擇腳本集", "只能選已通過審查的腳本集。", required=True),
                _f("執行範圍", "會在哪些機器上執行。"),
            ),
            notes=("沒開機的學生會列為未執行。",),
        ),
        DialogSpec(
            id="move-week",
            title="調整檢查週次",
            opened_by="調整週次",
            purpose="讓這份檢查只出現在所選週次，學生端不會混到其他週。",
            fields=(_f("週次", required=True),),
        ),
    ),
    related=(
        RelatedPage("/class-management", "回到班級清單"),
    ),
))

_add("class-setup", SurfaceGuide(
    when_to_use="要開一個新班級，替每位學生準備好上課機器。每一步都會保存成草稿，可以隨時離開再回來。",
    features=(
        "第 1 步「班級與課表」：填班級名稱、上課地點、開始與結束日期、每週上課星期與時間；學期、時區、提前開機在進階設定，可維持預設。",
        "第 2 步「學生名單」：貼上學生 Email（換行、逗號或分號分隔）或匯入 CSV，至少要有一位已存在的學生帳號。",
        "第 3 步「教學環境」：選已發布、提供給正式課程的環境版本。沒有合適的可以按「建立新的上課模板」，發布後會自動回到這一步並替你選好。",
        "第 4 步「每週任務」：選填，填每週主題或 Checkpoint；可勾選直接發布給學生。",
        "第 5 步「確認建立」：系統做容量預檢，通過後按完成設定送出；送出後學生、環境與課表會鎖定並等待管理員審核。",
    ),
    related=(
        RelatedPage("/course-template-management", "要先建立或發布教學環境"),
        RelatedPage("/class-management", "回到班級清單或繼續其他班級"),
    ),
))

_ENVIRONMENT_EDITOR_FEATURES = (
    "「基本資料」：填環境名稱、說明，選套用方式（只用於正式課程、只用於快速練習或兩者皆可），可上傳說明文件。",
    "「機器配置」：選來源方式——① 選擇既有範本，或 ② 新增 VM/LXC 規格並選基礎映像——再按「加入機器」；每位學生最多 3 台。",
    "點機器節點可以改名稱、角色與 CPU、RAM、Disk；按「新增連線」設定機器之間互通或對外服務。",
    "草稿會自動儲存。確認無誤後在機器配置按「發布」並確認「發布並鎖定」；發布後機器配置不能再改，名稱與套用方式仍可調整。",
    "從建立班級過來的，發布後會回到原本的班級。",
)

_ENVIRONMENT_CONNECTION_DIALOG = DialogSpec(
    id="connection",
    title="新增連線",
    opened_by="新增連線",
    purpose="設定這組環境裡機器之間的互通，或把某台機器的服務開放給外部。",
    fields=_CONNECTION_DIALOG_FIELDS,
    notes=(
        "課程環境的連線必須指定 port，不支援 icmp 這類無埠協定。",
        "用網址發布時可以用主機名樣板：{student} 是匿名學生代號、{class} 是課堂代號。",
    ),
)

_add("course-template-management", SurfaceGuide(
    when_to_use="要準備一組給班級或快速練習用的機器組合（例如一台網站伺服器加一台資料庫），或管理既有教學環境的版本。",
    features=(
        "按「建立教學環境」開始新的環境。",
        "清單顯示每個環境的狀態：草稿、已發布、已停用；點進去編輯。",
        "已發布的環境才能被班級選用；草稿可以刪除。",
    ),
    related=(
        RelatedPage("/templates", "環境需要的單台機器範本還沒準備好"),
        RelatedPage("/class-setup", "環境發布後要開班"),
        RelatedPage("/quick-create", "要確認快速練習的學生端看到什麼"),
    ),
))

_ENVIRONMENT_EDITOR_GUIDE = SurfaceGuide(
    when_to_use="正在建立或修改一組教學環境的基本資料與機器配置，準備發布給班級或快速練習使用。",
    features=_ENVIRONMENT_EDITOR_FEATURES,
    dialogs=(_ENVIRONMENT_CONNECTION_DIALOG,),
    related=(
        RelatedPage("/templates", "需要先把單台機器做成範本當來源"),
        RelatedPage("/course-template-management", "回到教學環境清單"),
        RelatedPage("/class-setup", "環境發布後要開班"),
    ),
)
_add("course-template-new", _ENVIRONMENT_EDITOR_GUIDE)
_add("course-template-editor", _ENVIRONMENT_EDITOR_GUIDE)

_add("course-cms", SurfaceGuide(
    when_to_use="要編輯學生在課程頁看到的學習路徑、房間、任務與題目，或查看學生的作答進度。",
    features=(
        "「內容編輯」：內容分三層——學習路徑、房間、任務。按「新增學習路徑」建立最外層，再在樹狀清單「新增房間」「新增任務」。",
        "選取學習路徑可以改名稱、連結所屬班級，並「發布」或「下架」；發布後學生才看得到。",
        "選取房間可以改名稱與難度；選取任務可以寫 Markdown 教學內容並「新增題目」。",
        "「學生進度」：選學習路徑後即時看每位學生的作答進度。",
    ),
    dialogs=(
        DialogSpec(
            id="new-path",
            title="新增學習路徑",
            opened_by="新增學習路徑",
            purpose="建立一條學習路徑並連結到班級。",
            fields=(
                _f("所屬班級", "一個班級只能連結一條學習路徑。", required=True),
                _f("路徑名稱", required=True),
            ),
        ),
        DialogSpec(
            id="question",
            title="新增題目",
            opened_by="新增題目",
            purpose="在任務底下加一題。",
            fields=(
                _f("題型", "答案題：學生要輸入正確答案；閱讀題：學生按完成就算數。", required=True),
                _f("題目描述", "最多 1000 字。", required=True),
                _f("正確答案", "答案題才要填；學生輸入去掉前後空白後要完全一樣，大小寫也要相同。"),
                _f("分數", "0 到 1000 分。"),
            ),
        ),
    ),
    related=(
        RelatedPage("/class-management", "要把路徑對應的班級準備好"),
    ),
))

# ── 僅管理者 ──────────────────────────────────────────────────────────

_add("resource-mgmt", SurfaceGuide(
    when_to_use="管理員要查看或處理全站的機器：找某台機器、代為開關機、處理建立失敗或刪除不要的資源。",
    features=(
        "清單列出所有虛擬機與容器的狀態、IP、到期日與節點；同一組課程環境的機器收成一列，可以整組開機或關機。",
        "「電源控制」送出開機、關機、重啟、強制停止或重置；「終端機」「控制台」直接連進機器。",
        "「查看詳情」進入單台機器的詳細頁，管理員調規格不需要送申請。",
        "勾選多台後可以「批次刪除」，動作無法復原。",
    ),
    related=(
        RelatedPage("/request-review", "要審核建立、規格調整或延期申請"),
        RelatedPage("/monitoring", "要看節點與機器的即時負載"),
        RelatedPage("/jobs", "要看刪除或建立任務的進度"),
    ),
))

_add("managed-resource-detail", SurfaceGuide(
    when_to_use="管理員要細看或調整某一台機器的監控、規格、快照、備份與進階設定。",
    features=(
        "分頁與使用者看到的資源詳細頁相同：總覽、監控、規格、快照、備份、操作紀錄、進階設定。",
        "管理員在「規格」可以直接套用新規格，不必送申請；執行中的虛擬機需重開機後 CPU／記憶體才會生效。",
    ),
    related=(
        RelatedPage("/resource-mgmt", "回到全站資源清單"),
    ),
))

_add("request-review", SurfaceGuide(
    when_to_use="有使用者送出機器建立、規格調整或延長到期日的申請，要決定核准或拒絕。",
    features=(
        "用狀態篩選待審核、已通過、已拒絕、已過期，或搜尋主機、申請人。",
        "點一筆申請看詳細：申請類型、規格或變更內容、預計節點與可行性評估；評估不可行時不能核准。",
        "填「審核備註」（申請者看得到）後按「核准」或「拒絕」。",
        "AI API 金鑰申請不在這裡，班級的批次建機也有自己的審核頁。",
    ),
    related=(
        RelatedPage("/batch-review", "要審核老師為班級送出的批次建機"),
        RelatedPage("/ai-api-review", "要審核 AI API 金鑰申請"),
        RelatedPage("/governance", "審核時發現配額需要調整"),
    ),
))

_add("batch-review", SurfaceGuide(
    when_to_use="老師送出班級建機後，要審核整批機器是否放得下、要不要核准。",
    features=(
        "點一筆批次看課程、資源類型、VM 數量、規格與作業系統，以及週期排程的開機時段。",
        "按「核准」或「駁回」，可以填審核備註；核准後在「建立進度」看建了幾台、失敗幾台。",
    ),
    related=(
        RelatedPage("/request-review", "要審核個人的機器申請"),
        RelatedPage("/monitoring", "核准前想確認叢集目前的負載"),
    ),
))

_add("gpu-mgmt", SurfaceGuide(
    when_to_use="要確認叢集有哪些 GPU、還剩幾張可用、被哪些機器佔用，或移除不再使用的 GPU mapping。",
    features=(
        "清單列出每組 GPU mapping 的描述、節點與 PCI 位址、可用與總數，以及正在使用的 VM。",
        "SR-IOV 的卡以 VF 計算，直通的卡以裝置計算；「已滿載」代表新的申請選不到它。",
        "「移除映射」把 GPU mapping 從平台移除。",
    ),
    related=(
        RelatedPage("/request-review", "有申請需要 GPU，要決定是否核准"),
        RelatedPage("/resource-mgmt", "要處理佔用 GPU 的機器"),
    ),
))

_add("monitoring", SurfaceGuide(
    when_to_use="要確認叢集是否過載、哪個節點或機器吃掉最多資源、有沒有警告或疑似挖礦的事件。",
    features=(
        "活動警告列出超過閾值或平台異常的警告，每 30 秒更新，可以按「確認」。",
        "節點用量點一列展開趨勢圖；CPU 與記憶體 Top 5 找出最吃資源的機器。",
        "挖礦事件列出疑似挖礦且已自動暫停的資源，可以「停權」擁有者或「誤判解除」。",
        "要看更細的歷史可以按「在 Grafana 查看詳細」。",
    ),
    dialogs=(
        DialogSpec(
            id="dismiss-mining",
            title="解除挖礦事件",
            opened_by="誤判解除",
            purpose="把這筆事件標記為誤判，並嘗試恢復機器運行。",
            fields=(
                _f("同時將此資源加入豁免（之後不再偵測）", "例如老師的模型訓練這類正常的高負載。"),
                _f("備註（選填）", "例如：教授的模型訓練工作負載。"),
            ),
        ),
    ),
    related=(
        RelatedPage("/governance", "要調整警告閾值或挖礦偵測政策"),
        RelatedPage("/resource-mgmt", "要處理某台機器"),
        RelatedPage("/ai-monitoring", "要看 AI API 的用量"),
    ),
))

_add("audit", SurfaceGuide(
    when_to_use="要追查誰在什麼時候做了什麼操作，例如某台機器被誰刪除、有沒有異常的登入失敗。",
    features=(
        "統計卡顯示總紀錄數、危險操作、登入失敗與活躍使用者。",
        "用搜尋、操作類型、使用者與起訖日期篩選紀錄。",
        "「匯出 CSV」下載目前篩選的紀錄；沒設篩選時只會匯出最新的一批。",
    ),
    related=(
        RelatedPage("/admin", "查到可疑帳號，要停用或調整角色"),
    ),
))

_add("pve-connections", SurfaceGuide(
    when_to_use="第一次接上 Proxmox VE、新增或修改叢集連線，或調整節點是否接收新機器與放置優先度。",
    features=(
        "按「新增連線」建立 PVE 連線，第一組會自動成為預設連線。",
        "每組連線可以「測試」實際連一次、「同步」重新抓節點與 Storage 清單、「編輯」或「刪除」。",
        "「節點」清單：取消「啟用」後該節點不再接收新 VM（既有 VM 不受影響）；「編輯」修改連線位址、Port 與優先度。",
        "節點的即時用量與趨勢在資源監控頁。",
    ),
    dialogs=(
        DialogSpec(
            id="connection",
            title="新增連線",
            opened_by="新增連線",
            purpose="設定一組 PVE 連線與這個叢集自己的資源設定。",
            fields=(
                _f("名稱", "識別用，例：機房A。", required=True),
                _f("Host", "PVE 的位址，例：192.168.100.2。", required=True),
                _f("Port", "1 到 65535。"),
                _f("API 使用者", required=True),
                _f("密碼", "新增時必填；編輯時留空表示不變更。"),
                _f("API Timeout（秒）"),
                _f("驗證 SSL 憑證", "PVE 用自簽憑證時可以填 CA 憑證 PEM。"),
                _f("Pool 名稱", "這個叢集建立的 VM / LXC 會放進這個 pool。"),
                _f("Backup Storage", "選填；機器不能用快照時改用備份當還原點，留空表示不開放備份。"),
                _f("內網網段", "例：192.168.100.0/24。"),
                _f("預設節點", "選填，未指定時優先使用這個節點。"),
                _f("啟用此連線"),
                _f("設為預設連線", "未指定叢集的建立工作會走預設連線。"),
            ),
        ),
        DialogSpec(
            id="edit-node",
            title="編輯節點",
            opened_by="編輯",
            purpose="修改單一節點的連線位址與放置優先度。",
            fields=(
                _f("Host", "平台呼叫此節點 PVE API 的位址，改錯會讓同步與開關機失效。", required=True),
                _f("Port", "1 到 65535。"),
                _f("優先度", "數字小的優先；只在各節點放置分數接近時作決勝（預設 5）。"),
            ),
        ),
    ),
    related=(
        RelatedPage("/storage", "要設定各 Storage 的速度等級與優先度"),
        RelatedPage("/scheduler", "要調整放置與超配參數"),
        RelatedPage("/monitoring", "要看節點的即時負載"),
    ),
))

_add("scheduler", SurfaceGuide(
    when_to_use="機器放置不平均、資源超賣太多或太少，或排程開機與練習時段的行為需要調整。",
    features=(
        "「放置與超配」：CPU 與 Disk 超配比決定一個節點能放多少機器。",
        "「資源評估閾值」：峰值餘裕、LoadAvg 警戒與上限、各資源權重，影響放置時怎麼挑節點。",
        "「排程開機與時段」：開機批次大小與間隔、提前開機、時段寬限、練習時段長度與到期提醒。",
        "改完按「儲存排程設定」一次送出整頁。",
    ),
    related=(
        RelatedPage("/pve-connections", "要停用某個節點或改優先度"),
        RelatedPage("/governance", "要設定配額或治理政策"),
    ),
))

_add("governance", SurfaceGuide(
    when_to_use="要設定全站的資源規範：警告閾值、到期回收、閒置與挖礦偵測、快照上限、克隆併發，或調整使用者能用多少資源。",
    features=(
        "資源警告、TTL 生命週期、閒置偵測、VM / LXC 自動判斷、反挖礦偵測、快照治理、克隆併發各自一組，開關與數值都在同一頁。",
        "改完按「儲存治理設定」一次送出。",
        "「全域預設配額」是沒有個人覆寫的使用者共用的上限，調整只影響之後的新增與擴容。",
        "「個別使用者覆寫」按「新增配額」替某位使用者設專屬上限；刪除後改回套用全域預設。",
    ),
    dialogs=(
        DialogSpec(
            id="quota",
            title="新增配額",
            opened_by="新增配額",
            purpose="替某位使用者設定專屬的資源上限。",
            fields=(
                _f("使用者", "輸入姓名或 email 搜尋。", required=True),
                _f("CPU cores", "1 到 256。"),
                _f("記憶體 (MB)", "256 到 1048576。"),
                _f("磁碟 (GB)", "1 到 65536。"),
                _f("實例數", "1 到 100。"),
                _f("無限制", "每一項都可以勾選，代表該項不設上限。"),
            ),
            notes=("欄位會帶入目前的全域預設值，改成這位使用者專屬的上限即可。",),
        ),
    ),
    related=(
        RelatedPage("/scheduler", "要調整放置與超配，而不是使用者上限"),
        RelatedPage("/monitoring", "要看警告與挖礦事件"),
    ),
))

_add("ldap", SurfaceGuide(
    when_to_use="要讓師生用學校的 LDAP / Active Directory 帳號登入，或調整誰登入後成為教師、管理員。",
    features=(
        "填伺服器 URI、服務帳號、使用者搜尋 Base DN 與過濾範本；{username} 會代入登入時輸入的帳號。",
        "「自動建立帳號」關閉時，只有已存在的本地帳號能用 LDAP 登入。",
        "填教師群組 DN、管理員群組 DN，屬於該群組的人會建立成對應角色。",
        "先按「測試連線」驗證設定（不會儲存），再按「儲存 LDAP 設定」，最後才啟用 LDAP 登入。",
    ),
    related=(
        RelatedPage("/admin", "要查看或調整個別帳號"),
    ),
))

_add("storage", SurfaceGuide(
    when_to_use="要決定新機器的磁碟優先放在哪個 Storage，或暫停使用某個 Storage。",
    features=(
        "每個 Storage 可以設「速度等級」（NVMe、SSD、HDD、未知）與「使用者優先度」（數字越小越優先）。",
        "取消「啟用」後，放置演算法不會再選它。",
        "共享 Storage 掛在多個節點上，變更會套用到所有掛載節點。",
        "可以依狀態篩選，或搜尋名稱、節點、類型。",
    ),
    related=(
        RelatedPage("/pve-connections", "清單少了 Storage，要重新同步連線"),
    ),
))

_add("ip-management", SurfaceGuide(
    when_to_use="第一次設定機器用的網段，或要查哪個 IP 分給了哪台機器。",
    features=(
        "按「建立子網設定」（已設定時為「編輯子網設定」）設定網段、閘道與對外 port 配號池。",
        "統計卡顯示子網總 IP、可用與已分配。",
        "IP 分配清單可以搜尋 IP、VMID 或備註。",
        "刪除子網設定會停用全站 VM / LXC 建立功能，而且要沒有機器佔用 IP 才能刪除。",
    ),
    dialogs=(
        DialogSpec(
            id="subnet",
            title="建立子網設定",
            opened_by="建立子網設定",
            purpose="設定新機器會用的網段與對外入口。",
            fields=(
                _f("子網 CIDR", "例：10.10.0.0/24；已有機器使用這個網段時不能改。", required=True),
                _f("閘道 IP", "例：10.10.0.1。", required=True),
                _f("Bridge 名稱", "例：vmbr1。", required=True),
                _f("VLAN ID", "選填，1–4094；留空表示不使用 VLAN。"),
                _f("Gateway VM IP", "例：10.10.0.2。", required=True),
                _f("DNS Servers", "選填，多組以逗號分隔，例如 8.8.8.8,1.1.1.1。"),
                _f("對外 port 配號池：起始", "1024 到 65535。", required=True),
                _f("對外 port 配號池：結束", "1024 到 65535。", required=True),
                _f("對外入口主機", "例：gw.school.edu 或 140.xxx.xxx.xxx，學生用它加對外 port 連進來。"),
                _f("預設封鎖網段 / IP", "選填，每行一個。"),
            ),
        ),
    ),
    related=(
        RelatedPage("/gateway", "要設定對外流量經過的 Gateway VM"),
        RelatedPage("/domain", "要讓機器用網址對外"),
    ),
))

_add("domain", SurfaceGuide(
    when_to_use="要接上 Cloudflare 管理網域，讓機器能用網址對外，或手動新增、修改 DNS 紀錄。",
    features=(
        "第一次使用先按「連線設定」填 Cloudflare Account ID 與 API Token，再按「測試連線」。",
        "連上後選一個 Zone，查看與搜尋它的 DNS 紀錄。",
        "按「新增紀錄」新增 DNS record；每筆紀錄可以編輯或刪除。",
        "這裡開放的網域就是使用者用網址發布服務時能選的網址結尾。",
    ),
    dialogs=(
        DialogSpec(
            id="config",
            title="Cloudflare 連線設定",
            opened_by="連線設定",
            purpose="讓平台可以管理 Cloudflare 上的 DNS。",
            fields=(
                _f("Account ID", "Cloudflare 帳號的 Account ID。"),
                _f("API Token", "需要 Zone / DNS 編輯權限；已設定時留空表示不變更。", required=True),
                _f("預設 DNS Target 類型", "A（IP 位址）或 CNAME（主機名）。"),
                _f("預設 DNS Target 值", "例：140.131.x.x 或 gw.example.com。"),
            ),
        ),
        DialogSpec(
            id="record",
            title="新增 DNS Record",
            opened_by="新增紀錄",
            purpose="在選取的 Zone 新增一筆 DNS 紀錄。",
            fields=(
                _f("類型", required=True),
                _f("TTL", "設 1 代表 Auto。"),
                _f("名稱", "例：www 或 www.example.com。", required=True),
                _f("內容", "例：140.131.x.x。", required=True),
                _f("優先順序", "MX 這類紀錄才需要，0 到 65535。"),
                _f("備註", "選填。"),
                _f("經由 Cloudflare Proxy（橘色雲）"),
            ),
        ),
    ),
    related=(
        RelatedPage("/gateway", "要設定 HTTPS 憑證或平台入口"),
        RelatedPage("/firewall", "要用網址發布某台機器的服務"),
    ),
))

_add("gateway", SurfaceGuide(
    when_to_use="要設定或檢查對外流量經過的 Gateway VM：nginx 轉發、WireGuard VPN、HTTPS 憑證，或讓平台本身用網域與 HTTPS 對外。",
    features=(
        "「連線設定」：填 Gateway 的 SSH 連線，把 SSH 公鑰加到 Gateway 的 authorized_keys；可以重新產生 Keypair 或重設 Host Key。",
        "「安裝服務」：在 Gateway 上安裝需要的服務。",
        "「HTTPS 憑證」：系統不會自動簽發憑證，請自行簽好放到 Gateway，再填憑證（fullchain）與私鑰路徑，按「儲存並套用」；換新憑證路徑不變時按「重新套用」。",
        "「平台入口」：填主系統網域與 Gateway 連得到的部署機位址，可先「測試上游」，儲存時連不到就不套用；可選擇經由 Cloudflare Proxy。",
        "「nginx」與「WireGuard VPN」：查看與調整服務設定，服務日誌每 10 秒更新；可以啟動、停止或重新啟動服務。",
    ),
    related=(
        RelatedPage("/domain", "要接上 Cloudflare 或調整 DNS"),
        RelatedPage("/ip-management", "要設定網段與對外 port 配號池"),
    ),
))

_add("admin", SurfaceGuide(
    when_to_use="要新增帳號、改某人的角色（學生、教師、管理者）、停用帳號、要求或重設兩步驟驗證。",
    features=(
        "搜尋使用者；每列顯示角色、啟用狀態，以及 LDAP、2FA 標記。",
        "按「新增使用者」或「編輯」開啟帳號視窗；不能變更自己的角色或停用自己。",
        "「刪除」會一併清理該使用者的申請紀錄；仍持有已開通資源時會被拒絕。",
    ),
    dialogs=(
        DialogSpec(
            id="user",
            title="新增使用者",
            opened_by="新增使用者",
            purpose="建立帳號，或（從「編輯」開啟時）修改既有帳號。",
            fields=(
                _f("Email", "登入用的帳號。", required=True),
                _f("姓名", "可留空。"),
                _f("密碼", "新增時必填；編輯時留空表示不變更。至少 8 個字元，且要同時有大寫、小寫、數字與特殊符號。LDAP 帳號不能設本地密碼。"),
                _f("角色", "學生、教師或管理者。"),
                _f("啟用帳戶", "取消勾選後無法登入。"),
                _f("強制兩步驟驗證", "勾選後對方登入時必須先完成綁定，綁定後不可自行停用。"),
            ),
            notes=("編輯已啟用 2FA 的帳號時，可以「重設兩步驟驗證」（例如手機遺失）。",),
        ),
    ),
    related=(
        RelatedPage("/ldap", "要讓帳號改由學校目錄管理"),
        RelatedPage("/governance", "要調整某人的資源配額"),
        RelatedPage("/audit", "要查某個帳號做過的操作"),
    ),
))

_add("ai-api-review", SurfaceGuide(
    when_to_use="有使用者申請 AI API 金鑰，要決定通過或拒絕。",
    features=(
        "用狀態篩選待審核、已通過、已拒絕；「用途」欄是審核的主要依據。",
        "每筆申請按「通過」或「拒絕」，通過後系統會直接核發 base_url 與 api_key。",
        "大量不合格的申請可以勾選後用「批量拒絕」，套用同一個理由。",
    ),
    dialogs=(
        DialogSpec(
            id="approve",
            title="通過 AI API 申請",
            opened_by="通過",
            purpose="確認申請者、金鑰名稱與用途後通過。",
            fields=(_f("審核備註", "可留空。"),),
        ),
        DialogSpec(
            id="reject",
            title="拒絕 AI API 申請",
            opened_by="拒絕",
            purpose="拒絕這筆申請。",
            fields=(_f("審核備註", "可留空，但寫上原因能讓申請者知道下一步。"),),
        ),
        DialogSpec(
            id="bulk-reject",
            title="批量拒絕 AI API 申請",
            opened_by="批量拒絕",
            purpose="一次拒絕勾選的待審核申請。",
            fields=(_f("共用拒絕理由", "這批申請會套用同一個理由。", required=True),),
        ),
    ),
    related=(
        RelatedPage("/ai-api-keys", "要管理已核發的金鑰"),
        RelatedPage("/ai-monitoring", "要看 AI 用量與成本"),
    ),
))

_add("ai-api-keys", SurfaceGuide(
    when_to_use="要查看全站已核發的 AI API 金鑰，或刪除外洩、濫用的金鑰。",
    features=(
        "依狀態切換啟用中、已停用或全部；「篩選」可依使用者身分與建立時間過濾。",
        "搜尋名稱、使用者、Email 或金鑰前綴。",
        "每把金鑰顯示擁有者、申請用途與最後使用時間；按「刪除」讓它立即無法再發起呼叫。",
    ),
    dialogs=(
        DialogSpec(
            id="revoke",
            title="確認刪除這把金鑰？",
            opened_by="刪除",
            purpose="刪除後使用者不再看到這把金鑰，也無法發起新的呼叫。",
            notes=("管理與用量紀錄會保留，已接受的呼叫會完成。",),
        ),
    ),
    related=(
        RelatedPage("/ai-monitoring", "要確認某把金鑰的用量"),
        RelatedPage("/ai-api-review", "要處理新的金鑰申請"),
    ),
))

_add("ai-monitoring", SurfaceGuide(
    when_to_use="要掌握 AI API 的整體用量、找出呼叫量或錯誤率異常的模型或使用者。",
    features=(
        "「模型」：依模型看呼叫量、Token、錯誤率與平均延遲。",
        "「金鑰 API 呼叫」：使用申請金鑰發出的呼叫紀錄與統計。",
        "「使用者用量」：依使用者看用量排行。",
    ),
    related=(
        RelatedPage("/ai-api-keys", "發現異常用量，要停用金鑰"),
    ),
))

from sqlmodel import Session, create_engine, select

from app.core.config import settings
from app.models import SystemSetup, User
from app.repositories import user as user_repo
from app.repositories.system_setup import SYSTEM_SETUP_ID
from app.schemas import UserCreate

engine = create_engine(
    str(settings.SQLALCHEMY_DATABASE_URI),
    connect_args={
        "client_encoding": "utf8",
        "connect_timeout": 10,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5,
    },
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_use_lifo=True,
    # 池上限（pool_size + max_overflow）必須 ≥ anyio threadpool 併發數（40），
    # 否則高併發下持有 threadpool token 的請求會互等連線而餓死。
    pool_size=10,
    max_overflow=40,
)


def end_read_transaction(session: Session) -> None:
    """結束目前的讀取交易，把 DB 連線還給連線池，已載入的 ORM 物件保持可用。

    Session 一查詢就自動開交易並佔住一條連線，直到 commit／rollback／close；
    請求層的 session 要到回應送出後才關。async 端點若在查完 DB 後 await
    PVE、LLM 等慢速 I/O，連線就在 idle in transaction 狀態下被抱著——這不受
    threadpool 上限約束，整班同時操作時會把連線池（與 PgBouncer 的 server
    連線）吃光。在慢速 await 之前呼叫這個函式即可讓出連線，之後再查 DB 會
    自動重新取一條。

    暫時關掉 expire_on_commit：否則已讀進來的物件會被標成過期，下次讀屬性時
    又在 await 途中（甚至在 event loop 上）重新開一段交易，等於白做。需要最新
    狀態的地方本來就會明確 refresh。會送出網路 COMMIT，async 程式碼要經
    threadpool 呼叫。

    只在純讀取時讓出：session 裡還有未送出的新增／修改／刪除時什麼都不做，
    避免把呼叫端之後才要決定去留的寫入提早 commit。持有
    ``pg_advisory_xact_lock`` 的交易不要呼叫（commit 會連鎖一起放掉）。
    """
    if not session.in_transaction():
        return
    if session.new or session.dirty or session.deleted:
        return
    expire_on_commit = session.expire_on_commit
    session.expire_on_commit = False
    try:
        session.commit()
    finally:
        session.expire_on_commit = expire_on_commit


# make sure all SQLModel models are imported (app.models) before initializing DB
# otherwise, SQLModel might fail to initialize relationships properly
# for more details: https://github.com/fastapi/full-stack-fastapi-template/issues/28


def init_db(session: Session) -> None:
    """建立 .env 指定的初始超級使用者（資料表由 Alembic migration 建立）。

    初始化精靈完成後就不再碰這個帳號：精靈會刻意停用 .env 的
    FIRST_SUPERUSER（密碼寫在 .env），之後管理員把它刪掉或改 email，
    若每次啟動都補建，就會重新冒出一個密碼已知的有效超級使用者。
    這裡直接讀 singleton，不走會自動插入列的 repository。
    """
    setup_state = session.get(SystemSetup, SYSTEM_SETUP_ID)
    if setup_state is not None and setup_state.completed:
        return

    ensure_first_superuser(session)


def ensure_first_superuser(session: Session) -> None:
    """不論初始化精靈狀態，確保 .env 的 FIRST_SUPERUSER 帳號存在。

    正式啟動一律走 init_db（精靈完成後不補建）；這支只給測試等
    明確需要該帳號的地方直接呼叫。
    """
    user = session.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            role="admin",
        )
        user_repo.create_user(session=session, user_create=user_in)
        session.commit()

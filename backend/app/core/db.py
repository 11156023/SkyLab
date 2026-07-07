from sqlmodel import Session, create_engine, select

from app.core.config import settings
from app.models import User
from app.repositories import user as user_repo
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


# make sure all SQLModel models are imported (app.models) before initializing DB
# otherwise, SQLModel might fail to initialize relationships properly
# for more details: https://github.com/fastapi/full-stack-fastapi-template/issues/28


def init_db(session: Session) -> None:
    # Tables should be created with Alembic migrations
    # But if you don't want to use migrations, create
    # the tables un-commenting the next lines
    # from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    # SQLModel.metadata.create_all(engine)

    user = session.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            role="admin",
            is_superuser=True,
        )
        user_repo.create_user(session=session, user_create=user_in)
        session.commit()

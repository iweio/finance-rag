"""MySQL 连接与会话管理。

与 v1 的差别（B15）：`create_all` 失败不再被静默吞掉 —— 记日志并置 `SCHEMA_OK=False`，
由 `/api/v1/health` 暴露 `mysql.schema`。v1 里 `try: create_all except: pass` 的写法，
后果是"库连上了但表不存在"，接口逐个 500 而 health 显示 ok。

测试用 SQLite 时（§6.2）：`DATABASE_URL=sqlite:///./dev.db` 即可，模型侧已加 `with_variant`；
内存库需要 StaticPool，否则每个连接看到的是不同的库。
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "mysql+pymysql://rag:rag_pwd@localhost:3306/rag_finance?charset=utf8mb4",
)

if DATABASE_URL.startswith("sqlite"):
    _kwargs: dict = {"connect_args": {"check_same_thread": False}, "pool_pre_ping": True}
    if ":memory:" in DATABASE_URL or DATABASE_URL.endswith("sqlite://"):
        _kwargs["poolclass"] = StaticPool
else:
    _kwargs = {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}

engine = create_engine(DATABASE_URL, echo=False, **_kwargs)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


# 建表是否成功。health 用它暴露 mysql.schema，而不是让接口逐个 500。
SCHEMA_OK: bool = False


def init_db() -> bool:
    """建表（幂等）。失败不抛，返回 False 并记日志 —— 由 health 暴露。"""
    global SCHEMA_OK
    try:
        Base.metadata.create_all(bind=engine)
        SCHEMA_OK = True
        logger.info("建表完成：%s", DATABASE_URL.rsplit("@", 1)[-1])
    except Exception as exc:  # noqa: BLE001 - 要把原因记下来，不能让进程起不来
        SCHEMA_OK = False
        logger.error("建表失败（接口会逐个报 50003，health 的 mysql.schema 会是 down）：%s", exc)
    return SCHEMA_OK


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

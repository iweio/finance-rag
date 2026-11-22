"""SQLAlchemy ORM 模型。与 契约/DB.md §2.2 的五张表逐字对应。

三处与 v1 不同的地方（见 文档/前后端补齐整改计划.md B17/B18）：
1. `qa_history.answer` 用 MEDIUMTEXT —— 契约 DDL 就是 MEDIUMTEXT，用 `Text`（MySQL TEXT，64KB）
   存长答案会抛 1406，而且是在 SSE 已经把答案吐完之后才抛。
2. 补齐 DDL 里的唯一约束与复合索引 —— `create_all` 不会自动加，缺了就会出现
   「DDL 文档和实际库不一致」，而 `_contract_check.py` 只比文档、发现不了。
3. 主键/时间类型加 `with_variant` —— 让同一套模型能在 SQLite 上跑测试（§6.2）：
   BIGINT PRIMARY KEY 在 SQLite 里不是 rowid 别名、autoincrement 不生效；
   `mysql.DATETIME(fsp=3)` 在 SQLite 上也没有对应实现。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger, DateTime, Float, Index, Integer, JSON, String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.dialects.mysql import DATETIME as MySQLDateTime
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import DATABASE_URL, Base

# ---- 跨方言类型：MySQL 保持原类型，SQLite 退化成等价类型（仅供测试）----
ID = BigInteger().with_variant(Integer(), "sqlite")
TS3 = MySQLDateTime(fsp=3).with_variant(DateTime(), "sqlite")

# DATETIME(3) 的默认值必须**带同样的 fsp**。实测 MySQL 8.0.45：
#   DEFAULT now()             -> 1067 Invalid default value
#   DEFAULT CURRENT_TIMESTAMP -> 1067 Invalid default value
#   DEFAULT CURRENT_TIMESTAMP(3) -> OK
# 契约 DB.md §2.2 写的就是 CURRENT_TIMESTAMP(3)，是 ORM 这边用 func.now()
# 渲染成了 now() 才对不上。SQLite 没有 fsp 语法，只能退回 CURRENT_TIMESTAMP，
# 而 server_default 不像类型那样有 with_variant，所以按方言各给一份文本。
TS3_DEFAULT = text("CURRENT_TIMESTAMP" if DATABASE_URL.startswith("sqlite")
                   else "CURRENT_TIMESTAMP(3)")


class User(Base):
    __tablename__ = "user"
    __table_args__ = (UniqueConstraint("username", name="uk_username"),)

    user_id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Session_(Base):
    __tablename__ = "session"
    __table_args__ = (Index("idx_user_created", "user_id", "created_at"),)

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ID, nullable=True)
    title: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class QaHistory(Base):
    __tablename__ = "qa_history"
    __table_args__ = (Index("idx_session_created", "session_id", "created_at"),)

    qa_id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False)
    sources: Mapped[list | None] = mapped_column(JSON, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(TS3, server_default=TS3_DEFAULT)


class Favorite(Base):
    __tablename__ = "favorite"
    __table_args__ = (
        UniqueConstraint("user_id", "clause_id", name="uk_clause"),
        UniqueConstraint("user_id", "qa_id", name="uk_qa"),
        Index("idx_user", "user_id"),
    )

    favorite_id: Mapped[int] = mapped_column(ID, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ID, nullable=True)
    clause_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    qa_id: Mapped[int | None] = mapped_column(ID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class KnowledgePoint(Base):
    __tablename__ = "knowledge_point"
    __table_args__ = (
        Index("idx_doc", "doc_id"),
        Index("idx_status", "status"),
        Index("idx_domain", "domain"),
        Index("idx_effdate", "effective_date"),
    )

    clause_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(32), nullable=False)
    doc_title: Mapped[str] = mapped_column(String(256), nullable=False)
    clause_no: Mapped[str] = mapped_column(String(64), nullable=False)
    clause_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter: Mapped[str | None] = mapped_column(String(256), nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(16), nullable=False, default="clause")
    text: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    org: Mapped[str] = mapped_column(String(128), nullable=False)
    doc_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publish_date: Mapped[int | None] = mapped_column(Integer, nullable=True)
    effective_date: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expire_date: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    fetched_at: Mapped[int] = mapped_column(Integer, nullable=False)

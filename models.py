"""
models.py -- FROZEN.

SQLAlchemy schemas for Kalam & Co.'s AI-readiness layer: CatalogItem,
Mandate, AuditLog. Do not add columns without explicit user approval
(see project instructions).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DB_PATH = "data/store.db"
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CatalogItem(Base):
    """A single SKU in Kalam & Co.'s catalog, raw + AI-enriched."""

    __tablename__ = "catalog_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Raw, messy merchant input -- kept verbatim, never overwritten.
    raw_name: Mapped[str] = mapped_column(String, nullable=False)
    raw_description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LLM-enriched fields.
    clean_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list[str]

    price_raw: Mapped[str | None] = mapped_column(String, nullable=True)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)

    stock: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terms: Mapped[str | None] = mapped_column(String, nullable=True)

    enrichment_reasoning: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "inferred" | "rule-matched" | "explicit"
    enrichment_confidence: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class Mandate(Base):
    """A buyer agent's pre-authorized spend envelope."""

    __tablename__ = "mandates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    agent_id: Mapped[str] = mapped_column(String, nullable=False)
    spend_cap: Mapped[float] = mapped_column(Float, nullable=False)
    category_scope: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list[str], empty/None = unrestricted

    # "one_time" | "recurring"
    mandate_type: Mapped[str] = mapped_column(String, nullable=False, default="one_time")
    # "active" | "expired" | "revoked"
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class AuditLog(Base):
    """Every AI-driven or money-adjacent decision, explainable and timestamped."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    timestamp: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    # "buyer_agent" | "merchant_system"
    actor: Mapped[str] = mapped_column(String, nullable=False)
    # "catalog_query" | "enrichment" | "negotiate_offer" | "mandate_check" | "payment"
    action: Mapped[str] = mapped_column(String, nullable=False)
    # "approved" | "blocked" | "info"
    decision: Mapped[str] = mapped_column(String, nullable=False)

    reason: Mapped[str] = mapped_column(Text, nullable=False)
    # "inferred" | "rule-matched" | "blocked-mandate-violation"
    reasoning_basis: Mapped[str | None] = mapped_column(String, nullable=True)

    linked_entity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    linked_entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    log_metadata: Mapped[dict | None] = mapped_column(JSON, nullable=True)


def init_db() -> None:
    """Create all tables if they don't already exist."""
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    print(f"Initialized {DB_PATH} with tables: {list(Base.metadata.tables.keys())}")

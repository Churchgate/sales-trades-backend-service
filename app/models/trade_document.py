from datetime import datetime

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String, func
from sqlmodel import Field, SQLModel


class TradeDocument(SQLModel, table=True):
    """One uploaded eligibility document for a Trade registration.

    Submitted directly through wtcabuja.com (not a dashboard-minted link) —
    see POST /trade/programs/{slug}/eligibility. Documents are company-level
    (CAC certificate, logo, ...), so they key off `registration_id`, not a
    single participant. `document_key` matches an entry in the owning
    program's `config["required_documents"]`; re-uploading the same key for
    the same registration replaces the previous row (and its stored object).

    Files themselves live in Cloudflare R2 (private bucket) — `storage_key`
    is the R2 object key; the row never stores file bytes or a public URL.
    """

    __tablename__ = "trade_documents"
    __table_args__ = (
        Index(
            "idx_trade_documents_registration_key",
            "registration_id",
            "document_key",
            unique=True,
        ),
        Index("idx_trade_documents_program", "trade_program_id"),
    )

    id: int | None = Field(
        default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True)
    )
    trade_program_id: int = Field(
        sa_column=Column(BigInteger, ForeignKey("trade_programs.id"), nullable=False)
    )
    registration_id: str = Field(sa_column=Column(String, nullable=False))
    document_key: str = Field(sa_column=Column(String, nullable=False))

    storage_key: str = Field(sa_column=Column(String, nullable=False))
    file_name: str
    content_type: str | None = None
    size_bytes: int = Field(sa_column=Column(BigInteger, nullable=False))

    uploaded_at: datetime = Field(
        sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )

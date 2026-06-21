import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text,
    ForeignKey, Enum as SAEnum, JSON, UniqueConstraint
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class CompanyType(enum.Enum):
    equity = "equity"
    investment_trust = "investment_trust"


class DocumentType(enum.Enum):
    annual = "annual"
    interim = "interim"
    trading_update = "trading_update"
    nav_announcement = "nav_announcement"


class PeriodType(enum.Enum):
    annual = "annual"
    interim = "interim"


class LineType(enum.Enum):
    revenue = "revenue"
    ebitda = "ebitda"
    operating_profit = "operating_profit"
    net_profit = "net_profit"
    eps_reported = "eps_reported"
    eps_adjusted = "eps_adjusted"
    net_debt = "net_debt"
    operating_cash_flow = "operating_cash_flow"
    capex = "capex"
    free_cash_flow = "free_cash_flow"
    dps = "dps"
    shares_outstanding = "shares_outstanding"
    capital_employed = "capital_employed"
    interest_expense = "interest_expense"
    nav_per_share = "nav_per_share"


class ReviewStatus(enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class GuidanceDirection(enum.Enum):
    ahead = "ahead"
    inline = "inline"
    behind = "behind"
    unclear = "unclear"


class Company(Base):
    __tablename__ = "companies"

    ticker = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    market = Column(String)
    sector = Column(String)
    sub_sector = Column(String)
    isin = Column(String)
    company_type = Column(SAEnum(CompanyType), default=CompanyType.equity)
    blacklisted = Column(Boolean, default=False)
    in_watchlist = Column(Boolean, default=False)
    in_portfolio = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    documents = relationship("Document", back_populates="company")
    financial_periods = relationship("FinancialPeriod", back_populates="company")
    forecast_entries = relationship("ForecastEntry", back_populates="company")
    company_notes = relationship("CompanyNote", back_populates="company", order_by="CompanyNote.created_at.desc()")
    share_prices = relationship("SharePrice", back_populates="company")
    table_mappings = relationship("TableMapping", back_populates="company")


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, ForeignKey("companies.ticker"), nullable=False)
    document_type = Column(SAEnum(DocumentType), nullable=False)
    source_url = Column(String)
    file_path = Column(String)
    period_end_date = Column(String)
    raw_text = Column(Text)
    uploaded_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime)

    company = relationship("Company", back_populates="documents")
    extracted_tables = relationship("ExtractedTable", back_populates="document")
    trading_update_analyses = relationship("TradingUpdateAnalysis", back_populates="document")
    financial_periods = relationship("FinancialPeriod", back_populates="source_document")


class FinancialPeriod(Base):
    __tablename__ = "financial_periods"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, ForeignKey("companies.ticker"), nullable=False)
    period_end_date = Column(String, nullable=False)
    period_type = Column(SAEnum(PeriodType), nullable=False)
    is_audited = Column(Boolean, default=False)
    source_document_id = Column(Integer, ForeignKey("documents.id"))

    __table_args__ = (UniqueConstraint("ticker", "period_end_date", "period_type"),)

    company = relationship("Company", back_populates="financial_periods")
    source_document = relationship("Document", back_populates="financial_periods")
    line_items = relationship("FinancialLineItem", back_populates="period")
    adjustments = relationship("Adjustment", back_populates="period")


class FinancialLineItem(Base):
    __tablename__ = "financial_line_items"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("financial_periods.id"), nullable=False)
    line_type = Column(SAEnum(LineType), nullable=False)
    value = Column(Float)
    is_adjusted = Column(Boolean, default=False)
    adjustment_description = Column(String)
    version = Column(Integer, default=1)
    is_canonical = Column(Boolean, default=True)

    period = relationship("FinancialPeriod", back_populates="line_items")


class Adjustment(Base):
    __tablename__ = "adjustments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_id = Column(Integer, ForeignKey("financial_periods.id"), nullable=False)
    line_type = Column(SAEnum(LineType), nullable=False)
    adjustment_name = Column(String)
    adjustment_value = Column(Float)
    is_accepted = Column(Boolean, default=False)

    period = relationship("FinancialPeriod", back_populates="adjustments")


class ExtractedTable(Base):
    __tablename__ = "extracted_tables"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    table_index = Column(Integer)
    raw_data = Column(JSON)
    mapping_id = Column(Integer, ForeignKey("table_mappings.id"), nullable=True)
    review_status = Column(SAEnum(ReviewStatus), default=ReviewStatus.pending)

    document = relationship("Document", back_populates="extracted_tables")
    mapping = relationship("TableMapping")


class TableMapping(Base):
    __tablename__ = "table_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, ForeignKey("companies.ticker"), nullable=False)
    document_type = Column(SAEnum(DocumentType), nullable=False)
    mapping_config = Column(JSON)
    last_used_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="table_mappings")


class ForecastEntry(Base):
    __tablename__ = "forecast_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, ForeignKey("companies.ticker"), nullable=False)
    metric_type = Column(String, nullable=False)
    value = Column(Float)
    period_end_date = Column(String)
    entered_at = Column(DateTime, default=datetime.utcnow)
    source_notes = Column(Text)

    company = relationship("Company", back_populates="forecast_entries")


class CompanyNote(Base):
    __tablename__ = "company_notes"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, ForeignKey("companies.ticker"), nullable=False)
    note_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company", back_populates="company_notes")


class SharePrice(Base):
    __tablename__ = "share_prices"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ticker = Column(String, ForeignKey("companies.ticker"), nullable=False)
    price_date = Column(String, nullable=False)
    close = Column(Float)
    market_cap = Column(Float)

    __table_args__ = (UniqueConstraint("ticker", "price_date"),)

    company = relationship("Company", back_populates="share_prices")


class TradingUpdateAnalysis(Base):
    __tablename__ = "trading_update_analysis"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False)
    metric_type = Column(String)
    guidance_direction = Column(SAEnum(GuidanceDirection))
    guidance_text = Column(Text)
    extrapolated_value = Column(Float)
    flagged_discrepancy = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    document = relationship("Document", back_populates="trading_update_analyses")

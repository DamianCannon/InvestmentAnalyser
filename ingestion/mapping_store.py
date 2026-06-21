from datetime import datetime
from sqlalchemy.orm import Session
from db.models import TableMapping, DocumentType


def get_mapping(session: Session, ticker: str, doc_type: str) -> dict | None:
    doc_type_enum = DocumentType(doc_type)
    mapping = (
        session.query(TableMapping)
        .filter_by(ticker=ticker, document_type=doc_type_enum)
        .order_by(TableMapping.last_used_at.desc())
        .first()
    )
    return mapping.mapping_config if mapping else None


def save_mapping(session: Session, ticker: str, doc_type: str, config: dict) -> TableMapping:
    doc_type_enum = DocumentType(doc_type)
    existing = (
        session.query(TableMapping)
        .filter_by(ticker=ticker, document_type=doc_type_enum)
        .first()
    )
    if existing:
        existing.mapping_config = config
        existing.last_used_at = datetime.utcnow()
        session.commit()
        return existing
    mapping = TableMapping(
        ticker=ticker,
        document_type=doc_type_enum,
        mapping_config=config,
    )
    session.add(mapping)
    session.commit()
    return mapping

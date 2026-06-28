import io
import re
from typing import Optional

_NUM_PATTERN = re.compile(r"[\d,.]+")


def filter_junk_tables(tables: list[dict]) -> list[dict]:
    """Return only tables that are likely to contain financial data."""
    return [t for t in tables if _is_likely_financial(t)]


def _is_likely_financial(tbl: dict) -> bool:
    headers = tbl.get("headers", [])
    rows = tbl.get("rows", [])

    if len(headers) < 2 or len(rows) < 2:
        return False

    # Need at least one non-blank header after the first column
    if not any(h.strip() for h in headers[1:]):
        return False

    # At least 15% of value cells (non-first-column) must contain a number
    value_cells = [cell for row in rows for cell in (row[1:] if len(row) > 1 else [])]
    if not value_cells:
        return False
    numeric_ratio = sum(1 for c in value_cells if _NUM_PATTERN.search(c.strip())) / len(value_cells)
    if numeric_ratio < 0.15:
        return False

    # Row labels should be short — long averages indicate narrative text blocks
    first_col = [row[0] for row in rows if row]
    if first_col and sum(len(c) for c in first_col) / len(first_col) > 80:
        return False

    return True


def render_page(file_bytes: bytes, page_num: int, dpi: int = 150) -> bytes:
    """Render a 0-indexed PDF page to PNG bytes using PyMuPDF."""
    try:
        import fitz  # pymupdf
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        if page_num >= len(doc):
            return b""
        pix = doc.load_page(page_num).get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        return pix.tobytes("png")
    except Exception:
        return b""


def extract_tables(file_bytes: bytes, filename: str = "upload.pdf") -> list[dict]:
    """Extract tables from PDF bytes. Returns list of {index, headers, rows, raw_df}."""
    tables = _extract_with_pdfplumber(file_bytes)
    if not tables:
        tables = _extract_with_camelot(file_bytes, filename)
    return tables


def _extract_with_pdfplumber(file_bytes: bytes) -> list[dict]:
    try:
        import pdfplumber
    except ImportError:
        return []

    results = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_tables = page.extract_tables()
                for tbl_idx, tbl in enumerate(page_tables):
                    if not tbl or len(tbl) < 2:
                        continue
                    headers = [str(c) if c else "" for c in tbl[0]]
                    rows = [[str(c) if c else "" for c in row] for row in tbl[1:]]
                    results.append({
                        "index": len(results),
                        "page": page_num + 1,
                        "headers": headers,
                        "rows": rows,
                        "source": "pdfplumber",
                    })
    except Exception:
        return []
    return results


def _extract_with_camelot(file_bytes: bytes, filename: str) -> list[dict]:
    try:
        import camelot
        import tempfile
        import os
    except ImportError:
        return []

    results = []
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = tmp.name

        tables = camelot.read_pdf(tmp_path, pages="all", flavor="lattice")
        for idx, tbl in enumerate(tables):
            df = tbl.df
            if df.empty or len(df) < 2:
                continue
            headers = [str(c) for c in df.iloc[0].tolist()]
            rows = [[str(v) for v in row] for row in df.iloc[1:].values.tolist()]
            results.append({
                "index": idx,
                "page": tbl.page,
                "headers": headers,
                "rows": rows,
                "source": "camelot",
            })
        os.unlink(tmp_path)
    except Exception:
        pass
    return results

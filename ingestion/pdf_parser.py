import io
from typing import Optional


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

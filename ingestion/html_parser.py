import requests
from bs4 import BeautifulSoup


def fetch_and_parse(url: str) -> tuple[list[dict], str]:
    """Fetch URL and extract tables + raw text. Returns (tables, raw_text)."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; InvestmentAnalyser/1.0)"
        )
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "lxml")

    tables = _extract_tables(soup)
    raw_text = _extract_text(soup)
    return tables, raw_text


def _extract_tables(soup: BeautifulSoup) -> list[dict]:
    results = []
    for idx, tbl in enumerate(soup.find_all("table")):
        headers = []
        rows = []

        header_row = tbl.find("tr")
        if header_row:
            headers = [th.get_text(strip=True) for th in header_row.find_all(["th", "td"])]

        for tr in tbl.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if any(cells):
                rows.append(cells)

        if rows:
            results.append({
                "index": idx,
                "page": 1,
                "headers": headers,
                "rows": rows,
                "source": "html",
            })
    return results


def _extract_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "nav", "header", "footer"]):
        tag.decompose()
    paragraphs = []
    for el in soup.find_all(["p", "div", "span", "h1", "h2", "h3", "h4", "li"]):
        text = el.get_text(separator=" ", strip=True)
        if len(text) > 40:
            paragraphs.append(text)
    seen = set()
    unique = []
    for p in paragraphs:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return "\n\n".join(unique)

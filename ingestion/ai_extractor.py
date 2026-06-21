import json
import os
import anthropic

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


LINE_TYPES = [
    "revenue", "ebitda", "operating_profit", "net_profit",
    "eps_reported", "eps_adjusted", "net_debt", "operating_cash_flow",
    "capex", "free_cash_flow", "dps", "shares_outstanding",
    "capital_employed", "interest_expense", "nav_per_share",
]


def propose_mapping(table: dict, ticker: str, doc_type: str) -> dict | None:
    """Ask Claude to identify financial line items in an extracted table.

    Returns mapping_config: {row_label -> line_type, period_columns: [col_name]}
    or None if the table doesn't contain recognisable financial data.
    """
    headers_str = " | ".join(table.get("headers", []))
    rows_preview = "\n".join(
        " | ".join(row) for row in table.get("rows", [])[:15]
    )
    prompt = f"""You are analyzing a financial table extracted from a {doc_type} report for {ticker}.

Table headers: {headers_str}
First rows:
{rows_preview}

Available line types: {", ".join(LINE_TYPES)}

If this table contains financial results (income statement, cash flow, or balance sheet data), return a JSON object with:
{{
  "is_financial": true,
  "period_columns": ["col1", "col2"],
  "row_mappings": {{
    "exact row label from table": "line_type"
  }}
}}

If the table does NOT contain financial results, return:
{{"is_financial": false}}

Return only the JSON object, no commentary."""

    client = _get_client()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
        else:
            return None

    if not result.get("is_financial"):
        return None
    return result


def analyze_trading_update(raw_text: str, historical_summary: str = "") -> list[dict]:
    """Extract forward-looking guidance from trading update text.

    Returns list of {metric, direction, quote, implied_value}.
    direction is one of: ahead, inline, behind, unclear
    """
    prompt = f"""You are analyzing a trading update / RNS announcement for a UK listed company.

Text:
{raw_text[:6000]}

{f"Historical context (last 3 periods):{chr(10)}{historical_summary}" if historical_summary else ""}

Extract all forward-looking statements about financial performance. For each one return a JSON object in this array:
[
  {{
    "metric": "revenue|ebitda|operating_profit|eps_reported|eps_adjusted|net_debt|free_cash_flow|dps|nav_per_share|other",
    "direction": "ahead|inline|behind|unclear",
    "quote": "exact quote from the text",
    "implied_value": null or numeric value if stated
  }}
]

Return only the JSON array. If there are no forward-looking statements, return []."""

    client = _get_client()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text.strip()
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]") + 1
        if start >= 0 and end > start:
            result = json.loads(text[start:end])
        else:
            return []
    return result if isinstance(result, list) else []

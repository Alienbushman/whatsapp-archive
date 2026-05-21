import re

_TICKER_RE = re.compile(r'\$([A-Z]{1,6})\b')

def extract_tickers(text: str) -> list[str]:
    """Return de-duplicated uppercase ticker symbols found in text (e.g. $NDX → 'NDX').

    Only matches uppercase sequences of 1-6 letters prefixed with $. Numeric
    amounts like $100 are excluded by the [A-Z]+ pattern.
    """
    seen: set[str] = set()
    result: list[str] = []
    for m in _TICKER_RE.finditer(text or ""):
        symbol = m.group(1)
        if symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
    return result

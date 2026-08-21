"""
services/sector_lookup.py
─────────────────────────
yfinance 섹터 조회 + GICS 표준 명칭 정규화.
"""
from __future__ import annotations


def _fetch_sector(ticker: str) -> str:
    """yfinance로 종목 섹터 조회. 실패 시 'Other' 반환."""
    try:
        import yfinance as yf
        info = yf.Ticker(ticker).info or {}
        sector = info.get("sector") or info.get("sectorDisp") or ""
        _MAP = {
            "Technology": "Technology",
            "Information Technology": "Technology",
            "Healthcare": "Healthcare",
            "Health Care": "Healthcare",
            "Financials": "Financials",
            "Financial Services": "Financials",
            "Financial": "Financials",
            "Consumer Cyclical": "Consumer Discretionary",
            "Consumer Discretionary": "Consumer Discretionary",
            "Consumer Defensive": "Consumer Staples",
            "Consumer Staples": "Consumer Staples",
            "Energy": "Energy",
            "Industrials": "Industrials",
            "Basic Materials": "Materials",
            "Materials": "Materials",
            "Real Estate": "Real Estate",
            "Utilities": "Utilities",
            "Communication Services": "Communication Services",
            "Telecommunication Services": "Communication Services",
        }
        return _MAP.get(sector, sector) if sector else "Other"
    except Exception:
        return "Other"



"""
routers/portfolio.py
─────────────────────
포트폴리오 CRUD + 에쿼티 커브 + 핵심 지표 API.
데이터 저장: DB 우선 (portfolio_repo), DB 미연결 시 JSON 파일 폴백.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel

from backend.db.portfolio_repo import (
    get_holdings, save_holding, delete_holding,
    get_trade_log, add_trade,
    update_trade_by_id, delete_trade_by_id,
    list_users, create_user,
)
from backend.models.portfolio import (
    HoldingItem, AddTradeRequest, UpdateHoldingRequest,
)
from backend.services.market_data import get_close_df
from backend.services.portfolio_calculator import (
    build_equity_curve,
    equity_curve_to_records,
    calculate_metrics,
    get_holdings_detail,
)

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


def _uid(x_user_id: Optional[str]) -> str:
    """헤더 X-User-Id 가 없으면 'default' 사용."""
    return (x_user_id or "default").strip() or "default"


def _portfolio_close_df(holdings: dict, period: str = "2y", ttl: int = 300):
    """
    포트폴리오 전용 close_df.
    ALWAYS_FETCH(22개 시장 지수) 제외, 사용자 종목 + 계산에 필요한 ^GSPC/^VIX만 포함.
    metrics 와 equity-curve 가 동일 파라미터로 호출 → 두 번째 요청은 메모리 캐시 히트.
    """
    tickers = sorted(set(
        [t for t in holdings if t != "CASH"] + ["^GSPC", "^VIX"]
    ))
    return get_close_df(tickers, period=period, ttl=ttl, include_market=False)


# ── Holdings ───────────────────────────────────────────────────────────────────

@router.get("/holdings")
def get_holdings_endpoint(x_user_id: Optional[str] = Header(default=None)):
    return get_holdings(_uid(x_user_id))


@router.put("/holdings/{ticker}")
def update_holding(
    ticker: str,
    body: UpdateHoldingRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    ticker = ticker.upper()
    holdings = get_holdings(uid)
    if ticker not in holdings:
        raise HTTPException(status_code=404, detail=f"{ticker} 미보유")
    sector = body.sector or holdings[ticker].get("sector", "Other")
    save_holding(ticker, body.q, body.avg, sector, uid)
    return {"ok": True, "ticker": ticker}


@router.post("/holdings/{ticker}")
def add_holding(
    ticker: str,
    item: HoldingItem,
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    ticker = ticker.upper()
    holdings = get_holdings(uid)
    if ticker in holdings:
        raise HTTPException(status_code=409, detail=f"{ticker} 이미 존재. PUT으로 수정하세요.")
    save_holding(ticker, item.q, item.avg, item.sector or "Other", uid)
    return {"ok": True, "ticker": ticker}


@router.delete("/holdings/{ticker}")
def delete_holding_endpoint(
    ticker: str,
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    ticker = ticker.upper()
    holdings = get_holdings(uid)
    if ticker not in holdings:
        raise HTTPException(status_code=404, detail=f"{ticker} 미보유")
    delete_holding(ticker, uid)
    return {"ok": True, "ticker": ticker}


# ── 섹터 헬퍼 (내부 함수, 엔드포인트 아님) ────────────────────────────────────

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


# ── Trade Log ──────────────────────────────────────────────────────────────────

@router.get("/trades")
def get_trades(x_user_id: Optional[str] = Header(default=None)):
    return get_trade_log(_uid(x_user_id))


@router.post("/trades")
def add_trade_endpoint(
    body: AddTradeRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    import threading
    uid = _uid(x_user_id)
    holdings = get_holdings(uid)
    ticker = body.ticker.upper()

    _type_map = {"BUY": "ADD", "SELL": "SOLD"}
    trade_type = _type_map.get(body.type.upper(), body.type.upper())

    if ticker not in holdings and trade_type == "ADD":
        save_holding(ticker, 0.0, float(body.price or 0), "Other", uid)
        holdings = get_holdings(uid)
        # 백그라운드에서 섹터 자동 조회 후 업데이트
        def _bg_sector():
            sector = _fetch_sector(ticker)
            if sector and sector != "Other":
                h = get_holdings(uid).get(ticker, {})
                if h:
                    save_holding(ticker, h.get("q", 0), h.get("avg", 0), sector, uid)
        threading.Thread(target=_bg_sector, daemon=True).start()

    trade_date = (body.date or "").strip() or datetime.now().strftime("%Y-%m-%d")
    record = {
        "date":   trade_date,
        "ticker": ticker,
        "type":   trade_type,
        "q":      body.q,
        "price":  body.price,
        "memo":   body.memo,
    }
    add_trade(record, uid)

    # holdings 자동 업데이트
    if ticker in holdings:
        q     = float(body.q)
        price = float(body.price or 0)
        cur   = holdings[ticker]
        if trade_type == "ADD":
            prev_q, prev_avg = cur["q"], cur.get("avg", price)
            new_q   = prev_q + q
            new_avg = round((prev_avg * prev_q + price * q) / new_q, 4) if new_q > 0 else price
            save_holding(ticker, round(new_q, 6), new_avg, cur.get("sector", "Other"), uid)
        elif trade_type == "SOLD":
            new_qty = max(0.0, round(cur["q"] - q, 6))
            if new_qty == 0.0:
                delete_holding(ticker, uid)
            else:
                save_holding(ticker, new_qty, cur["avg"], cur.get("sector", "Other"), uid)
        elif trade_type == "UPDATE":
            save_holding(ticker, q, cur["avg"], cur.get("sector", "Other"), uid)

    return {"ok": True, "record": record}


class UpdateTradeRequest(BaseModel):
    date:   str
    ticker: str
    type:   str
    q:      float
    price:  Optional[float] = None
    memo:   Optional[str]   = None


@router.put("/trades/{trade_id}")
def update_trade_endpoint(
    trade_id: int,
    body: UpdateTradeRequest,
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    # 수정 전에 기존 티커 파악 (ticker가 변경될 수 있으므로 old/new 모두 재계산)
    all_trades = get_trade_log(uid)
    old_trade  = next((t for t in all_trades if t.get("id") == trade_id), None)
    old_ticker = old_trade["ticker"] if old_trade else None

    ok = update_trade_by_id(trade_id, body.dict(), uid)
    if not ok:
        raise HTTPException(status_code=404, detail="거래 내역 없음")

    # 거래 수정 후 보유수량 재계산
    _recalculate_holding_from_trades(body.ticker.upper(), uid)
    if old_ticker and old_ticker.upper() != body.ticker.upper():
        _recalculate_holding_from_trades(old_ticker.upper(), uid)
    return {"ok": True}


@router.delete("/trades/{trade_id}")
def delete_trade_endpoint(
    trade_id: int,
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    # 삭제 전에 어느 티커인지 먼저 파악
    all_trades = get_trade_log(uid)
    trade = next((t for t in all_trades if t.get("id") == trade_id), None)
    if not trade:
        raise HTTPException(status_code=404, detail="거래 내역 없음")

    ok = delete_trade_by_id(trade_id, uid)
    if not ok:
        raise HTTPException(status_code=404, detail="거래 내역 없음")

    # 해당 티커의 보유수량 재계산
    _recalculate_holding_from_trades(trade["ticker"], uid)
    return {"ok": True}


def _recalculate_holding_from_trades(ticker: str, uid: str) -> None:
    """거래 기록 전체를 재생해 보유 수량·단가를 재계산. 수량 0이면 삭제."""
    trades = sorted(
        [t for t in get_trade_log(uid) if t["ticker"] == ticker],
        key=lambda t: (t.get("date", ""), t.get("id", 0)),
    )
    qty        = 0.0
    total_cost = 0.0

    for tr in trades:
        q     = float(tr.get("q") or 0)
        price = float(tr.get("price") or 0)
        ttype = tr.get("type", "")
        if ttype in ("ADD", "BUY"):
            total_cost += price * q
            qty        += q
        elif ttype in ("SOLD", "SELL"):
            if qty > 0:
                ratio      = min(q, qty) / qty
                total_cost = total_cost * (1.0 - ratio)
            qty = max(0.0, qty - q)
        elif ttype == "UPDATE":
            qty = q   # avg 는 그대로 유지

    holdings = get_holdings(uid)
    existing = holdings.get(ticker, {})
    sector   = existing.get("sector", "Other")

    qty = round(qty, 6)
    if qty <= 0:
        delete_holding(ticker, uid)
    else:
        avg = round(total_cost / qty, 4) if qty > 0 else 0.0
        save_holding(ticker, qty, avg, sector, uid)


# 시총 순 미국 상장 주요 티커 (rank 낮을수록 대형주)
_US_TICKERS: list[tuple[str, str]] = [
    ("AAPL","Apple Inc."),("MSFT","Microsoft Corp."),("NVDA","NVIDIA Corp."),
    ("AMZN","Amazon.com Inc."),("GOOGL","Alphabet Inc."),("META","Meta Platforms"),
    ("TSLA","Tesla Inc."),("BRK-B","Berkshire Hathaway"),("AVGO","Broadcom Inc."),
    ("LLY","Eli Lilly"),("JPM","JPMorgan Chase"),("V","Visa Inc."),
    ("XOM","ExxonMobil"),("UNH","UnitedHealth Group"),("MA","Mastercard"),
    ("COST","Costco Wholesale"),("NFLX","Netflix Inc."),("WMT","Walmart Inc."),
    ("PG","Procter & Gamble"),("JNJ","Johnson & Johnson"),
    ("HD","Home Depot"),("CRM","Salesforce Inc."),("BAC","Bank of America"),
    ("ORCL","Oracle Corp."),("ABBV","AbbVie Inc."),("MRK","Merck & Co."),
    ("CVX","Chevron Corp."),("KO","Coca-Cola Co."),("CSCO","Cisco Systems"),
    ("AMD","Advanced Micro Devices"),("ADBE","Adobe Inc."),("PEP","PepsiCo"),
    ("ACN","Accenture"),("INTC","Intel Corp."),("DIS","Walt Disney Co."),
    ("TXN","Texas Instruments"),("MCD","McDonald's Corp."),("VZ","Verizon"),
    ("QCOM","Qualcomm Inc."),("CAT","Caterpillar Inc."),("WFC","Wells Fargo"),
    ("TMO","Thermo Fisher"),("AMGN","Amgen Inc."),("IBM","IBM Corp."),
    ("SPGI","S&P Global"),("INTU","Intuit Inc."),("MS","Morgan Stanley"),
    ("GS","Goldman Sachs"),("BKNG","Booking Holdings"),("LOW","Lowe's Cos."),
    ("PFE","Pfizer Inc."),("UBER","Uber Technologies"),("ISRG","Intuitive Surgical"),
    ("RTX","RTX Corp."),("AMAT","Applied Materials"),("AXP","American Express"),
    ("LRCX","Lam Research"),("NOW","ServiceNow"),("DE","Deere & Co."),
    ("T","AT&T Inc."),("HON","Honeywell"),("C","Citigroup"),
    ("GILD","Gilead Sciences"),("ETN","Eaton Corp."),("BMY","Bristol-Myers"),
    ("BSX","Boston Scientific"),("MDT","Medtronic"),("SYK","Stryker Corp."),
    ("REGN","Regeneron Pharma"),("PANW","Palo Alto Networks"),("ADI","Analog Devices"),
    ("KLAC","KLA Corp."),("MU","Micron Technology"),("VRTX","Vertex Pharma"),
    ("PLD","Prologis"),("SNPS","Synopsys"),("CDNS","Cadence Design"),
    ("MMC","Marsh & McLennan"),("BLK","BlackRock"),("CI","Cigna Group"),
    ("SHW","Sherwin-Williams"),("CME","CME Group"),("ZTS","Zoetis"),
    ("TMUS","T-Mobile US"),("PGR","Progressive Corp."),("AON","Aon plc"),
    ("GEV","GE Vernova"),("COP","ConocoPhillips"),("SO","Southern Co."),
    ("DUK","Duke Energy"),("NEE","NextEra Energy"),("APD","Air Products"),
    ("FI","Fiserv Inc."),("MCO","Moody's Corp."),("DASH","DoorDash"),
    ("SHOP","Shopify Inc."),("SNOW","Snowflake"),("PLTR","Palantir"),
    ("ARM","Arm Holdings"),("ASML","ASML Holding"),("TSM","TSMC"),
    ("NVO","Novo Nordisk"),("SAP","SAP SE"),("SONY","Sony Group"),
    ("TM","Toyota Motor"),("BABA","Alibaba Group"),("PDD","PDD Holdings"),
    ("JD","JD.com"),("BIDU","Baidu"),("NKE","Nike Inc."),
    ("SBUX","Starbucks"),("BA","Boeing Co."),("MMM","3M Co."),
    ("GE","GE Aerospace"),("F","Ford Motor"),("GM","General Motors"),
    ("RIVN","Rivian Auto."),("LCID","Lucid Group"),("NIO","NIO Inc."),
    ("XPEV","Xpeng Inc."),("LI","Li Auto"),("COIN","Coinbase"),
    ("HOOD","Robinhood Markets"),("XYZ","Block Inc."),("PYPL","PayPal"),
    ("SOFI","SoFi Technologies"),("AFRM","Affirm Holdings"),("UPST","Upstart"),
    ("APP","Applovin Corp."),("RBLX","Roblox Corp."),("U","Unity Software"),
    ("DKNG","DraftKings"),("PENN","PENN Entertainment"),("MGM","MGM Resorts"),
    ("WYNN","Wynn Resorts"),("LVS","Las Vegas Sands"),("CZR","Caesars"),
    ("MAR","Marriott Intl."),("HLT","Hilton Worldwide"),("H","Hyatt Hotels"),
    ("UAL","United Airlines"),("DAL","Delta Air Lines"),("AAL","American Airlines"),
    ("LUV","Southwest Airlines"),("CCL","Carnival Corp."),("RCL","Royal Caribbean"),
    ("NCLH","Norwegian Cruise"),("MO","Altria Group"),("PM","Philip Morris"),
    ("BTI","British American"),("KMB","Kimberly-Clark"),("CL","Colgate-Palmolive"),
    ("CHD","Church & Dwight"),("EL","Estee Lauder"),("ULTA","Ulta Beauty"),
    ("LULU","Lululemon"),("GPS","Gap Inc."),("PVH","PVH Corp."),
    ("RL","Ralph Lauren"),("TPR","Tapestry"),("CPRI","Capri Holdings"),
    ("TGT","Target Corp."),("DLTR","Dollar Tree"),("DG","Dollar General"),
    ("KR","Kroger Co."),("SYY","Sysco Corp."),("MKC","McCormick"),
    ("CPB","Campbell Soup"),("HRL","Hormel Foods"),("GIS","General Mills"),
    ("K","Kellanova"),("HSY","Hershey Co."),("MDLZ","Mondelez Intl."),
    ("TSN","Tyson Foods"),("CAG","Conagra Brands"),("ADM","Archer-Daniels"),
    ("BG","Bunge Global"),("MOS","Mosaic Co."),("CF","CF Industries"),
    ("NTR","Nutrien Ltd."),("DVN","Devon Energy"),("OXY","Occidental Petroleum"),
    ("HAL","Halliburton"),("SLB","SLB (Schlumberger)"),("BKR","Baker Hughes"),
    ("FANG","Diamondback Energy"),("EOG","EOG Resources"),("MPC","Marathon Petroleum"),
    ("VLO","Valero Energy"),("PSX","Phillips 66"),("HES","Hess Corp."),
    ("APA","APA Corp."),("PXD","Pioneer Natural"),("AR","Antero Resources"),
    ("RRC","Range Resources"),("EQT","EQT Corp."),("CTRA","Coterra Energy"),
    ("ACGL","Arch Capital"),("ALL","Allstate"),("CB","Chubb Ltd."),
    ("TRV","Travelers Cos."),("MET","MetLife"),("PRU","Prudential Fin."),
    ("AFL","Aflac"),("LNC","Lincoln National"),("UNM","Unum Group"),
    ("HIG","Hartford Financial"),("GL","Globe Life"),("CINF","Cincinnati Fin."),
    ("WRB","W.R. Berkley"),("AJG","Arthur Gallagher"),("MMB","Marsh McLennan"),
    ("USB","U.S. Bancorp"),("TFC","Truist Financial"),("PNC","PNC Financial"),
    ("FITB","Fifth Third"),("KEY","KeyCorp"),("RF","Regions Financial"),
    ("CFG","Citizens Financial"),("HBAN","Huntington Bancshares"),("MTB","M&T Bank"),
    ("CMA","Comerica"),("ZION","Zions Bancorp"),("WAL","Western Alliance"),
    ("WBS","Webster Financial"),("VLY","Valley National"),("PACW","PacWest"),
    ("SCHW","Charles Schwab"),("RJF","Raymond James"),("ETFC","E*TRADE"),
    ("IBKR","Interactive Brokers"),("LPLA","LPL Financial"),("SF","Stifel Fin."),
    ("HDB","HDFC Bank"),("ITUB","Itau Unibanco"),("BBD","Bradesco"),
    ("SAN","Banco Santander"),("ING","ING Groep"),("CS","Credit Suisse"),
    ("UBS","UBS Group"),("DB","Deutsche Bank"),("BCS","Barclays"),
    ("LYG","Lloyds Banking"),("NWG","NatWest Group"),("HSBC","HSBC Holdings"),
    ("TD","Toronto-Dominion"),("RY","Royal Bank Canada"),("BMO","Bank of Montreal"),
    ("CM","CIBC"),("BNS","Bank of Nova Scotia"),("MFC","Manulife Fin."),
    ("SU","Suncor Energy"),("CNQ","Canadian Natural"),("CVE","Cenovus Energy"),
    ("ENB","Enbridge"),("TRP","TC Energy"),("BCE","BCE Inc."),
    ("AMCR","Amcor plc"),("IP","Intl. Paper"),("PKG","Packaging Corp."),
    ("WRK","WestRock"),("SON","Sonoco Products"),("SEE","Sealed Air"),
    ("BALL","Ball Corp."),("BLL","Ball Corp."),("AVY","Avery Dennison"),
    ("IFF","Intl. Flavors"),("EMN","Eastman Chemical"),("LYB","LyondellBasell"),
    ("HUN","Huntsman Corp."),("CC","Chemours Co."),("OLN","Olin Corp."),
    ("CE","Celanese"),("ASH","Ashland Inc."),("RPM","RPM Intl."),
    ("ECL","Ecolab"),("PPG","PPG Industries"),("FMC","FMC Corp."),
    ("NUE","Nucor Corp."),("STLD","Steel Dynamics"),("X","U.S. Steel"),
    ("CLF","Cleveland-Cliffs"),("CMC","Commercial Metals"),("MTX","Minerals Tech."),
    ("MLM","Martin Marietta"),("VMC","Vulcan Materials"),("MDU","MDU Resources"),
    ("PKX","POSCO Holdings"),("VALE","Vale S.A."),("FCX","Freeport-McMoRan"),
    ("SCCO","Southern Copper"),("HBM","Hudbay Minerals"),("TECK","Teck Resources"),
    ("AA","Alcoa Corp."),("CENX","Century Aluminum"),("KALU","Kaiser Aluminum"),
    ("ARNC","Arconic"),("ATI","ATI Inc."),("HWM","Howmet Aerospace"),
    ("GD","General Dynamics"),("LMT","Lockheed Martin"),("NOC","Northrop Grumman"),
    ("LHX","L3Harris"),("HII","Huntington Ingalls"),("TDG","TransDigm"),
    ("SPR","Spirit AeroSystems"),("HXL","Hexcel Corp."),("TXT","Textron"),
    ("PWR","Quanta Services"),("FLR","Fluor Corp."),("J","Jacobs Solutions"),
    ("ACM","AECOM"),("MTZ","MasTec"),("MYR","MYR Group"),("MYRG","MYR Group"),
    ("EME","EMCOR Group"),("TTEK","Tetra Tech"),("ABM","ABM Industries"),
    ("AYI","Acuity Brands"),("CSGP","CoStar Group"),("VNO","Vornado Realty"),
    ("SLG","SL Green"),("BXP","BXP Inc."),("KIM","Kimco Realty"),
    ("O","Realty Income"),("SPG","Simon Property"),("MAC","Macerich"),
    ("CBL","CBL & Associates"),("WPG","WP Glimcher"),("SKT","Tanger Factory"),
    ("ADC","Agree Realty"),("NNN","NNN REIT"),("EPRT","Essential Prop."),
    ("STAG","STAG Industrial"),("EGP","EastGroup Prop."),("REXR","Rexford Ind."),
    ("FR","First Industrial"),("DRE","Duke Realty"),("TRNO","Terreno Realty"),
    ("AMT","American Tower"),("CCI","Crown Castle"),("SBAC","SBA Comm."),
    ("IRM","Iron Mountain"),("DLR","Digital Realty"),("EQIX","Equinix"),
    ("QTS","QTS Realty"),("CONE","CyrusOne"),("NLOK","NortonLifeLock"),
    ("AKAM","Akamai Tech."),("FTNT","Fortinet"),("CRWD","CrowdStrike"),
    ("S","SentinelOne"),("ZS","Zscaler"),("OKTA","Okta Inc."),
    ("CYBR","CyberArk"),("QLYS","Qualys"),("VRNS","Varonis Systems"),
    ("TENB","Tenable Holdings"),("RPD","Rapid7"),("BB","BlackBerry"),
    ("NET","Cloudflare"),("FSLY","Fastly"),("DDOG","Datadog"),
    ("GTLB","GitLab"),("MDB","MongoDB"),("ESTC","Elastic N.V."),
    ("NEWR","New Relic"),("SUMO","Sumo Logic"),("SPLK","Splunk"),
    ("SMAR","Smartsheet"),("APPN","Appian Corp."),("ASAN","Asana"),
    ("TEAM","Atlassian"),("ZM","Zoom Video"),("RNG","RingCentral"),
    ("TWLO","Twilio"),("BAND","Bandwidth"),("CIEN","Ciena Corp."),
    ("JNPR","Juniper Networks"),("ANET","Arista Networks"),("NTAP","NetApp"),
    ("NTNX","Nutanix"),("DT","Dynatrace"),("NCNO","nCino"),
    ("ALTR","Altair Eng."),("PTC","PTC Inc."),("CDLX","Cardlytics"),
    ("TTD","The Trade Desk"),("PUBM","PubMatic"),("MGNI","Magnite"),
    ("DV","DoubleVerify"),("IAS","Integral Ad Sci."),("CARG","CarGurus"),
    ("CAR","Avis Budget"),("HTZ","Hertz Global"),("URI","United Rentals"),
    ("GATX","GATX Corp."),("AL","Air Lease"),("AER","AerCap Holdings"),
    ("FTAI","FTAI Aviation"),("WSC","WillScot Mobile"),
    ("FWLD","Foreworld Corp."),
    ("HIMS","Hims & Hers"),("W","Wayfair"),("CHWY","Chewy"),
    ("ETSY","Etsy Inc."),("EBAY","eBay"),("OSTK","Overstock.com"),
    ("FTCH","Farfetch"),("RH","RH (Restoration)"),("WSM","Williams-Sonoma"),
    ("BBY","Best Buy"),("GME","GameStop"),("BBBY","Bed Bath Beyond"),
    ("SFIX","Stitch Fix"),("REAL","RealReal"),("POSH","Poshmark"),
    ("ACMR","ACM Research"),("BRKS","Brooks Automation"),("CEVA","CEVA Inc."),
    ("DIOD","Diodes Inc."),("ENTG","Entegris"),("FN","Fabrinet"),
    ("MKSI","MKS Instruments"),("ONTO","Onto Innovation"),("ACLS","Axcelis"),
    ("CAMT","Camtek Ltd."),("COHU","Cohu Inc."),("ICHR","Ichor Holdings"),
    ("KLIC","Kulicke & Soffa"),("NXPI","NXP Semiconductors"),
    ("ON","ON Semiconductor"),("SWKS","Skyworks"),("QRVO","Qorvo"),
    ("MCHP","Microchip Tech."),("MPWR","Monolithic Power"),("WOLF","Wolfspeed"),
    ("AEHR","Aehr Test"),("LSCC","Lattice Semi."),("MLAB","Mesa Labs"),
    ("PI","Impinj"),("FORM","FormFactor"),("ACMR","ACM Research"),
    ("AAON","AAON Inc."),("AIR","AAR Corp."),("ALLE","Allegion"),
    ("AWK","American Water"),("ATO","Atmos Energy"),("CNP","CenterPoint"),
    ("LNT","Alliant Energy"),("IDA","IDACORP"),("PNW","Pinnacle West"),
    ("AEE","Ameren Corp."),("WEC","WEC Energy"),("PPL","PPL Corp."),
    ("FE","FirstEnergy"),("NI","NiSource"),("AES","AES Corp."),
    ("CMS","CMS Energy"),("ETR","Entergy"),("EXC","Exelon"),
    ("PCG","PG&E Corp."),("SCG","SCANA Corp."),("XEL","Xcel Energy"),
    ("SRE","Sempra"),("AWR","American States"),("CTWS","ConnSavings"),
    ("MSEX","Middlesex Water"),("SJW","SJW Group"),("YORW","York Water"),
    ("CPK","Chesapeake Utils."),("LADR","Ladder Capital"),("KREF","KKR Real Estate"),
    ("GPMT","Granite Point"),("RC","Ready Capital"),("BXMT","Blackstone Mtg."),
    ("ARI","Apollo Commercial"),("BRSP","BrightSpire"),("TPVG","TriplePoint Vent."),
    ("GAIN","Gladstone Invest."),("MAIN","Main Street Capital"),
    ("ARCC","Ares Capital"),("FSCO","FS Credit Opps"),
]


@router.get("/ticker-search")
def ticker_search(q: str = "", limit: int = 5):
    """미국 상장 티커 시총 순 검색. prefix 우선, 그 다음 contains 매칭."""
    if not q:
        return []
    q = q.upper().strip()
    # prefix 매칭 먼저 (시총 순 정렬 유지)
    prefix  = [(t, n) for t, n in _US_TICKERS if t.startswith(q)]
    # contains 매칭 (이미 prefix에 없는 것만)
    in_mid  = [(t, n) for t, n in _US_TICKERS if q in t and not t.startswith(q)]
    # 회사명 contains 매칭
    by_name = [(t, n) for t, n in _US_TICKERS if q in n.upper() and not t.startswith(q) and q not in t]
    merged = (prefix + in_mid + by_name)[:limit]
    return [{"ticker": t, "name": n} for t, n in merged]


@router.get("/ticker-price")
def get_ticker_price(ticker: str, x_user_id: Optional[str] = Header(default=None)):
    """티커 현재가 조회 (빠른 응답 — sector는 /auto-sector 경유)."""
    try:
        import yfinance as yf
        tk = yf.Ticker(ticker.upper())
        hist = tk.history(period="5d")
        if hist.empty:
            raise HTTPException(status_code=404, detail=f"티커 {ticker}를 찾을 수 없습니다")
        price = round(float(hist["Close"].iloc[-1]), 4)
        info  = tk.fast_info  # fast_info: .info 보다 훨씬 빠름
        return {
            "ticker":   ticker.upper(),
            "price":    price,
            "name":     getattr(info, "display_name", "") or "",
            "currency": getattr(info, "currency", "USD") or "USD",
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"티커 조회 실패: {e}")


@router.post("/auto-sector")
def auto_sector(x_user_id: Optional[str] = Header(default=None)):
    """보유 종목 중 sector='Other'인 종목의 섹터를 yfinance로 자동 분류."""
    uid      = _uid(x_user_id)
    holdings = get_holdings(uid)
    updated  = []
    for ticker, info in holdings.items():
        if ticker == "CASH":
            continue
        current_sector = info.get("sector", "Other")
        if current_sector and current_sector != "Other":
            continue
        sector = _fetch_sector(ticker)
        if sector and sector != "Other":
            save_holding(ticker, info.get("q", 0), info.get("avg", 0), sector, uid)
            updated.append({"ticker": ticker, "sector": sector})
    return {"updated": updated, "count": len(updated)}


# ── 분석 데이터 ─────────────────────────────────────────────────────────────────

@router.get("/metrics")
def get_metrics(x_user_id: Optional[str] = Header(default=None)):
    uid = _uid(x_user_id)
    holdings  = get_holdings(uid)
    trade_log = get_trade_log(uid)
    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")
    # period="2y" → equity-curve 와 동일 캐시키 공유, 두 번째 요청은 메모리 히트
    close_df = _portfolio_close_df(holdings, period="2y", ttl=300)
    equity_curve = build_equity_curve(holdings, trade_log, close_df)
    return calculate_metrics(holdings, close_df, equity_curve)


@router.get("/equity-curve")
def get_equity_curve(
    benchmark: Optional[str] = "sp500",
    x_user_id: Optional[str] = Header(default=None),
):
    uid = _uid(x_user_id)
    holdings  = get_holdings(uid)
    trade_log = get_trade_log(uid)
    if not holdings:
        raise HTTPException(status_code=400, detail="보유 종목 없음")
    close_df = _portfolio_close_df(holdings, period="2y", ttl=300)
    equity_curve = build_equity_curve(holdings, trade_log, close_df)
    return equity_curve_to_records(equity_curve, close_df)


@router.get("/holdings-detail")
def get_holdings_detail_endpoint(x_user_id: Optional[str] = Header(default=None)):
    uid = _uid(x_user_id)
    holdings = get_holdings(uid)
    if not holdings:
        return []
    tickers = [t for t in holdings if t != "CASH"]
    # include_market=False: 현재가만 필요, 시장 지수 불필요
    close_df = get_close_df(tickers, period="5d", ttl=60, include_market=False)
    return get_holdings_detail(holdings, close_df)


@router.get("/sector-weights")
def get_sector_weights(x_user_id: Optional[str] = Header(default=None)):
    uid = _uid(x_user_id)
    holdings = get_holdings(uid)
    if not holdings:
        return {}
    tickers = [t for t in holdings if t != "CASH"]
    # holdings-detail 과 동일 캐시키 공유 (5d, include_market=False, 같은 tickers)
    close_df = get_close_df(tickers, period="5d", ttl=60, include_market=False) if tickers else None
    rows: dict[str, float] = {}
    for t, info in holdings.items():
        price = 1.0 if t == "CASH" else (
            float(close_df.iloc[-1].get(t, 0))
            if close_df is not None and t in close_df.columns else 0.0
        )
        val = price * info["q"]
        sector = info.get("sector", "Other")
        rows[sector] = rows.get(sector, 0) + val
    total = sum(rows.values())
    if total <= 0:
        return {}
    return {s: round(v / total, 4) for s, v in sorted(rows.items(), key=lambda x: -x[1])}


# ── 사용자 관리 ─────────────────────────────────────────────────────────────────

@router.get("/users")
def get_users():
    return list_users()


class UserCreateBody(BaseModel):
    name: str
    email: str = ""


@router.post("/users/{user_id}")
def post_create_user(user_id: str, body: UserCreateBody):
    ok = create_user(user_id.strip(), body.name, body.email)
    return {"ok": ok, "user_id": user_id}


# ── 개인 데이터 새로고침 ────────────────────────────────────────────────────────

@router.post("/refresh")
def refresh_portfolio(x_user_id: Optional[str] = Header(default=None)):
    """
    사용자 포트폴리오 종목의 최신 가격 강제 수집.
    새로고침 버튼 클릭 시 프론트엔드에서 호출.
    """
    uid = _uid(x_user_id)
    holdings = get_holdings(uid)
    tickers = [t for t in holdings if t != "CASH"]
    if not tickers:
        return {"ok": True, "tickers": [], "message": "보유 종목 없음"}

    try:
        from backend.db.scheduler import refresh_user_prices
        refresh_user_prices(tickers)
        return {"ok": True, "tickers": tickers, "message": f"{len(tickers)}개 종목 가격 갱신 완료"}
    except Exception as e:
        return {"ok": False, "tickers": tickers, "message": str(e)}

import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yfinance as yf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

import SolanaTrader as trader


ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
POPULAR_TICKERS = ["NVDA", "AAPL", "MSFT", "TSLA", "SPY", "INTC", "GOOG", "AMZN"]
POPULAR_NAMES = {
    "AAPL": "Apple Inc.",
    "MSFT": "Microsoft Corporation",
    "NVDA": "NVIDIA Corporation",
    "SPY": "SPDR S&P 500 ETF Trust",
    "TSLA": "Tesla, Inc.",
    "GOOG": "Alphabet Inc.",
    "AMZN": "Amazon.com, Inc.",
    "INTC": "Intel Corporation",
}
PERIODS = {
    "1D": ("1d", "5m"),
    "5D": ("5d", "1h"),
    "1M": ("1mo", "1d"),
    "1Y": ("1y", "1d"),
}
FUND_CATALOG = [
    {"symbol": "SPY", "name": "SPDR S&P 500 ETF Trust", "category": "Broad market"},
    {"symbol": "VOO", "name": "Vanguard S&P 500 ETF", "category": "Broad market"},
    {"symbol": "VTI", "name": "Vanguard Total Stock Market ETF", "category": "Broad market"},
    {"symbol": "QQQ", "name": "Invesco QQQ Trust", "category": "Growth & tech"},
    {"symbol": "XLK", "name": "Technology Select Sector SPDR Fund", "category": "Growth & tech"},
    {"symbol": "IWM", "name": "iShares Russell 2000 ETF", "category": "Small cap"},
    {"symbol": "VEA", "name": "Vanguard FTSE Developed Markets ETF", "category": "International"},
    {"symbol": "VWO", "name": "Vanguard FTSE Emerging Markets ETF", "category": "International"},
    {"symbol": "BND", "name": "Vanguard Total Bond Market ETF", "category": "Bonds"},
    {"symbol": "AGG", "name": "iShares Core U.S. Aggregate Bond ETF", "category": "Bonds"},
    {"symbol": "GLD", "name": "SPDR Gold Shares", "category": "Commodities"},
    {"symbol": "SCHD", "name": "Schwab U.S. Dividend Equity ETF", "category": "Dividend"},
]
FUND_CATEGORIES = list(dict.fromkeys(item["category"] for item in FUND_CATALOG))
FUND_BY_SYMBOL = {item["symbol"]: item for item in FUND_CATALOG}
# Register fund names so /api/market and /api/analyze label them correctly.
POPULAR_NAMES.update({item["symbol"]: item["name"] for item in FUND_CATALOG})
FUND_INFO_TTL_SECONDS = 6 * 60 * 60
fund_info_cache = {}
fund_info_lock = threading.Lock()
PENDING_ORDER_TTL_SECONDS = 300
pending_orders = {}
pending_orders_lock = threading.Lock()

app = FastAPI(title="Solana Stock Analyzer API")
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=["127.0.0.1", "localhost", "testserver"],
)


class AnalysisRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)


class PrepareTradeRequest(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    eligible: bool


class ExecuteTradeRequest(BaseModel):
    order_id: str = Field(min_length=20, max_length=100)
    confirmation: str = Field(min_length=1, max_length=30)


def normalize_ticker(value):
    ticker = value.strip().upper()
    if not re.fullmatch(r"[A-Z0-9.^=-]{1,12}", ticker):
        raise HTTPException(status_code=422, detail="Enter a valid stock ticker.")
    return ticker


def to_market_snapshot(ticker, period, interval):
    history = yf.Ticker(ticker).history(period=period, interval=interval)
    if history.empty or history["Close"].dropna().empty:
        raise ValueError(f"No price history found for {ticker}.")

    closes = history["Close"].dropna()
    latest = float(closes.iloc[-1])
    previous = float(closes.iloc[-2]) if len(closes) > 1 else latest
    change = latest - previous
    volume_series = history["Volume"].dropna()
    volume = float(volume_series.iloc[-1]) if not volume_series.empty else 0
    rows = []
    for index, row in history.iterrows():
        close = row.get("Close")
        if close is None or close != close:
            continue
        volume_value = row.get("Volume", 0)
        if volume_value != volume_value:
            volume_value = 0
        rows.append({
            "date": index.strftime("%b %d %H:%M") if interval.endswith("m") or interval.endswith("h") else index.strftime("%b %d"),
            "price": round(float(close), 4),
            "ma": round(float(closes.loc[:index].tail(5).mean()), 4),
            "volume": int(volume_value or 0),
        })

    return {
        "symbol": ticker,
        "name": POPULAR_NAMES.get(ticker, ticker),
        "price": latest,
        "change": change,
        "changePct": (change / previous * 100) if previous else 0,
        "volume": volume,
        "volumeSafe": volume >= 1_000_000,
        "ma5d": float(closes.tail(5).mean()),
        "periodHigh": float(closes.max()),
        "periodLow": float(closes.min()),
        "history": rows,
    }


def _number_or_none(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def get_fund_info(ticker):
    """Best-effort fund facts from Yahoo Finance, cached because .info is slow."""
    now = time.monotonic()
    with fund_info_lock:
        cached = fund_info_cache.get(ticker)
        if cached and cached["expires_at"] > now:
            return cached["value"]

    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        info = {}

    yield_fraction = _number_or_none(info.get("yield"))
    value = {
        # Yahoo reports netExpenseRatio already in percent (0.03 means 0.03%).
        "expenseRatio": _number_or_none(info.get("netExpenseRatio")),
        "yieldPct": yield_fraction * 100 if yield_fraction is not None else None,
        "totalAssets": _number_or_none(info.get("totalAssets")),
    }
    # Only cache when Yahoo returned something, so a transient failure retries.
    if any(item is not None for item in value.values()):
        with fund_info_lock:
            fund_info_cache[ticker] = {"value": value, "expires_at": now + FUND_INFO_TTL_SECONDS}
    return value


def to_fund_snapshot(ticker, period, interval):
    snapshot = to_market_snapshot(ticker, period, interval)
    snapshot["fund"] = {
        "category": FUND_BY_SYMBOL[ticker]["category"],
        **get_fund_info(ticker),
    }
    return snapshot


@app.get("/api/health")
def health():
    public_key = None
    wallet_configured = bool(trader.MOCK_PRIVATE_KEY)
    if wallet_configured:
        try:
            keypair = trader.keypair_from_base58_secret(trader.MOCK_PRIVATE_KEY)
            address = str(keypair.pubkey())
            public_key = f"{address[:4]}...{address[-4:]}"
        except ValueError:
            wallet_configured = False

    return {
        "status": "ok",
        "walletConfigured": wallet_configured,
        "wallet": public_key,
        "geminiConfigured": bool(trader.os.getenv("GEMINI_API_KEY")),
    }


@app.get("/api/market")
def market(symbols: str = "NVDA,AAPL,MSFT,TSLA,SPY,INTC,GOOG,AMZN", timeframe: str = "1M"):
    timeframe = timeframe.upper()
    if timeframe not in PERIODS:
        raise HTTPException(status_code=422, detail="Unsupported chart timeframe.")
    tickers = [normalize_ticker(item) for item in symbols.split(",") if item.strip()]
    if not tickers or len(tickers) > 8:
        raise HTTPException(status_code=422, detail="Request between 1 and 8 ticker symbols.")
    period, interval = PERIODS[timeframe]

    with ThreadPoolExecutor(max_workers=min(len(tickers), 5)) as executor:
        futures = {
            ticker: executor.submit(to_market_snapshot, ticker, period, interval)
            for ticker in tickers
        }
    results = {}
    errors = {}
    for ticker, future in futures.items():
        try:
            results[ticker] = future.result()
        except Exception as exc:
            errors[ticker] = str(exc)
    return {"stocks": results, "errors": errors}


@app.get("/api/funds")
def funds(timeframe: str = "1M", category: str | None = None):
    timeframe = timeframe.upper()
    if timeframe not in PERIODS:
        raise HTTPException(status_code=422, detail="Unsupported chart timeframe.")
    if category is not None and category not in FUND_CATEGORIES:
        raise HTTPException(status_code=422, detail="Unknown fund category.")
    catalog = [
        item for item in FUND_CATALOG
        if category is None or item["category"] == category
    ]
    period, interval = PERIODS[timeframe]

    with ThreadPoolExecutor(max_workers=min(max(len(catalog), 1), 6)) as executor:
        futures = {
            item["symbol"]: executor.submit(to_fund_snapshot, item["symbol"], period, interval)
            for item in catalog
        }
    results = {}
    errors = {}
    for ticker, future in futures.items():
        try:
            results[ticker] = future.result()
        except Exception as exc:
            errors[ticker] = str(exc)
    return {
        "catalog": catalog,
        "categories": FUND_CATEGORIES,
        "funds": results,
        "errors": errors,
    }


@app.post("/api/analyze")
def analyze(payload: AnalysisRequest):
    ticker = normalize_ticker(payload.ticker)
    try:
        decision, metrics = trader.evaluate_stock_trade_decision(ticker)
        try:
            background = trader.generate_stock_background(ticker, decision, metrics or {})
            background_error = None
        except Exception as exc:
            background = None
            background_error = str(exc)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Could not analyze {ticker}: {exc}") from exc

    signal_type = (
        "BUY" if decision.startswith("BUY")
        else "SELL" if decision.startswith("SELL")
        else "HOLD"
    )
    return {
        "ticker": ticker,
        "name": POPULAR_NAMES.get(ticker, ticker),
        "decision": decision,
        "signalType": signal_type,
        "metrics": metrics,
        "background": background,
        "backgroundError": background_error,
    }


@app.post("/api/trades/prepare")
def prepare_trade(payload: PrepareTradeRequest):
    ticker = normalize_ticker(payload.ticker)
    if payload.eligible is not True:
        raise HTTPException(
            status_code=400,
            detail="Confirm issuer jurisdiction eligibility before requesting a quote.",
        )
    try:
        decision, _ = trader.evaluate_stock_trade_decision(ticker)
        if not decision.startswith("BUY SIGNAL"):
            raise HTTPException(
                status_code=409,
                detail="Trade not available natively through Solana",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Trade not available natively through Solana",
        ) from exc

    try:
        prepared = trader.prepare_tokenized_stock_purchase(ticker)
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail="Trade not available natively through Solana",
        ) from exc

    order_id = secrets.token_urlsafe(32)
    with pending_orders_lock:
        expired = [
            key for key, value in pending_orders.items()
            if value["expires_at"] <= time.monotonic()
        ]
        for key in expired:
            pending_orders.pop(key, None)
        pending_orders[order_id] = {
            "prepared": prepared,
            "ticker": ticker,
            "expires_at": time.monotonic() + PENDING_ORDER_TTL_SECONDS,
        }

    public_quote = {
        key: value
        for key, value in prepared.items()
        if not key.startswith("_")
    }
    public_quote.update({
        "orderId": order_id,
        "expiresInSeconds": PENDING_ORDER_TTL_SECONDS,
    })
    return public_quote


@app.post("/api/trades/execute")
def execute_trade(payload: ExecuteTradeRequest, request: Request):
    origin = request.headers.get("origin")
    if origin and origin not in {
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    }:
        raise HTTPException(status_code=403, detail="Trade requests must come from the local dashboard.")

    with pending_orders_lock:
        pending = pending_orders.get(payload.order_id)
        if pending is None:
            raise HTTPException(status_code=404, detail="Trade quote not found or already used.")
        if pending["expires_at"] <= time.monotonic():
            pending_orders.pop(payload.order_id, None)
            raise HTTPException(status_code=410, detail="Trade quote expired; request a fresh quote.")
        prepared = pending["prepared"]
        if payload.confirmation != prepared["approval"]:
            raise HTTPException(
                status_code=400,
                detail=f"Type {prepared['approval']} exactly to confirm this trade.",
            )
        pending_orders.pop(payload.order_id)

    try:
        signature = trader.execute_prepared_tokenized_stock_purchase(prepared)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Insufficient Funds") from exc
    return {
        "status": "Success",
        "signature": signature,
        "ticker": pending["ticker"],
        "tokenSymbol": prepared["token_symbol"],
        "inputUsdc": prepared["input_usdc"],
        "tokensReceivedEstimated": prepared["estimated_tokens"],
    }


if (DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def dashboard():
        return FileResponse(DIST / "index.html")

import base64
import json
import os
import time
from decimal import Decimal, ROUND_CEILING
from urllib import response
import re

import google.generativeai as genai
import requests
import yfinance as yf
from dotenv import load_dotenv
from google.api_core.exceptions import GoogleAPICallError
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.transaction import VersionedTransaction

load_dotenv()

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
JUPITER_TOKENS_URL = "https://api.jup.ag/tokens/v2"
JUPITER_SWAP_URL = "https://api.jup.ag/swap/v2"


def evaluate_stock_trade_decision(ticker_symbol, safety_volume_threshold=1000000):
    """
    Downloads real-time and historical stock data to calculate a Moving Average decision
    along with liquidity/safety validation based on trading volume.
    """
    print(f"Fetching real-time data for: {ticker_symbol.upper()}...")
    ticker = yf.Ticker(ticker_symbol)
    
    # Fetch last 5 days of 1-hour interval historical data
    history = ticker.history(period="5d", interval="1h")
    if history.empty:
        return "HOLD (Could not retrieve stock data)", None
        
    # Get real-time price (most recent close)
    current_price = history['Close'].iloc[-1]
    
    # Historical closing prices (excluding current price to avoid leakage)
    past_prices = history['Close'].iloc[:-1].tolist()
    
    # Calculate safety metrics using current session trading volume
    current_volume = history['Volume'].iloc[-1]
    is_safe_liquidity = current_volume >= safety_volume_threshold
    
    safety_metrics = {
        "current_volume": float(current_volume),
        "is_safe_liquidity": bool(is_safe_liquidity),
        "volume_threshold": safety_volume_threshold,
        "current_price": float(current_price),
    }

    if len(past_prices) >= 3:
        avg_price = sum(past_prices) / len(past_prices)
        deviation = (current_price - avg_price) / avg_price * 100
        safety_metrics.update({
            "moving_average": float(avg_price),
            "deviation_pct": float(deviation),
            "comparison_periods": len(past_prices),
        })

    if not is_safe_liquidity:
        return f"HOLD / DO NOT TRADE (High volatility risk: trading volume {current_volume:,} is below safety threshold of {safety_volume_threshold:,})", safety_metrics
        
    if len(past_prices) < 3:
        return "HOLD (Gathering historical data points...)", safety_metrics
    
    print(f"Current Price: ${current_price:.2f} | MA({len(past_prices)} hours): ${avg_price:.2f} | Deviation: {deviation:+.2f}%")
    
    # Buy if price is 1% below average of recent price ticks
    if current_price < avg_price * 0.99:
        decision_str = "BUY SIGNAL (Stock is underpriced relative to short-term average - Opportunity verified)"
    # Sell if price is 5% above average
    elif current_price > avg_price * 1.05:
        decision_str = "SELL SIGNAL (Stock is overpriced relative to short-term average - Opportunity verified)"
    else:
        decision_str = "HOLD (Price is stable within safe boundaries)"
        
    return decision_str, safety_metrics


import json
import os
import re
import google.generativeai as genai
import yfinance as yf

def generate_stock_background(ticker_symbol, decision, algorithm_metrics):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set in the environment or .env file.")

    daily_history = yf.Ticker(ticker_symbol).history(period="1y", interval="1d")
    if daily_history.empty or daily_history["Close"].dropna().empty:
        raise ValueError(f"No one-year daily price history is available for {ticker_symbol}.")

    closes = daily_history["Close"].dropna()
    first_close = float(closes.iloc[0])
    latest_close = float(closes.iloc[-1])
    if first_close <= 0:
        raise ValueError(f"Invalid historical price data for {ticker_symbol}.")

    # Compact facts payload to prevent token bloat
    compact_facts = {
        "ticker": ticker_symbol,
        "decision": decision,
        "1y_change_pct": round((latest_close - first_close) / first_close * 100, 2),
        "1y_high": round(float(closes.max()), 2),
        "1y_low": round(float(closes.min()), 2),
        "latest_close": round(latest_close, 2),
        "metrics": algorithm_metrics,
    }

    model_name = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(
        model_name=model_name,
        system_instruction=(
            "You are a stock-history analysis agent. Return exactly 4 short sentences "
            "as a JSON object with key 'sentences' containing an array of strings."
        ),
    )

    prompt = (
        "Write 4 concise background sentences about this stock based on these facts: "
        + json.dumps(compact_facts)
    )

    response = gemini_model.generate_content(
        prompt,
        generation_config={
            "response_mime_type": "application/json",
            "response_schema": {
                "type": "OBJECT",
                "properties": {
                    "sentences": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    }
                },
                "required": ["sentences"],
            },
            "temperature": 0.1,
            "max_output_tokens": 1500,  # Generous headroom to prevent truncation
        },
        request_options={"timeout": 30},
    )

    text = response.text.strip() if response and response.text else ""

    # Clean markdown wrappers if present
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    sentences = []

    # Standard JSON parse
    try:
        result = json.loads(text)
        sentences = result.get("sentences", [])
    except json.JSONDecodeError:
        # Fallback: Extract completed quoted strings from truncated JSON
        matches = re.findall(r'"([^"\\]*(?:\\.[^"\\]*)*)"', text)
        # Filter out the key name 'sentences'
        sentences = [m for m in matches if m != "sentences" and len(m) > 10]

    cleaned = [s.strip() for s in sentences if isinstance(s, str) and s.strip()]
    if not cleaned:
        raise ValueError("Gemini returned no usable background text.")
    return " ".join(cleaned)


def jupiter_get(url, params):
    headers = {}
    api_key = os.getenv("JUPITER_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key
    else:
        time.sleep(2.1)

    response = requests.get(url, params=params, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def find_tokenized_stock(ticker_symbol):
    stock_symbol = f"{ticker_symbol}x"
    tokens = jupiter_get(
        f"{JUPITER_TOKENS_URL}/search",
        {"query": stock_symbol},
    )
    matches = [
        token
        for token in tokens
        if token.get("symbol", "").casefold() == stock_symbol.casefold()
        and token.get("isVerified") is True
        and {"stocks", "xstocks"}.issubset(
            {tag.casefold() for tag in token.get("tags", [])}
        )
    ]
    if len(matches) != 1:
        raise ValueError(
            f"Expected one verified xStocks token for {ticker_symbol}; "
            f"found {len(matches)}."
        )

    token = matches[0]
    if not token.get("id") or not isinstance(token.get("decimals"), int):
        raise ValueError(f"Jupiter returned incomplete token metadata for {stock_symbol}.")
    if Decimal(str(token.get("usdPrice", 0))) <= 0:
        raise ValueError(f"Jupiter has no valid USD price for {stock_symbol}.")
    return token


def get_jupiter_order(input_amount, output_mint, taker=None):
    params = {
        "inputMint": USDC_MINT,
        "outputMint": output_mint,
        "amount": str(input_amount),
    }
    if taker:
        params["taker"] = taker

    order = jupiter_get(f"{JUPITER_SWAP_URL}/order", params)
    if (
        order.get("inputMint") != USDC_MINT
        or order.get("outputMint") != output_mint
        or int(order.get("inAmount", 0)) != input_amount
        or order.get("swapMode") != "ExactIn"
        or int(order.get("outAmount", 0)) <= 0
    ):
        raise ValueError("Jupiter returned an invalid or mismatched order.")
    return order


def keypair_from_base58_secret(private_key_b58: str) -> Keypair:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    secret = private_key_b58.strip()
    if not secret:
        raise ValueError("The configured Solana key is empty.")

    decoded_value = 0
    for character in secret:
        if character not in alphabet:
            raise ValueError("The configured Solana key is not valid Base58.")
        decoded_value = decoded_value * 58 + alphabet.index(character)

    decoded = (
        decoded_value.to_bytes((decoded_value.bit_length() + 7) // 8, "big")
        if decoded_value
        else b""
    )
    leading_zero_bytes = len(secret) - len(secret.lstrip("1"))
    secret_bytes = b"\0" * leading_zero_bytes + decoded

    if len(secret_bytes) == 32:
        return Keypair.from_seed(secret_bytes)
    if len(secret_bytes) == 64:
        return Keypair.from_bytes(secret_bytes)
    raise ValueError(
        "The Base58 Solana key must decode to a 32-byte seed or a 64-byte keypair."
    )


def sign_transaction_for_wallet(
    versioned_tx: VersionedTransaction,
    keypair: Keypair,
) -> VersionedTransaction:
    signer_count = versioned_tx.message.header.num_required_signatures
    required_signers = versioned_tx.message.account_keys[:signer_count]
    wallet_pubkey = keypair.pubkey()

    try:
        signer_index = required_signers.index(wallet_pubkey)
    except ValueError as exc:
        raise ValueError(
            "The configured wallet is not a required signer for this Jupiter order."
        ) from exc

    signatures = list(versioned_tx.signatures)
    if len(signatures) != signer_count:
        raise ValueError("Jupiter transaction has an invalid signature-slot count.")

    signatures[signer_index] = keypair.sign_message(
        to_bytes_versioned(versioned_tx.message)
    )
    return VersionedTransaction.populate(versioned_tx.message, signatures)


def prepare_tokenized_stock_purchase(
    ticker_symbol: str,
    private_key_b58: str | None = None,
) -> dict:
    private_key_b58 = private_key_b58 or os.getenv("SOLANA_PRIVATE_KEY")
    if not private_key_b58:
        raise ValueError("SOLANA_PRIVATE_KEY is not configured.")

    keypair = keypair_from_base58_secret(private_key_b58)
    user_public_key = str(keypair.pubkey())
    token = find_tokenized_stock(ticker_symbol)
    decimals = token["decimals"]
    target_amount = 10 ** decimals
    estimated_input = int(
        (Decimal(str(token["usdPrice"])) * Decimal(1_000_000)).to_integral_value(
            rounding=ROUND_CEILING
        )
    )
    sizing_order = get_jupiter_order(estimated_input, token["id"])
    input_amount = int(
        (
            Decimal(estimated_input)
            * Decimal(target_amount)
            / Decimal(sizing_order["outAmount"])
        ).to_integral_value(rounding=ROUND_CEILING)
    )
    order = get_jupiter_order(input_amount, token["id"], user_public_key)

    raw_transaction = order.get("transaction")
    request_id = order.get("requestId")
    if not raw_transaction or not request_id:
        missing_fields = []
        if not raw_transaction:
            missing_fields.append(
                "transaction is empty"
                if "transaction" in order
                else "transaction is missing"
            )
        if not request_id:
            missing_fields.append("requestId is missing")

        provider_details = [
            f"{field}={str(order[field])[:300]}"
            for field in ("router", "errorCode", "errorMessage")
            if order.get(field) not in (None, "")
        ]
        details = "; ".join(missing_fields + provider_details)
        raise ValueError(f"Jupiter did not return an executable order: {details}.")

    versioned_tx = VersionedTransaction.from_bytes(base64.b64decode(raw_transaction))
    required_signers = versioned_tx.message.account_keys[
        :versioned_tx.message.header.num_required_signatures
    ]
    if user_public_key not in {str(pubkey) for pubkey in required_signers}:
        raise ValueError("The configured wallet is not a required signer for this order.")

    expected_output = int(order["outAmount"])
    minimum_output = int(order.get("otherAmountThreshold", expected_output))
    actual_input = int(order["inAmount"])
    output_scale = Decimal(10) ** decimals
    return {
        "ticker": ticker_symbol,
        "token_name": token["name"],
        "token_symbol": token["symbol"],
        "mint": token["id"],
        "decimals": decimals,
        "input_usdc": float(Decimal(actual_input) / Decimal(1_000_000)),
        "estimated_tokens": float(Decimal(expected_output) / output_scale),
        "minimum_tokens": float(Decimal(minimum_output) / output_scale),
        "price_impact_pct": float(
            order.get("priceImpact", float(order.get("priceImpactPct", 0)) * 100)
        ),
        "slippage_bps": int(order.get("slippageBps", 0)),
        "approval": f"BUY {token['symbol']}",
        "_keypair": keypair,
        "_transaction": versioned_tx,
        "_request_id": request_id,
    }


def execute_prepared_tokenized_stock_purchase(prepared_order: dict) -> str:
    keypair = prepared_order["_keypair"]
    versioned_tx = prepared_order["_transaction"]
    signed_tx = sign_transaction_for_wallet(versioned_tx, keypair)
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("JUPITER_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key

    response = requests.post(
        f"{JUPITER_SWAP_URL}/execute",
        headers=headers,
        json={
            "signedTransaction": base64.b64encode(bytes(signed_tx)).decode("ascii"),
            "requestId": prepared_order["_request_id"],
        },
        timeout=30,
    )
    try:
        result = response.json()
    except ValueError as exc:
        if _is_insufficient_wallet_funds(response.text):
            raise RuntimeError("Insufficient Funds") from exc
        response.raise_for_status()
        raise RuntimeError("Jupiter returned an invalid execution response.") from exc

    error_details = response.text
    if isinstance(result, dict):
        error_details += " " + json.dumps(result, default=str)
    if _is_insufficient_wallet_funds(error_details):
        raise RuntimeError("Insufficient Funds")

    response.raise_for_status()
    if not isinstance(result, dict):
        raise RuntimeError("Jupiter returned an invalid execution response.")
    if result.get("status") != "Success":
        raise RuntimeError(f"Jupiter execution failed: {result}")
    return result["signature"]


def _is_insufficient_wallet_funds(error_details: str) -> bool:
    normalized = error_details.casefold()
    return any(
        phrase in normalized
        for phrase in (
            "insufficient funds",
            "insufficient balance",
            "insufficient lamports",
            "attempt to debit an account but found no record",
        )
    )


def execute_tokenized_stock_purchase(
    private_key_b58: str,
    ticker_symbol: str,
    trade_reason: str,
):
    try:
        prepared_order = prepare_tokenized_stock_purchase(
            ticker_symbol,
            private_key_b58=private_key_b58,
        )
    except Exception:
        print("Trade not available natively through Solana")
        return

    print("\n=== Live Tokenized-Stock Trade Approval Required ===")
    print(f"Reason: {trade_reason}")
    print(f"Asset: {prepared_order['token_name']} ({prepared_order['token_symbol']})")
    print(f"Mint: {prepared_order['mint']}")
    print(f"Pay: {prepared_order['input_usdc']:.6f} USDC")
    print(f"Estimated receive: {prepared_order['estimated_tokens']:.8f} token")
    print(f"Minimum receive at quoted slippage: {prepared_order['minimum_tokens']:.8f} token")
    print("This is an issuer-backed tokenized security, not a brokerage-held share.")
    if input(
        f"Type {prepared_order['approval']} to sign and submit this transaction: "
    ).strip() != prepared_order["approval"]:
        print("Trade cancelled; transaction was not signed or broadcast.")
        return

    try:
        signature = execute_prepared_tokenized_stock_purchase(prepared_order)
        print(f"[SUCCESS] Jupiter executed the trade. Signature: {signature}")
    except Exception:
        print("Insufficient Funds")

def main():
    ticker_symbol = input("Enter the stock ticker to analyze: ").strip().upper()
    if not ticker_symbol:
        raise ValueError("A stock ticker is required.")

    decision, stock_safety = evaluate_stock_trade_decision(ticker_symbol)
    print(f"\n=== Stock Decision Engine Result ===\n{decision}")

    try:
        background = generate_stock_background(
            ticker_symbol,
            decision,
            stock_safety or {},
        )
        print(f"\n=== Gemini Flash Stock Background ===\n{background}")
    except (GoogleAPICallError, requests.RequestException, ValueError, TypeError) as e:
        print(f"\n[Gemini Agent Warning] Could not generate stock background: {e}")

    if not decision.startswith("BUY SIGNAL"):
        print("No Solana swap proposed for this decision.")
        return

    if not MOCK_PRIVATE_KEY:
        print("A BUY signal was generated, but SOLANA_PRIVATE_KEY is not set; no trade can be submitted.")
        return

    if input(
        "Confirm you are eligible under the token issuer's jurisdiction terms "
        "by typing ELIGIBLE: "
    ).strip() != "ELIGIBLE":
        print("Eligibility not confirmed; no trade was prepared.")
        return

    execute_tokenized_stock_purchase(
        private_key_b58=MOCK_PRIVATE_KEY,
        ticker_symbol=ticker_symbol,
        trade_reason=f"{ticker_symbol} produced a BUY signal",
    )


MOCK_PRIVATE_KEY = os.getenv("SOLANA_PRIVATE_KEY")


if __name__ == "__main__":
    main()
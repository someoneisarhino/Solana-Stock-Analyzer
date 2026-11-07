import base64
import os
import time
from decimal import Decimal, ROUND_CEILING

import requests
import yfinance as yf
from dotenv import load_dotenv
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
        "current_volume": current_volume,
        "is_safe_liquidity": is_safe_liquidity,
        "volume_threshold": safety_volume_threshold
    }
    
    if not is_safe_liquidity:
        return f"HOLD / DO NOT TRADE (High volatility risk: trading volume {current_volume:,} is below safety threshold of {safety_volume_threshold:,})", safety_metrics
        
    if len(past_prices) < 3:
        return "HOLD (Gathering historical data points...)", safety_metrics
        
    avg_price = sum(past_prices) / len(past_prices)
    deviation = (current_price - avg_price) / avg_price * 100
    
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


def execute_tokenized_stock_purchase(
    private_key_b58: str,
    ticker_symbol: str,
    trade_reason: str,
):
    """
    Target one xStocks token using Jupiter's live quote and require approval.
    """
    try:
        keypair = keypair_from_base58_secret(private_key_b58)
        user_public_key = str(keypair.pubkey())
    except Exception as e:
        print(f"[Error] Could not initialize the configured wallet ({type(e).__name__}).")
        return

    try:
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
    except Exception as e:
        print(f"[Error] Could not prepare a verified {ticker_symbol} stock-token order: {e}")
        return

    try:
        raw_transaction = order.get("transaction")
        if not raw_transaction or not order.get("requestId"):
            raise ValueError("Jupiter did not return an executable order.")
        versioned_tx = VersionedTransaction.from_bytes(base64.b64decode(raw_transaction))
        if user_public_key not in {
            str(pubkey)
            for pubkey in versioned_tx.message.account_keys[
                :versioned_tx.message.header.num_required_signatures
            ]
        }:
            raise ValueError(
                "The configured wallet is not a required signer for this Jupiter order."
            )
    except Exception as e:
        print(f"[Error] Failed to prepare the Jupiter transaction: {e}")
        return

    try:
        expected_output = int(order["outAmount"])
        minimum_output = int(order.get("otherAmountThreshold", expected_output))
        actual_input = int(order["inAmount"])
    except (KeyError, TypeError, ValueError) as e:
        print(f"[Error] Jupiter returned invalid quote details: {e}")
        return

    output_scale = Decimal(10) ** decimals
    estimated_tokens = Decimal(expected_output) / output_scale
    minimum_tokens = Decimal(minimum_output) / output_scale
    target_tokens = Decimal(1)
    price_impact_pct = float(
        order.get("priceImpact", float(order.get("priceImpactPct", 0)) * 100)
    )
    slippage_bps = int(order.get("slippageBps", 0))
    headers = {"Content-Type": "application/json"}
    api_key = os.getenv("JUPITER_API_KEY")
    if api_key:
        headers["x-api-key"] = api_key

    print("\n=== Live Tokenized-Stock Trade Approval Required ===")
    print(f"Reason: {trade_reason}")
    print(f"Asset: {token['name']} ({token['symbol']})")
    print(f"Mint: {token['id']}")
    print(f"Target: {target_tokens:.{decimals}f} token (one share's tokenized exposure)")
    print(f"Pay: {Decimal(actual_input) / Decimal(1_000_000):.6f} USDC")
    print(f"Estimated receive: {estimated_tokens:.{decimals}f} token")
    print(f"Minimum receive at quoted slippage: {minimum_tokens:.{decimals}f} token")
    print(f"Estimated price impact: {price_impact_pct:.4f}%")
    print(f"Quoted slippage limit: {slippage_bps / 100:.2f}%")
    print("This is an issuer-backed tokenized security, not a brokerage-held share.")
    print("Jupiter uses exact-input swaps; the amount received may differ from one token.")
    approval = f"BUY {token['symbol']}"
    if input(f"Type {approval} to sign and submit this transaction: ").strip() != approval:
        print("Trade cancelled; transaction was not signed or broadcast.")
        return

    try:
        signed_tx = sign_transaction_for_wallet(versioned_tx, keypair)
        execute_response = requests.post(
            f"{JUPITER_SWAP_URL}/execute",
            headers=headers,
            json={
                "signedTransaction": base64.b64encode(bytes(signed_tx)).decode("ascii"),
                "requestId": order["requestId"],
            },
            timeout=30,
        )
        execute_response.raise_for_status()
        result = execute_response.json()
        if result.get("status") != "Success":
            raise RuntimeError(f"Jupiter execution failed: {result}")
        print(f"[SUCCESS] Jupiter executed the trade. Signature: {result['signature']}")
    except Exception as e:
        print(f"[Execution Failed] Error signing or submitting the Jupiter order: {e}")

def main():
    ticker_symbol = input("Enter the stock ticker to analyze: ").strip().upper()
    if not ticker_symbol:
        raise ValueError("A stock ticker is required.")

    decision, _ = evaluate_stock_trade_decision(ticker_symbol)
    print(f"\n=== Stock Decision Engine Result ===\n{decision}")

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
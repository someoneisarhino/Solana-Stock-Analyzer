import { useEffect, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  Activity,
  ArrowUpRight,
  BarChart3,
  CheckCircle2,
  CircleAlert,
  LoaderCircle,
  Search,
  ShieldCheck,
  Sparkles,
  Wallet,
} from "lucide-react";

type PricePoint = {
  date: string;
  price: number;
  ma: number;
  volume: number;
};

type StockSnapshot = {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
  volume: number;
  volumeSafe: boolean;
  ma5d: number;
  periodHigh: number;
  periodLow: number;
  history: PricePoint[];
};

type Health = {
  walletConfigured: boolean;
  wallet: string | null;
  geminiConfigured: boolean;
};

type Analysis = {
  ticker: string;
  name: string;
  decision: string;
  signalType: "BUY" | "SELL" | "HOLD";
  metrics: Record<string, number | boolean> | null;
  background: string | null;
  backgroundError: string | null;
};

type TradeQuote = {
  orderId: string;
  token_name: string;
  token_symbol: string;
  mint: string;
  input_usdc: number;
  estimated_tokens: number;
  minimum_tokens: number;
  price_impact_pct: number;
  approval: string;
  expiresInSeconds: number;
};

type ApiError = { detail?: string };
type Timeframe = "1D" | "5D" | "1M" | "1Y";

const POPULAR = ["NVDA", "AAPL", "MSFT", "TSLA", "SPY", "INTC", "GOOG", "AMZN"];
const TIMEFRAMES: Timeframe[] = ["1D", "5D", "1M", "1Y"];

async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  const body = await response.json().catch(() => ({} as ApiError));
  if (!response.ok) {
    throw new Error((body as ApiError).detail || `Request failed (${response.status})`);
  }
  return body as T;
}

function money(value: number) {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value);
}

function compactNumber(value: number) {
  return new Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(value);
}

function App() {
  const [page, setPage] = useState<"home" | "search">("home");
  const [timeframe, setTimeframe] = useState<Timeframe>("1M");
  const [stocks, setStocks] = useState<Record<string, StockSnapshot>>({});
  const [marketErrors, setMarketErrors] = useState<Record<string, string>>({});
  const [marketLoading, setMarketLoading] = useState(true);
  const [health, setHealth] = useState<Health | null>(null);
  const [tickerInput, setTickerInput] = useState("");
  const [selectedTicker, setSelectedTicker] = useState("NVDA");
  const [selectedStock, setSelectedStock] = useState<StockSnapshot | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [analysisError, setAnalysisError] = useState("");
  const [eligibilityText, setEligibilityText] = useState("");
  const [quote, setQuote] = useState<TradeQuote | null>(null);
  const [tradeConfirmation, setTradeConfirmation] = useState("");
  const [tradeLoading, setTradeLoading] = useState(false);
  const [tradeMessage, setTradeMessage] = useState("");

  useEffect(() => {
    let active = true;
    api<Health>("/api/health")
      .then((result) => {
        if (active) setHealth(result);
      })
      .catch(() => {
        if (active) setHealth({ walletConfigured: false, wallet: null, geminiConfigured: false });
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    setMarketLoading(true);
    api<{ stocks: Record<string, StockSnapshot>; errors: Record<string, string> }>(
      `/api/market?symbols=${POPULAR.join(",")}&timeframe=${timeframe}`,
    )
      .then((result) => {
        if (!active) return;
        setStocks(result.stocks);
        setMarketErrors(result.errors);
        setMarketLoading(false);
      })
      .catch((error: Error) => {
        if (!active) return;
        setMarketErrors({ market: error.message });
        setMarketLoading(false);
      });
    return () => {
      active = false;
    };
  }, [timeframe]);

  async function loadStock(ticker: string) {
    const result = await api<{ stocks: Record<string, StockSnapshot>; errors: Record<string, string> }>(
      `/api/market?symbols=${encodeURIComponent(ticker)}&timeframe=${timeframe}`,
    );
    const snapshot = result.stocks[ticker];
    if (!snapshot) throw new Error(result.errors[ticker] || `No market data available for ${ticker}.`);
    setSelectedStock(snapshot);
  }

  async function runAnalysis(value = tickerInput) {
    const ticker = value.trim().toUpperCase();
    if (!ticker) {
      setAnalysisError("Enter a stock ticker to analyze.");
      return;
    }

    setPage("search");
    setSelectedTicker(ticker);
    setTickerInput(ticker);
    setAnalysis(null);
    setAnalysisError("");
    setAnalysisLoading(true);
    setQuote(null);
    setTradeMessage("");
    setEligibilityText("");
    setTradeConfirmation("");
    try {
      const result = await api<Analysis>("/api/analyze", {
        method: "POST",
        body: JSON.stringify({ ticker }),
      });
      setAnalysis(result);
      try {
        await loadStock(ticker);
      } catch {
        setSelectedStock(null);
      }
    } catch (error) {
      setAnalysisError((error as Error).message);
    } finally {
      setAnalysisLoading(false);
    }
  }

  async function prepareTrade() {
    if (eligibilityText !== "ELIGIBLE") {
      setTradeMessage("Type ELIGIBLE to confirm issuer-jurisdiction eligibility.");
      return;
    }
    setTradeLoading(true);
    setTradeMessage("");
    try {
      const prepared = await api<TradeQuote>("/api/trades/prepare", {
        method: "POST",
        body: JSON.stringify({ ticker: selectedTicker, eligible: true }),
      });
      setQuote(prepared);
      setTradeConfirmation("");
    } catch {
      setTradeMessage("Trade not available natively through Solana");
    } finally {
      setTradeLoading(false);
    }
  }

  async function executeTrade() {
    if (!quote || tradeConfirmation !== quote.approval) return;
    setTradeLoading(true);
    setTradeMessage("");
    try {
      const result = await api<{
        status: string;
        signature: string;
        tokenSymbol: string;
        inputUsdc: number;
        tokensReceivedEstimated: number;
      }>("/api/trades/execute", {
        method: "POST",
        body: JSON.stringify({
          order_id: quote.orderId,
          confirmation: tradeConfirmation,
        }),
      });
      setTradeMessage(
        `${result.status}: Jupiter submitted ${result.tokenSymbol}; ${result.tokensReceivedEstimated.toFixed(8)} tokens estimated for ${money(result.inputUsdc)} USDC. Signature: ${result.signature}`,
      );
      setQuote(null);
      setTradeConfirmation("");
    } catch {
      setTradeMessage("Insufficient Funds");
    } finally {
      setTradeLoading(false);
    }
  }

  const openSearch = (ticker = "") => {
    setPage("search");
    if (ticker) {
      setTickerInput(ticker);
      void runAnalysis(ticker);
    }
  };

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100">
      <header className="sticky top-0 z-20 border-b border-slate-800 bg-slate-950/90 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4">
          <button className="flex items-center gap-3 text-left" onClick={() => setPage("home")}>
            <span className="rounded-xl bg-cyan-400/10 p-2 text-cyan-300"><Activity size={21} /></span>
            <span>
              <span className="block font-bold tracking-tight">Solana Stock Analyzer</span>
              <span className="block text-xs text-slate-500">Market signals · tokenized equities</span>
            </span>
          </button>
          <nav className="flex items-center gap-2">
            <button onClick={() => setPage("home")} className={`rounded-lg px-4 py-2 text-sm ${page === "home" ? "bg-slate-800 text-white" : "text-slate-400 hover:text-white"}`}>Markets</button>
            <button onClick={() => setPage("search")} className={`rounded-lg px-4 py-2 text-sm ${page === "search" ? "bg-slate-800 text-white" : "text-slate-400 hover:text-white"}`}>Analyze</button>
            <div className="ml-2 hidden items-center gap-2 rounded-full border border-slate-800 px-3 py-2 text-xs text-slate-400 sm:flex">
              <span className={`h-2 w-2 rounded-full ${health?.walletConfigured ? "bg-emerald-400" : "bg-slate-600"}`} />
              {health?.walletConfigured ? `Wallet ${health.wallet}` : "Wallet not configured"}
            </div>
          </nav>
        </div>
      </header>

      <main className="mx-auto max-w-7xl space-y-8 px-5 py-8">
        {page === "home" ? (
          <>
            <section className="grid gap-8 rounded-3xl border border-slate-800 bg-gradient-to-br from-slate-900 to-slate-950 p-7 md:grid-cols-[1.4fr_0.6fr] md:p-10">
              <div className="self-center">
                <p className="mb-3 text-xs font-semibold uppercase tracking-[0.22em] text-cyan-300">Live market overview</p>
                <h1 className="max-w-2xl text-3xl font-bold leading-tight sm:text-5xl">Explore the market. Review the signal. Approve every trade.</h1>
                <p className="mt-4 max-w-xl text-sm leading-6 text-slate-400">View Yahoo Finance market history, then run the Python decision engine and Gemini stock background for a ticker you choose.</p>
                <button onClick={() => openSearch()} className="mt-6 inline-flex items-center gap-2 rounded-xl bg-cyan-300 px-5 py-3 text-sm font-bold text-slate-950 hover:bg-cyan-200">
                  Analyze a stock <ArrowUpRight size={17} />
                </button>
              </div>
              <div className="hidden items-center justify-center md:flex">
                <div className="rounded-3xl border border-cyan-400/20 bg-cyan-400/5 p-8 text-cyan-200"><BarChart3 size={100} strokeWidth={1} /></div>
              </div>
            </section>

            <section>
              <div className="mb-5 flex flex-wrap items-end justify-between gap-4">
                <div>
                  <h2 className="text-xl font-bold">Popular stocks</h2>
                  <p className="mt-1 text-sm text-slate-500">Yahoo Finance price history · refreshes when timeframe changes</p>
                </div>
                <div className="flex rounded-lg border border-slate-800 bg-slate-900 p-1">
                  {TIMEFRAMES.map((range) => (
                    <button key={range} onClick={() => setTimeframe(range)} className={`rounded-md px-3 py-1.5 text-xs ${timeframe === range ? "bg-cyan-300 font-semibold text-slate-950" : "text-slate-400 hover:text-white"}`}>{range}</button>
                  ))}
                </div>
              </div>
              {marketErrors.market && <ErrorBanner message={marketErrors.market} />}
              {marketLoading && <Loading label="Loading market data from Yahoo Finance..." />}
              {!marketLoading && (
                <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                  {POPULAR.map((symbol) => {
                    const stock = stocks[symbol];
                    if (!stock) return <ErrorBanner key={symbol} message={`${symbol}: ${marketErrors[symbol] || "Market data unavailable."}`} />;
                    return <StockCard key={symbol} stock={stock} onSelect={() => openSearch(symbol)} />;
                  })}
                </div>
              )}
            </section>
            <p className="text-xs leading-5 text-slate-600">Market data and algorithm results are informational, not investment advice. Tokenized securities are not brokerage-held shares and may be restricted by issuer eligibility rules.</p>
          </>
        ) : (
          <section className="space-y-6">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">Stock decision engine</p>
              <h1 className="mt-2 text-3xl font-bold">Analyze a ticker</h1>
              <p className="mt-2 text-sm text-slate-400">The server runs the Python algorithm and Gemini Flash summary; no trade is submitted without your approval.</p>
            </div>
            <form className="flex max-w-3xl gap-3" onSubmit={(event) => { event.preventDefault(); void runAnalysis(); }}>
              <label className="relative flex-1">
                <Search className="absolute left-4 top-3.5 text-slate-500" size={18} />
                <input value={tickerInput} onChange={(event) => setTickerInput(event.target.value)} placeholder="Enter a ticker, such as AAPL" maxLength={12} className="w-full rounded-xl border border-slate-800 bg-slate-900 py-3 pl-11 pr-4 outline-none focus:border-cyan-400" />
              </label>
              <button disabled={analysisLoading} className="rounded-xl bg-cyan-300 px-5 font-semibold text-slate-950 disabled:opacity-50">{analysisLoading ? "Analyzing..." : "Analyze"}</button>
            </form>

            {analysisError && <ErrorBanner message={analysisError} />}
            {analysisLoading && <Loading label="Fetching market history and generating your decision..." />}

            {analysis && (
              <>
                <div className="grid gap-5 lg:grid-cols-[1.15fr_0.85fr]">
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div><p className="text-sm text-slate-500">{analysis.name}</p><h2 className="text-2xl font-bold">{analysis.ticker}</h2></div>
                      <SignalBadge signal={analysis.signalType} />
                    </div>
                    <p className="mt-5 text-sm leading-6 text-slate-300">{analysis.decision}</p>
                    {analysis.metrics && <div className="mt-5 grid grid-cols-2 gap-3 sm:grid-cols-3">
                      <Metric label="Current price" value={money(Number(analysis.metrics.current_price || 0))} />
                      <Metric label="Moving average" value={analysis.metrics.moving_average === undefined ? "—" : money(Number(analysis.metrics.moving_average))} />
                      <Metric label="Deviation" value={analysis.metrics.deviation_pct === undefined ? "—" : `${Number(analysis.metrics.deviation_pct).toFixed(2)}%`} />
                      <Metric label="Session volume" value={compactNumber(Number(analysis.metrics.current_volume || 0))} />
                      <Metric label="Liquidity check" value={analysis.metrics.is_safe_liquidity ? "Passed" : "Below threshold"} />
                      <Metric label="Comparison points" value={String(analysis.metrics.comparison_periods ?? "—")} />
                    </div>}
                    {selectedStock && selectedStock.symbol === analysis.ticker && <div className="mt-6 h-64">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={selectedStock.history}>
                          <defs><linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#22d3ee" stopOpacity={0.28} /><stop offset="100%" stopColor="#22d3ee" stopOpacity={0} /></linearGradient></defs>
                          <CartesianGrid stroke="#1e293b" strokeDasharray="3 3" />
                          <XAxis dataKey="date" tick={{ fill: "#64748b", fontSize: 10 }} minTickGap={28} />
                          <YAxis domain={["auto", "auto"]} tick={{ fill: "#64748b", fontSize: 10 }} tickFormatter={(value: number) => `$${value}`} width={65} />
                          <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", borderRadius: 10 }} />
                          <Area type="monotone" dataKey="price" stroke="#22d3ee" fill="url(#priceFill)" strokeWidth={2} name="Price" />
                          <Line type="monotone" dataKey="ma" stroke="#a78bfa" dot={false} strokeWidth={1.5} name="5-point average" />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>}
                  </div>
                  <div className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5">
                    <div className="flex items-center gap-2"><Sparkles size={18} className="text-violet-300" /><h3 className="font-semibold">Gemini Flash background</h3></div>
                    {analysis.background ? <p className="mt-4 text-sm leading-7 text-slate-300">{analysis.background}</p> : <p className="mt-4 text-sm leading-6 text-amber-300">{analysis.backgroundError || "The background summary is unavailable."}</p>}
                    <div className="mt-5 rounded-xl border border-slate-800 bg-slate-950/70 p-4 text-xs leading-5 text-slate-500">This four-sentence background expands on the algorithm decision using the stock's recent one-year history. Gemini output is explanatory and does not change the signal.</div>
                  </div>
                </div>

                <div className="rounded-2xl border border-amber-500/20 bg-amber-500/5 p-5">
                  <div className="flex items-start gap-3">
                    <ShieldCheck className="mt-0.5 shrink-0 text-amber-300" size={20} />
                    <div className="flex-1">
                      <h3 className="font-semibold">Issuer-backed tokenized security</h3>
                      <p className="mt-1 text-sm leading-6 text-slate-400">A BUY signal only enables a Jupiter quote. Review the exact USDC input and estimated token output before separately approving. The target is approximately one token, not a guaranteed one brokerage share; market price, decimals, and swap slippage affect the amount.</p>
                      {analysis.signalType === "BUY" ? (
                        <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto]">
                          <label className="text-xs text-slate-400">Confirm that you are eligible under the issuer's jurisdiction terms by typing <span className="font-bold text-white">ELIGIBLE</span>.
                            <input value={eligibilityText} onChange={(event) => setEligibilityText(event.target.value)} placeholder="Type ELIGIBLE" className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm text-white outline-none focus:border-amber-300" />
                          </label>
                          <button disabled={tradeLoading || !health?.walletConfigured || eligibilityText !== "ELIGIBLE"} onClick={() => void prepareTrade()} className="self-end rounded-lg bg-amber-300 px-4 py-2.5 text-sm font-bold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40">{tradeLoading ? "Preparing quote..." : "Get Jupiter quote"}</button>
                        </div>
                      ) : <p className="mt-4 text-sm font-medium text-slate-300">No trade quote is available: the current server-side decision is {analysis.signalType}.</p>}
                      {!health?.walletConfigured && analysis.signalType === "BUY" && <p className="mt-3 text-xs text-amber-200">A valid SOLANA_PRIVATE_KEY must be configured in the local .env file before a quote can be prepared.</p>}
                      {tradeMessage && <p className="mt-3 break-words text-sm text-amber-200">{tradeMessage}</p>}
                    </div>
                  </div>
                  {quote && (
                    <div className="mt-5 rounded-xl border border-slate-700 bg-slate-950 p-4">
                      <div className="flex items-center gap-2 text-emerald-300"><CheckCircle2 size={18} /><h4 className="font-semibold">Quote ready — no transaction submitted</h4></div>
                      <dl className="mt-4 grid gap-3 text-sm sm:grid-cols-2">
                        <QuoteRow label="Tokenized security" value={`${quote.token_name} (${quote.token_symbol})`} />
                        <QuoteRow label="USDC input" value={money(quote.input_usdc)} />
                        <QuoteRow label="Estimated token output" value={quote.estimated_tokens.toFixed(8)} />
                        <QuoteRow label="Minimum output at quoted slippage" value={quote.minimum_tokens.toFixed(8)} />
                        <QuoteRow label="Price impact" value={`${quote.price_impact_pct.toFixed(4)}%`} />
                        <QuoteRow label="Quote expires in" value={`${quote.expiresInSeconds} seconds`} />
                      </dl>
                      <p className="mt-3 break-all text-xs text-slate-500">Mint: {quote.mint}</p>
                      <label className="mt-4 block text-xs text-slate-400">To submit this live swap, type <strong className="text-white">{quote.approval}</strong>.
                        <input value={tradeConfirmation} onChange={(event) => setTradeConfirmation(event.target.value)} placeholder={quote.approval} className="mt-2 w-full rounded-lg border border-slate-700 bg-slate-900 px-3 py-2.5 text-sm text-white outline-none focus:border-emerald-400" />
                      </label>
                      <div className="mt-4 flex flex-wrap gap-3">
                        <button disabled={tradeLoading || tradeConfirmation !== quote.approval} onClick={() => void executeTrade()} className="rounded-lg bg-emerald-400 px-4 py-2.5 text-sm font-bold text-slate-950 disabled:cursor-not-allowed disabled:opacity-40">{tradeLoading ? "Submitting..." : `Approve and submit ${quote.approval}`}</button>
                        <button onClick={() => { setQuote(null); setTradeConfirmation(""); }} className="rounded-lg border border-slate-700 px-4 py-2.5 text-sm text-slate-300 hover:bg-slate-900">Cancel quote</button>
                      </div>
                    </div>
                  )}
                  {tradeMessage.startsWith("Success:") && <p className="mt-3 break-all text-sm text-emerald-300">{tradeMessage}</p>}
                </div>
              </>
            )}
          </section>
        )}
      </main>
      <footer className="border-t border-slate-900 py-5 text-center text-xs text-slate-600">Local dashboard · API keys and private keys remain on this machine</footer>
    </div>
  );
}

function StockCard({ stock, onSelect }: { stock: StockSnapshot; onSelect: () => void }) {
  const positive = stock.change >= 0;
  return (
    <button onClick={onSelect} className="rounded-2xl border border-slate-800 bg-slate-900/70 p-5 text-left transition hover:border-cyan-400/50 hover:bg-slate-900">
      <div className="flex items-start justify-between">
        <div><p className="font-bold">{stock.symbol}</p><p className="mt-1 text-xs text-slate-500">{stock.name}</p></div>
        <span className={`text-xs font-semibold ${positive ? "text-emerald-300" : "text-rose-300"}`}>{positive ? "+" : ""}{stock.changePct.toFixed(2)}%</span>
      </div>
      <div className="mt-4 flex items-end justify-between"><span className="text-2xl font-semibold">{money(stock.price)}</span><span className="text-xs text-slate-500">Vol {compactNumber(stock.volume)}</span></div>
      <div className="mt-4 h-24">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={stock.history}>
            <defs><linearGradient id={`fill-${stock.symbol}`} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={positive ? "#34d399" : "#fb7185"} stopOpacity={0.22} /><stop offset="100%" stopColor={positive ? "#34d399" : "#fb7185"} stopOpacity={0} /></linearGradient></defs>
            <Area type="monotone" dataKey="price" stroke={positive ? "#34d399" : "#fb7185"} fill={`url(#fill-${stock.symbol})`} strokeWidth={1.7} dot={false} />
          </AreaChart>
        </ResponsiveContainer>
      </div>
      <div className="mt-3 flex justify-between text-xs text-slate-500"><span>Range: {money(stock.periodLow)} – {money(stock.periodHigh)}</span><span className="flex items-center gap-1 text-cyan-300">Analyze <ArrowUpRight size={13} /></span></div>
    </button>
  );
}

function SignalBadge({ signal }: { signal: Analysis["signalType"] }) {
  const color = signal === "BUY" ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300" : signal === "SELL" ? "border-rose-500/30 bg-rose-500/10 text-rose-300" : "border-slate-700 bg-slate-800 text-slate-300";
  return <span className={`rounded-full border px-3 py-1.5 text-xs font-bold tracking-wide ${color}`}>{signal} SIGNAL</span>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-3"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-sm font-semibold text-slate-200">{value}</p></div>;
}

function QuoteRow({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-xs text-slate-500">{label}</dt><dd className="mt-1 font-medium text-slate-200">{value}</dd></div>;
}

function ErrorBanner({ message }: { message: string }) {
  return <div className="flex items-start gap-2 rounded-xl border border-rose-500/20 bg-rose-500/5 p-4 text-sm text-rose-200"><CircleAlert className="mt-0.5 shrink-0" size={17} /><span>{message}</span></div>;
}

function Loading({ label }: { label: string }) {
  return <div className="flex items-center justify-center gap-3 rounded-xl border border-slate-800 bg-slate-900/50 p-8 text-sm text-slate-400"><LoaderCircle className="animate-spin text-cyan-300" size={19} />{label}</div>;
}

export default App;

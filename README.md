# Solana Stock Analyzer

The React/Vite dashboard talks to a local FastAPI backend. Market data, the
Python decision algorithm, Gemini summaries, and Jupiter order preparation are
handled by Python; API keys and the Solana key stay in the local `.env` file.

## Requirements

- Python 3.11 or newer
- Node.js 20.19+ or 22.12+
- A `.env` file in the project root containing the credentials you want to use:

```dotenv
GEMINI_API_KEY=
SOLANA_PRIVATE_KEY=
# Optional: improves Jupiter API rate limits
JUPITER_API_KEY=
# Optional; defaults to gemini-2.5-flash
GEMINI_MODEL=gemini-2.5-flash
```

Never commit or share `.env`.

## First-time setup (Windows PowerShell)

From the project directory:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install
```

## Run

Double-click `run_dashboard.bat`, or start the backend and frontend in separate
PowerShell terminals:

```powershell
.\.venv\Scripts\python.exe -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

```powershell
npm run dev
```
OR run command:
```
.\run_dashboard.bat
```

Open the Vite URL printed in the terminal (normally
`http://127.0.0.1:5173`). The Python API is available at
`http://127.0.0.1:8000`; its interactive API docs are at
`http://127.0.0.1:8000/docs`.

The dashboard only submits a Jupiter trade after a fresh server-side BUY
decision, eligibility confirmation, and an exact typed trade confirmation.
Tokenized stock swaps are not brokerage orders and may not deliver exactly one
token/share.

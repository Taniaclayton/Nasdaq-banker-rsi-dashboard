# Banker RSI Dashboard

A local dashboard that scans NASDAQ EOD data for **Banker RSI buy signals** based on the LOKEN BULLISH MCDX v2.2 indicator.

Three signal tiers are detected:
- 🟢 **5-day setup** — `banker_rsi` was zero for 5 consecutive days, then crossed above 0 today
- 🔵 **3-day setup** — `banker_rsi` was zero for 3 consecutive days, then crossed above 0 today
- 🟡 **Immediate** — `banker_rsi` is above 0 today (any crossing)

---

## Project Structure

```
banker-rsi-dashboard/
├── backend/
│   ├── api.py                  # Flask REST API
│   ├── loader.py               # EOD file → MySQL importer
│   ├── watcher.py              # Auto-runs loader when new files appear
│   ├── requirements.txt        # API dependencies
│   ├── loader_requirements.txt # Loader + watcher dependencies
│   └── .env.example            # Copy to .env and fill in credentials
├── frontend/
│   ├── src/
│   │   ├── App.jsx             # Main dashboard component
│   │   └── main.jsx            # React entry point
│   ├── index.html
│   ├── package.json
│   ├── vite.config.js
│   └── .env.example            # Copy to .env and set VITE_API_URL
├── data/                       # Drop your NASDAQ_*.txt files here
└── .gitignore
```

---

## Prerequisites

- Python 3.10+
- Node.js 18+
- MySQL 8.x running locally (or remotely)

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/banker-rsi-dashboard.git
cd banker-rsi-dashboard
```

### 2. Configure environment variables

**Backend:**
```bash
cp backend/.env.example backend/.env
```
Edit `backend/.env`:
```
DB_HOST=localhost
DB_PORT=3306
DB_NAME=nasdaq_eod
DB_USER=root
DB_PASSWORD=your_mysql_password
EOD_FOLDER=/path/to/your/data/folder   # or leave blank to use data/ in this repo
```

**Frontend:**
```bash
cp frontend/.env.example frontend/.env
```
Edit `frontend/.env` (defaults to localhost:5000, only change if needed):
```
VITE_API_URL=http://localhost:5000/api
```

### 3. Install backend dependencies

```bash
cd backend
pip install -r requirements.txt          # for the API
pip install -r loader_requirements.txt   # for the loader + watcher
```

### 4. Load EOD data into MySQL

Place your `NASDAQ_YYYYMMDD.txt` files in the `data/` folder (or wherever `EOD_FOLDER` points), then run:

```bash
python backend/loader.py
```

This creates the `nasdaq_eod` database and table automatically on first run.

### 5. Install frontend dependencies

```bash
cd frontend
npm install
```

---

## Running

Open **three terminals**:

**Terminal 1 — Flask API:**
```bash
cd backend
python api.py
# Runs on http://localhost:5000
```

**Terminal 2 — React frontend:**
```bash
cd frontend
npm run dev
# Opens http://localhost:3000
```

**Terminal 3 — Auto-loader (optional):**
```bash
cd backend
python watcher.py
# Watches for new NASDAQ_*.txt files and auto-imports them
```

---

## EOD File Format

The loader expects comma-separated files named `NASDAQ_YYYYMMDD.txt` with this header:

```
<ticker>,<date>,<open>,<high>,<low>,<close>,<volume>
```

Dates in the file should be in `YYYYMMDD` format.

---

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /api/health` | DB connectivity check |
| `GET /api/dates` | List of available trade dates |
| `GET /api/signal-day?date=YYYY-MM-DD` | Signals for a specific date |
| `GET /api/signals?days=N` | Signals for the last N trading days |

---

## Banker RSI Parameters

| Parameter | Value |
|---|---|
| RSI period | 50 |
| RSI base | 50 |
| Sensitivity | 1.5 |
| Bull threshold | 8.5 |

Formula: `banker_rsi = clamp(1.5 * (RSI(50) - 50), 0, 20)`

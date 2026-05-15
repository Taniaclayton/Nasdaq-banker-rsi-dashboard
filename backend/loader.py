"""
NASDAQ EOD → MySQL Loader with Banker Signal
=============================================
Reads all NASDAQ_YYYYMMDD.txt files from a folder,
computes the MCDX Banker RSI signal (from LOKEN BULLISH MCDX v2.2),
and inserts everything into a MySQL database.

Setup:
    1. Copy .env.example to .env and fill in your DB credentials.
    2. Set EOD_FOLDER in .env (or edit the fallback below).
    3. pip install -r loader_requirements.txt
    4. python loader.py

Columns stored per row:
  delta, u (gain), d (loss), avg_gain, avg_loss, rs, rsi,
  raw_banker (before clamping), banker_rsi (clamped 0-20),
  banker_ma, banker_signal, banker_bull
"""

import os
import glob
import numpy as np
import pandas as pd
import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# CONFIGURATION — set these in your .env file
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":     os.getenv("DB_HOST", "localhost"),
    "port":     int(os.getenv("DB_PORT", 3306)),
    "database": os.getenv("DB_NAME", "nasdaq_eod"),
    "user":     os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "use_pure": True,
}

# Folder containing NASDAQ_*.txt files.
# Set EOD_FOLDER in your .env, or change the fallback path below.
EOD_FOLDER = os.getenv("EOD_FOLDER", os.path.join(os.path.dirname(__file__), "..", "data"))

# ─────────────────────────────────────────────
# MCDX BANKER SIGNAL PARAMETERS (match Pine)
# ─────────────────────────────────────────────
RSI_BASE_BANKER    = 50
RSI_PERIOD_BANKER  = 50
SENSITIVITY_BANKER = 1.5


# ─────────────────────────────────────────────
# RSI — returns all intermediate columns
# ─────────────────────────────────────────────
def compute_rsi_full(series: pd.Series, period: int) -> pd.DataFrame:
    if len(series) <= period:
        nan = pd.Series(np.nan, index=series.index)
        return pd.DataFrame({
            "delta": series.diff(), "u": nan, "d": nan,
            "avg_gain": nan, "avg_loss": nan, "rs": nan, "rsi": nan,
        })

    delta = series.diff()
    u = delta.clip(lower=0)
    d = (-delta).clip(lower=0)

    avg_gain = np.full(len(series), np.nan)
    avg_loss = np.full(len(series), np.nan)

    # First value: simple average over first `period` bars (Pine's seed)
    avg_gain[period] = u.iloc[1:period + 1].mean()
    avg_loss[period] = d.iloc[1:period + 1].mean()

    # Subsequent values: Wilder smoothing
    for i in range(period + 1, len(series)):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + u.iloc[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + d.iloc[i]) / period

    avg_gain = pd.Series(avg_gain, index=series.index)
    avg_loss = pd.Series(avg_loss, index=series.index)

    rs  = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))

    return pd.DataFrame({
        "delta":    delta,
        "u":        u,
        "d":        d,
        "avg_gain": avg_gain,
        "avg_loss": avg_loss,
        "rs":       rs,
        "rsi":      rsi,
    })


# ─────────────────────────────────────────────
# BANKER RSI
# ─────────────────────────────────────────────
def compute_banker_columns(close: pd.Series) -> pd.DataFrame:
    """
    Returns DataFrame with delta, u, d, avg_gain, avg_loss, rs, rsi,
    raw_banker (before clamping), banker_rsi (clamped 0–20).
    """
    rsi_df = compute_rsi_full(close, RSI_PERIOD_BANKER)
    raw = SENSITIVITY_BANKER * (rsi_df["rsi"] - RSI_BASE_BANKER)
    rsi_df["raw_banker"] = raw
    rsi_df["banker_rsi"] = raw.clip(lower=0, upper=20)
    return rsi_df


# ─────────────────────────────────────────────
# EMA / RMA / SMA helpers
# ─────────────────────────────────────────────
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, min_periods=period, adjust=False).mean()


def rma(series: pd.Series, period: int) -> pd.Series:
    """Pine rma = Wilder MA = EWM with alpha 1/period."""
    return series.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


# ─────────────────────────────────────────────
# BANKER MA / SIGNAL
# ─────────────────────────────────────────────
def compute_banker_ma_signal(rsi_banker: pd.Series):
    bankma2    = sma(rsi_banker, 2)
    bankma7    = ema(rsi_banker, 7)
    bankma31   = ema(rsi_banker, 31)
    bankma     = sma((bankma2 * 70 + bankma7 * 20 + bankma31 * 10) / 100, 1)
    banksignal = rma(bankma, 4)
    return bankma, banksignal


# ─────────────────────────────────────────────
# PARSE ONE EOD FILE
# ─────────────────────────────────────────────
def parse_eod_file(filepath: str) -> pd.DataFrame:
    df = pd.read_csv(
        filepath,
        names=["ticker", "date", "open", "high", "low", "close", "volume"],
        header=0,
        dtype={"ticker": str, "date": str},
    )
    df.columns = df.columns.str.strip().str.replace("<", "", regex=False).str.replace(">", "", regex=False)
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    df[["open", "high", "low", "close"]] = df[["open", "high", "low", "close"]].astype(float)
    df["volume"] = df["volume"].astype(np.int64)
    return df


# ─────────────────────────────────────────────
# LOAD ALL FILES → combined DataFrame
# ─────────────────────────────────────────────
def load_all_files(folder: str) -> pd.DataFrame:
    pattern = os.path.join(folder, "NASDAQ_*.txt")
    files   = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No NASDAQ_*.txt files found in: {folder}")
    print(f"Found {len(files)} EOD file(s).")
    frames   = [parse_eod_file(f) for f in files]
    combined = pd.concat(frames, ignore_index=True)
    combined.sort_values(["ticker", "date"], inplace=True)
    combined.reset_index(drop=True, inplace=True)
    print(f"Total rows: {len(combined):,}  |  Unique tickers: {combined['ticker'].nunique():,}")
    return combined


# ─────────────────────────────────────────────
# COMPUTE SIGNALS PER TICKER
# ─────────────────────────────────────────────
def add_banker_signals(df: pd.DataFrame) -> pd.DataFrame:
    results = []
    for ticker, grp in df.groupby("ticker", sort=False):
        grp = grp.copy()
        cols = compute_banker_columns(grp["close"])
        for col in ["delta", "u", "d", "avg_gain", "avg_loss", "rs", "rsi", "raw_banker", "banker_rsi"]:
            grp[col] = cols[col]
        grp["banker_ma"], grp["banker_signal"] = compute_banker_ma_signal(grp["banker_rsi"])
        grp["banker_bull"] = (grp["banker_rsi"] > 8.5).astype(int)
        results.append(grp)

    enriched = pd.concat(results, ignore_index=True)
    enriched.sort_values(["ticker", "date"], inplace=True)

    float_cols = ["delta", "u", "d", "avg_gain", "avg_loss", "rs", "rsi",
                  "raw_banker", "banker_rsi", "banker_ma", "banker_signal"]
    for col in float_cols:
        enriched[col] = enriched[col].round(4)

    return enriched


# ─────────────────────────────────────────────
# DATABASE SETUP
# ─────────────────────────────────────────────
CREATE_DB_SQL = (
    "CREATE DATABASE IF NOT EXISTS {db} "
    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS nasdaq_eod (
    id             BIGINT AUTO_INCREMENT PRIMARY KEY,
    ticker         VARCHAR(20)    NOT NULL,
    trade_date     DATE           NOT NULL,
    open           DECIMAL(18,4)  NOT NULL,
    high           DECIMAL(18,4)  NOT NULL,
    low            DECIMAL(18,4)  NOT NULL,
    close          DECIMAL(18,4)  NOT NULL,
    volume         BIGINT         NOT NULL,

    delta          DECIMAL(18,4),
    u              DECIMAL(18,4),
    d              DECIMAL(18,4),
    avg_gain       DECIMAL(10,4),
    avg_loss       DECIMAL(10,4),
    rs             DECIMAL(10,4),
    rsi            DECIMAL(10,4),

    raw_banker     DECIMAL(10,4),
    banker_rsi     DECIMAL(10,4),
    banker_ma      DECIMAL(10,4),
    banker_signal  DECIMAL(10,4),
    banker_bull    TINYINT(1),

    UNIQUE KEY uq_ticker_date (ticker, trade_date),
    INDEX idx_ticker      (ticker),
    INDEX idx_date        (trade_date),
    INDEX idx_banker_rsi  (banker_rsi)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

INSERT_SQL = """
INSERT INTO nasdaq_eod
    (ticker, trade_date, open, high, low, close, volume,
     delta, u, d, avg_gain, avg_loss, rs, rsi,
     raw_banker, banker_rsi, banker_ma, banker_signal, banker_bull)
VALUES (%s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    open=VALUES(open), high=VALUES(high), low=VALUES(low),
    close=VALUES(close), volume=VALUES(volume),
    delta=VALUES(delta), u=VALUES(u), d=VALUES(d),
    avg_gain=VALUES(avg_gain), avg_loss=VALUES(avg_loss),
    rs=VALUES(rs), rsi=VALUES(rsi),
    raw_banker=VALUES(raw_banker), banker_rsi=VALUES(banker_rsi),
    banker_ma=VALUES(banker_ma), banker_signal=VALUES(banker_signal),
    banker_bull=VALUES(banker_bull);
"""

BATCH_SIZE = 5_000


def _nan_or(val):
    if val is None:
        return None
    try:
        if pd.isna(val) or not np.isfinite(val):
            return None
    except (TypeError, ValueError):
        pass
    return float(val)


def get_connection(with_db=True):
    cfg = {k: v for k, v in DB_CONFIG.items() if k != "database"}
    cfg["auth_plugin"] = "mysql_native_password"
    if with_db:
        cfg["database"] = DB_CONFIG["database"]
    return mysql.connector.connect(**cfg)


def setup_database():
    conn = get_connection(with_db=False)
    cur  = conn.cursor()
    cur.execute(CREATE_DB_SQL.format(db=DB_CONFIG["database"]))
    conn.commit()
    cur.close()
    conn.close()

    conn = get_connection()
    cur  = conn.cursor()
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()
    cur.close()
    conn.close()
    print(f"Database `{DB_CONFIG['database']}` and table `nasdaq_eod` ready.")


def insert_data(df: pd.DataFrame):
    rows = [
        (
            row.ticker, row.date.date(),
            float(row.open), float(row.high), float(row.low), float(row.close), int(row.volume),
            _nan_or(row.delta), _nan_or(row.u), _nan_or(row.d),
            _nan_or(row.avg_gain), _nan_or(row.avg_loss), _nan_or(row.rs), _nan_or(row.rsi),
            _nan_or(row.raw_banker), _nan_or(row.banker_rsi),
            _nan_or(row.banker_ma), _nan_or(row.banker_signal),
            None if pd.isna(row.banker_bull) else int(row.banker_bull),
        )
        for row in df.itertuples(index=False)
    ]

    conn     = get_connection()
    cur      = conn.cursor()
    total    = len(rows)
    inserted = 0

    try:
        for i in range(0, total, BATCH_SIZE):
            batch = rows[i:i + BATCH_SIZE]
            cur.executemany(INSERT_SQL, batch)
            conn.commit()
            inserted += len(batch)
            print(f"  Inserted {inserted:,} / {total:,} rows...", end="\r")
        print(f"\nDone — {inserted:,} rows upserted into `nasdaq_eod`.")
    except Error as e:
        conn.rollback()
        print(f"\nDB error: {e}")
        raise
    finally:
        cur.close()
        conn.close()


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────
if __name__ == "__main__":
    print("=== NASDAQ EOD → MySQL Loader ===\n")
    df = load_all_files(EOD_FOLDER)
    print("Computing RSI intermediate + Banker signals...")
    df = add_banker_signals(df)
    print("Signals computed.\n")
    setup_database()
    print("Inserting into MySQL...")
    insert_data(df)
    print("\n✓ All done!")

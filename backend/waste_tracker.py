"""
Waste Intelligence — backend data-access layer.

All DB access for the dashboard goes through this module. The frontend never
writes raw SQL. This keeps the SQL in one place and makes swapping SQLite for
PostgreSQL a single connection-string change.

Layout mirrors db/schema.sql. Uses SQLite for the prototype; the same queries
run on Postgres with negligible changes.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

DB_PATH = Path(__file__).resolve().parent.parent / \
    "db" / "waste_intelligence.db"

# EPA WARM-derived: ~1.0 kg CO2e per kg of landfilled food waste (conservative
# midpoint; landfill gas recovery lowers it, no recovery raises it). Diverted
# waste is treated as ~0.05 kg CO2e/kg (transport + processing).
CO2E_KG_PER_KG_LANDFILL = 1.0
CO2E_KG_PER_KG_DIVERTED = 0.05

# Industry benchmarks (for the dashboard's "vs benchmark" lines)
BENCH_WASTE_PCT_OF_SPEND = (8.0, 18.0)      # average hotel range
BENCH_GRAMS_PER_COVER = (150.0, 350.0)      # average hotel range


# ---------------------------------------------------------------------------
# Connection + schema bootstrap
# ---------------------------------------------------------------------------
SCHEMA_FILE = Path(__file__).resolve().parent.parent / \
    "db" / "schema_sqlite.sql"


@contextmanager
def get_conn():
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Create tables if not present. Called on app startup."""
    schema = SCHEMA_FILE.read_text()
    with get_conn() as conn:
        conn.executescript(schema)


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def log_waste(log_date: str, outlet_id: str, shift_id: str, category_code: str,
              reason_code: str | None, weight_kg: float, estimated_cost: float,
              logged_by: str, notes: str | None = None) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO waste_entry
               (log_date, outlet_id, shift_id, category_code, reason_code,
                weight_kg, estimated_cost, logged_by, notes)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (log_date, outlet_id, shift_id, category_code, reason_code,
             weight_kg, estimated_cost, logged_by, notes))
        conn.commit()
        return cur.lastrowid


def log_diversion(log_date: str, outlet_id: str, channel_code: str,
                  weight_kg: float, logged_by: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO diversion_entry
               (log_date, outlet_id, channel_code, weight_kg, logged_by)
               VALUES (?,?,?,?,?)""",
            (log_date, outlet_id, channel_code, weight_kg, logged_by))
        conn.commit()
        return cur.lastrowid


def set_daily_kpi(log_date: str, outlet_id: str, covers: int, food_spend: float) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO daily_kpi (log_date, outlet_id, covers, food_spend)
               VALUES (?,?,?,?)
               ON CONFLICT(log_date, outlet_id)
               DO UPDATE SET covers=excluded.covers, food_spend=excluded.food_spend""",
            (log_date, outlet_id, covers, food_spend))
        conn.commit()


# ---------------------------------------------------------------------------
# Reference data (for form dropdowns)
# ---------------------------------------------------------------------------
def list_outlets() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM outlet ORDER BY outlet_id", conn)


def list_categories() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM waste_category ORDER BY code", conn)


def list_reasons() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM reason_code ORDER BY code", conn)


def list_shifts() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM shift_def ORDER BY shift_id", conn)


def list_diversion_channels() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM diversion_channel ORDER BY code", conn)


def list_users() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM app_user ORDER BY user_id", conn)


# ---------------------------------------------------------------------------
# Reads / analytics
# ---------------------------------------------------------------------------
def fetch_waste_entries(start_date: str, end_date: str,
                        outlets: list[str] | None = None) -> pd.DataFrame:
    sql = """
        SELECT w.entry_id, w.log_date, w.outlet_id, o.name AS outlet,
               w.shift_id, w.category_code, wc.label AS category,
               w.reason_code, w.weight_kg, w.estimated_cost, w.logged_by, w.notes
        FROM waste_entry w
        JOIN outlet o ON o.outlet_id = w.outlet_id
        JOIN waste_category wc ON wc.code = w.category_code
        WHERE w.log_date BETWEEN ? AND ?
    """
    params: list = [start_date, end_date]
    if outlets:
        placeholders = ",".join("?" * len(outlets))
        sql += f" AND w.outlet_id IN ({placeholders})"
        params += outlets
    sql += " ORDER BY w.log_date DESC, w.entry_id DESC"
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def fetch_summary(start_date: str, end_date: str,
                  outlets: list[str] | None = None) -> dict:
    """Headline KPIs for the period."""
    sql = """
        SELECT
            COALESCE(SUM(w.weight_kg),0)        AS total_kg,
            COALESCE(SUM(w.estimated_cost),0)   AS total_cost,
            COUNT(*)                            AS entries,
            COALESCE(SUM(d.weight_kg),0)        AS diverted_kg
        FROM waste_entry w
        LEFT JOIN diversion_entry d
          ON d.log_date = w.log_date AND d.outlet_id = w.outlet_id
        WHERE w.log_date BETWEEN ? AND ?
    """
    params = [start_date, end_date]
    if outlets:
        placeholders = ",".join("?" * len(outlets))
        sql = sql.replace("WHERE w.log_date BETWEEN ? AND ?",
                          f"WHERE w.log_date BETWEEN ? AND ? AND w.outlet_id IN ({placeholders})")
        params += outlets
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    total_kg = row["total_kg"] or 0
    diverted_kg = row["diverted_kg"] or 0
    landfill_kg = max(total_kg - diverted_kg, 0)
    co2e = (landfill_kg * CO2E_KG_PER_KG_LANDFILL
            + diverted_kg * CO2E_KG_PER_KG_DIVERTED)
    diversion_pct = (100.0 * diverted_kg / total_kg) if total_kg > 0 else 0
    return {
        "total_kg": round(total_kg, 2),
        "total_cost": round(row["total_cost"] or 0, 2),
        "entries": row["entries"] or 0,
        "diverted_kg": round(diverted_kg, 2),
        "diversion_pct": round(diversion_pct, 1),
        "co2e_kg": round(co2e, 2),
    }


def fetch_by_category(start_date: str, end_date: str,
                      outlets: list[str] | None = None) -> pd.DataFrame:
    sql = """
        SELECT wc.label AS category, SUM(w.weight_kg) AS kg,
               SUM(w.estimated_cost) AS cost, COUNT(*) AS entries
        FROM waste_entry w
        JOIN waste_category wc ON wc.code = w.category_code
        WHERE w.log_date BETWEEN ? AND ?
    """
    params = [start_date, end_date]
    if outlets:
        placeholders = ",".join("?" * len(outlets))
        sql += f" AND w.outlet_id IN ({placeholders})"
        params += outlets
    sql += " GROUP BY wc.label ORDER BY kg DESC"
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=params)


def fetch_by_outlet(start_date: str, end_date: str) -> pd.DataFrame:
    sql = """
        SELECT o.name AS outlet, SUM(w.weight_kg) AS kg,
               SUM(w.estimated_cost) AS cost
        FROM waste_entry w
        JOIN outlet o ON o.outlet_id = w.outlet_id
        WHERE w.log_date BETWEEN ? AND ?
        GROUP BY o.name ORDER BY kg DESC
    """
    with get_conn() as conn:
        return pd.read_sql_query(sql, conn, params=[start_date, end_date])


def fetch_trend(start_date: str, end_date: str,
                outlets: list[str] | None = None) -> pd.DataFrame:
    sql = """
        SELECT w.log_date, o.name AS outlet,
               SUM(w.weight_kg) AS kg, SUM(w.estimated_cost) AS cost
        FROM waste_entry w
        JOIN outlet o ON o.outlet_id = w.outlet_id
        WHERE w.log_date BETWEEN ? AND ?
    """
    params = [start_date, end_date]
    if outlets:
        placeholders = ",".join("?" * len(outlets))
        sql += f" AND w.outlet_id IN ({placeholders})"
        params += outlets
    sql += " GROUP BY w.log_date, o.name ORDER BY w.log_date"
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    if not df.empty:
        df["log_date"] = pd.to_datetime(df["log_date"])
    return df


def fetch_per_cover(start_date: str, end_date: str) -> pd.DataFrame:
    sql = """
        SELECT w.log_date, o.name AS outlet,
               SUM(w.weight_kg) AS waste_kg,
               SUM(w.estimated_cost) AS waste_cost,
               k.covers, k.food_spend
        FROM waste_entry w
        JOIN outlet o ON o.outlet_id = w.outlet_id
        JOIN daily_kpi k ON k.log_date = w.log_date AND k.outlet_id = w.outlet_id
        WHERE w.log_date BETWEEN ? AND ?
        GROUP BY w.log_date, o.name, k.covers, k.food_spend
        ORDER BY w.log_date
    """
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=[start_date, end_date])
    if df.empty:
        return df
    df["grams_per_cover"] = df.apply(
        lambda r: (r["waste_kg"] * 1000.0 / r["covers"]) if r["covers"] > 0 else None, axis=1)
    df["pct_of_spend"] = df.apply(
        lambda r: (100.0 * r["waste_cost"] / r["food_spend"]) if r["food_spend"] > 0 else None, axis=1)
    df["log_date"] = pd.to_datetime(df["log_date"])
    return df


# ---------------------------------------------------------------------------
# Before/after pilot comparison
# ---------------------------------------------------------------------------
DEFAULT_PILOT_TARGET = 0.15   # 15% reduction (matches proposal pilot target)


def fetch_target(outlets: list[str] | None = None,
                 period: str = "PILOT_90D") -> float:
    """Return the reduction target as a fraction (0.15 = 15%).
    Averages the per-outlet targets from the target table; falls back to the
    default 15% pilot target if none are set."""
    sql = "SELECT AVG(target_pct) AS t FROM target WHERE period = ?"
    params: list = [period]
    if outlets:
        placeholders = ",".join("?" * len(outlets))
        sql += f" AND outlet_id IN ({placeholders})"
        params += outlets
    with get_conn() as conn:
        row = conn.execute(sql, params).fetchone()
    val = row["t"] if row and row["t"] is not None else None
    return float(val) if val is not None else DEFAULT_PILOT_TARGET


def _daily_totals(start_date: str, end_date: str,
                  outlets: list[str] | None = None) -> pd.DataFrame:
    """One row per day: total waste kg, cost, covers, food spend (aggregated
    across selected outlets). The comparison is built on top of this."""
    w_where = "w.log_date BETWEEN ? AND ?"
    k_where = "k.log_date BETWEEN ? AND ?"
    if outlets:
        placeholders = ",".join("?" * len(outlets))
        w_where += f" AND w.outlet_id IN ({placeholders})"
        k_where += f" AND k.outlet_id IN ({placeholders})"
        params: list = [start_date, end_date] + \
            outlets + [start_date, end_date] + outlets
    else:
        params = [start_date, end_date, start_date, end_date]
    sql = f"""
        SELECT d.log_date, d.kg, d.cost, k.covers, k.food_spend
        FROM (
          SELECT log_date, SUM(weight_kg) AS kg, SUM(estimated_cost) AS cost
          FROM waste_entry w
          WHERE {w_where}
          GROUP BY log_date
        ) d
        LEFT JOIN (
          SELECT log_date, SUM(covers) AS covers, SUM(food_spend) AS food_spend
          FROM daily_kpi k
          WHERE {k_where}
          GROUP BY log_date
        ) k ON k.log_date = d.log_date
        ORDER BY d.log_date
    """
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    if not df.empty:
        df["log_date"] = pd.to_datetime(df["log_date"])
    return df


def _period_stats(df: pd.DataFrame) -> dict:
    """Aggregate a daily-totals slice into comparison metrics."""
    if df.empty:
        return dict(kg=0, cost=0, days=0, kg_per_day=0, cost_per_day=0,
                    grams_per_cover=0, co2e=0)
    kg = float(df["kg"].sum())
    cost = float(df["cost"].sum())
    days = max(df["log_date"].dt.normalize().nunique(), 1)
    covers = float(df["covers"].fillna(0).sum())
    landfill = kg  # no diversion detail per day; use total (conservative)
    co2e = landfill * CO2E_KG_PER_KG_LANDFILL
    gpc = (kg * 1000.0 / covers) if covers > 0 else 0
    return dict(
        kg=round(kg, 2),
        cost=round(cost, 2),
        days=days,
        kg_per_day=round(kg / days, 2),
        cost_per_day=round(cost / days, 2),
        grams_per_cover=round(gpc, 1),
        co2e=round(co2e, 2),
    )


def fetch_pilot_comparison(start_date: str, end_date: str,
                           outlets: list[str] | None = None,
                           baseline_days: int = 14,
                           current_days: int = 14) -> dict:
    """Compare a baseline window (first N days) to a current window (last N days).

    Returns per-period stats plus the % reduction in kg/day vs. the pilot target.
    Status: ON_TRACK (>= target), AT_RISK (>= 50% of target), BEHIND (< 50%).
    """
    daily = _daily_totals(start_date, end_date, outlets)
    target = fetch_target(outlets)
    if daily.empty:
        return dict(baseline={}, current={}, reduction_pct=0, target_pct=round(target * 100, 1),
                    status="NO_DATA", by_outlet=pd.DataFrame())
    daily = daily.sort_values("log_date").reset_index(drop=True)
    baseline = daily.head(baseline_days)
    current = daily.tail(current_days)
    b = _period_stats(baseline)
    c = _period_stats(current)
    if b["kg_per_day"] > 0:
        reduction_pct = 100.0 * \
            (b["kg_per_day"] - c["kg_per_day"]) / b["kg_per_day"]
    else:
        reduction_pct = 0.0
    reduction_pct = round(reduction_pct, 1)
    target_pct = round(target * 100, 1)
    if reduction_pct >= target_pct:
        status = "ON_TRACK"
    elif reduction_pct >= target_pct * 0.5:
        status = "AT_RISK"
    else:
        status = "BEHIND"
    by_outlet = _comparison_by_outlet(start_date, end_date, outlets,
                                      baseline, current)
    return dict(
        baseline=b, current=c, reduction_pct=reduction_pct,
        target_pct=target_pct, status=status, by_outlet=by_outlet,
        baseline_window=(str(baseline["log_date"].min().date()),
                         str(baseline["log_date"].max().date())) if not baseline.empty else (None, None),
        current_window=(str(current["log_date"].min().date()),
                        str(current["log_date"].max().date())) if not current.empty else (None, None),
    )


def _comparison_by_outlet(start_date: str, end_date: str,
                          outlets: list[str] | None,
                          baseline_dates: pd.DataFrame,
                          current_dates: pd.DataFrame) -> pd.DataFrame:
    """Baseline vs current avg kg/day per outlet, for the breakdown chart."""
    b_dates = [d.strftime("%Y-%m-%d")
               for d in baseline_dates["log_date"].dt.normalize().unique()]
    c_dates = [d.strftime("%Y-%m-%d")
               for d in current_dates["log_date"].dt.normalize().unique()]
    if not b_dates or not c_dates:
        return pd.DataFrame()
    b_ph = ",".join("?" * len(b_dates))
    c_ph = ",".join("?" * len(c_dates))
    out_filter = ""
    params: list = b_dates + c_dates + [start_date, end_date]
    if outlets:
        out_ph = ",".join("?" * len(outlets))
        out_filter = f" AND o.outlet_id IN ({out_ph})"
        params = b_dates + c_dates + [start_date, end_date] + outlets
    # correlated subqueries: each outlet's own baseline/current daily averages
    sql = f"""
        SELECT o.name AS outlet,
          (SELECT AVG(t.kg) FROM (
             SELECT log_date, SUM(weight_kg) AS kg FROM waste_entry
             WHERE log_date IN ({b_ph}) AND outlet_id = o.outlet_id
             GROUP BY log_date) t) AS baseline_kg_day,
          (SELECT AVG(t.kg) FROM (
             SELECT log_date, SUM(weight_kg) AS kg FROM waste_entry
             WHERE log_date IN ({c_ph}) AND outlet_id = o.outlet_id
             GROUP BY log_date) t) AS current_kg_day
        FROM outlet o
        WHERE EXISTS (SELECT 1 FROM waste_entry w WHERE w.outlet_id = o.outlet_id
                      AND w.log_date BETWEEN ? AND ?){out_filter}
        ORDER BY o.name
    """
    with get_conn() as conn:
        df = pd.read_sql_query(sql, conn, params=params)
    if df.empty:
        return df
    df["baseline_kg_day"] = df["baseline_kg_day"].fillna(0).round(2)
    df["current_kg_day"] = df["current_kg_day"].fillna(0).round(2)
    df["reduction_pct"] = df.apply(
        lambda r: round(100.0 * (r["baseline_kg_day"] - r["current_kg_day"])
                        / r["baseline_kg_day"], 1)
        if r["baseline_kg_day"] > 0 else 0, axis=1)
    return df

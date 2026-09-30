-- ============================================================================
-- Waste Intelligence — Food Waste Reduction Tracker
-- SQLite DDL for the prototype. The production PostgreSQL schema is separate.
--
-- Layers:  reference data  ->  transactional log  ->  analytics views
-- The dashboard reads from the views, never directly from raw tables.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Reference: outlets (F&B locations)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS outlet (
    outlet_id   TEXT PRIMARY KEY,            -- 'BUFFET','STEAK','MAIN_KIT'
    name        TEXT NOT NULL,
    outlet_type TEXT NOT NULL                 -- 'BUFFET' | 'RESTAURANT' | 'KITCHEN'
);

-- ---------------------------------------------------------------------------
-- Reference: service shifts
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shift_def (
    shift_id TEXT PRIMARY KEY,               -- 'BREAK','LUNCH','DINNER','OVERNIGHT'
    label    TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Reference: waste categories (what was wasted)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS waste_category (
    code  TEXT PRIMARY KEY,                  -- 'OVERPROD','SPOILAGE','PLATE','TRIM','EXPIRED','OTHER'
    label TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Reference: reason codes (why it was wasted)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reason_code (
    code  TEXT PRIMARY KEY,                  -- 'OVERPREP','DEMAND_MISS','SHELF_LIFE','MISPREP','DAMAGE','OTHER'
    label TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Reference: diversion channels (where unavoidable waste goes instead of landfill)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS diversion_channel (
    code  TEXT PRIMARY KEY,                  -- 'DONATION','COMPOST','ANAEROBIC','ANIMAL_FEED'
    label TEXT NOT NULL
);

-- ---------------------------------------------------------------------------
-- Users (dashboard operators / loggers). NOT an auth system for approvals.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_user (
    user_id      TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL,               -- 'STEWARD','CHEF','ANALYST','MGT'
    home_outlet  TEXT REFERENCES outlet(outlet_id)
);

-- ---------------------------------------------------------------------------
-- Targets: reduction target % per outlet per period.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS target (
    outlet_id      TEXT NOT NULL REFERENCES outlet(outlet_id),
    period         TEXT NOT NULL,             -- 'PILOT_90D' | 'YEAR1'
    target_pct     NUMERIC NOT NULL,          -- e.g. 0.15 for 15%
    PRIMARY KEY (outlet_id, period)
);

-- ---------------------------------------------------------------------------
-- Core transactional table: one row per waste-logging event.
-- weight_kg and estimated_cost are the two headline numbers.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS waste_entry (
    entry_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date       DATE NOT NULL,
    outlet_id      TEXT NOT NULL REFERENCES outlet(outlet_id),
    shift_id       TEXT NOT NULL REFERENCES shift_def(shift_id),
    category_code  TEXT NOT NULL REFERENCES waste_category(code),
    reason_code    TEXT REFERENCES reason_code(code),
    weight_kg      NUMERIC NOT NULL CHECK (weight_kg >= 0),
    estimated_cost NUMERIC NOT NULL CHECK (estimated_cost >= 0),
    logged_by      TEXT NOT NULL REFERENCES app_user(user_id),
    notes          TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- ---------------------------------------------------------------------------
-- Daily operational context per outlet: covers served + food spend.
-- Lets us compute waste-per-cover and waste-as-%-of-spend benchmarks.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_kpi (
    log_date    DATE NOT NULL,
    outlet_id   TEXT NOT NULL REFERENCES outlet(outlet_id),
    covers      INTEGER NOT NULL,
    food_spend  NUMERIC NOT NULL,            -- $ food purchased that day
    PRIMARY KEY (log_date, outlet_id)
);

-- ---------------------------------------------------------------------------
-- Diversion log: unavoidable waste sent to donation/compost instead of landfill.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS diversion_entry (
    entry_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    log_date    DATE NOT NULL,
    outlet_id   TEXT NOT NULL REFERENCES outlet(outlet_id),
    channel_code TEXT NOT NULL REFERENCES diversion_channel(code),
    weight_kg   NUMERIC NOT NULL CHECK (weight_kg >= 0),
    logged_by   TEXT NOT NULL REFERENCES app_user(user_id),
    created_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_waste_date   ON waste_entry (log_date, outlet_id);
CREATE INDEX IF NOT EXISTS idx_waste_outlet ON waste_entry (outlet_id, log_date);

-- ============================================================================
-- Analytics views (read by the dashboard)
-- ============================================================================

-- Waste by category over a period
CREATE VIEW IF NOT EXISTS v_waste_by_category AS
SELECT wc.code, wc.label,
       SUM(w.weight_kg)     AS total_kg,
       SUM(w.estimated_cost) AS total_cost,
       COUNT(*)              AS entries
FROM waste_entry w
JOIN waste_category wc ON wc.code = w.category_code
GROUP BY wc.code, wc.label;

-- Waste by outlet over a period
CREATE VIEW IF NOT EXISTS v_waste_by_outlet AS
SELECT o.outlet_id, o.name, o.outlet_type,
       SUM(w.weight_kg)      AS total_kg,
       SUM(w.estimated_cost) AS total_cost,
       COUNT(*)              AS entries
FROM waste_entry w
JOIN outlet o ON o.outlet_id = w.outlet_id
GROUP BY o.outlet_id, o.name, o.outlet_type;

-- Waste per cover and waste as % of food spend (joins daily_kpi)
CREATE VIEW IF NOT EXISTS v_waste_per_cover AS
SELECT w.log_date, w.outlet_id, o.name,
       SUM(w.weight_kg)      AS waste_kg,
       SUM(w.estimated_cost) AS waste_cost,
       k.covers,
       k.food_spend,
       CASE WHEN k.covers > 0
            THEN SUM(w.weight_kg) * 1000.0 / k.covers END AS grams_per_cover,
       CASE WHEN k.food_spend > 0
            THEN 100.0 * SUM(w.estimated_cost) / k.food_spend END AS pct_of_spend
FROM waste_entry w
JOIN outlet o ON o.outlet_id = w.outlet_id
JOIN daily_kpi k ON k.log_date = w.log_date AND k.outlet_id = w.outlet_id
GROUP BY w.log_date, w.outlet_id, o.name, k.covers, k.food_spend;

-- Diversion rate (diverted weight / total waste weight)
CREATE VIEW IF NOT EXISTS v_diversion_rate AS
SELECT o.outlet_id, o.name,
       COALESCE(SUM(d.weight_kg), 0) AS diverted_kg,
       (SELECT SUM(weight_kg) FROM waste_entry w2 WHERE w2.outlet_id = o.outlet_id) AS total_waste_kg,
       CASE WHEN (SELECT SUM(weight_kg) FROM waste_entry w2 WHERE w2.outlet_id = o.outlet_id) > 0
            THEN 100.0 * COALESCE(SUM(d.weight_kg), 0)
                 / (SELECT SUM(weight_kg) FROM waste_entry w2 WHERE w2.outlet_id = o.outlet_id)
            END AS diversion_pct
FROM outlet o
LEFT JOIN diversion_entry d ON d.outlet_id = o.outlet_id
GROUP BY o.outlet_id, o.name;

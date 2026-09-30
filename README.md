Waste Intelligence — Food Waste Reduction Tracker (Prototype)
A layered prototype for the Global Sound Waves Innovation Challenge food-waste
reduction proposal. Tracks kitchen/buffet waste by outlet, shift, and category;
estimates cost and CO2e; computes waste-per-cover and waste-as-%-of-spend against
industry benchmarks.

Project structure (layered)
text
waste_tracker/
├── db/
│   ├── schema.sql          # production PostgreSQL DDL (tables + analytics views)
│   └── schema_sqlite.sql   # SQLite-flavored schema for the prototype
├── backend/
│   ├── waste_tracker.py    # data-access layer (all SQL lives here)
│   └── seed.py             # seeds realistic demo data (3 outlets, ~30 days)
├── frontend/
│   └── app.py              # Streamlit dashboard (imports backend only)
├── requirements.txt
└── README.md
Separation of concerns: the frontend never writes SQL — it calls
backend/waste_tracker.py, which owns all queries and the DB connection. This
makes the code reviewable, testable, and trivial to port from SQLite to
PostgreSQL (one connection-string change).

Run it
bash
cd waste_tracker
pip install -r requirements.txt
streamlit run frontend/app.py

On first launch the app auto-creates the SQLite DB and seeds it with demo data
(3 outlets, ~30 days of entries, daily covers/spend, and diversion). Change the
date range and outlet filters at the top.

What the dashboard shows
KPI row: total waste (kg), estimated cost ($), diversion rate %, estimated
CO2e (kg), and entry count.

Waste by category (bar + table) — overproduction, spoilage, plate waste,
trim, expired, other.

Waste by outlet (bar + table).

Waste trend over time (line, per outlet).

Waste per cover (g) vs. the hotel industry benchmark range (150–350 g/cover).

Log a new entry form (expander) — writes through the backend.

Key metrics computed
Metric	Source
Waste by category / outlet / shift	waste_entry
Estimated cost of waste	estimated_cost
Waste per cover (g)	waste_entry + daily_kpi.covers
Waste as % of food spend	waste_entry + daily_kpi.food_spend
Diversion rate	diversion_entry / waste_entry
Estimated CO2e	landfilled kg × 1.0 + diverted kg × 0.05 (EPA WARM-derived)
Production notes
Move to PostgreSQL: run db/schema.sql, point waste_tracker.get_conn() at the
Postgres connection, and switch the SQLite-specific ON CONFLICT upsert in
set_daily_kpi to a Postgres ON CONFLICT clause.

Add authentication + role-based access (stewards log, analysts/managers read).

The CO2e factor is a conservative configurable constant in
backend/waste_tracker.py; refine with your property's actual landfill/diversion
channels using EPA WARM.

Keep this a measurement/visibility tool — pair the data with F&B behavior
changes (production planning, portioning) for actual reduction.
"""Seed the SQLite database with deterministic demo data."""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import waste_tracker as wt

DAYS = 30
RANDOM_SEED = 20260930

OUTLETS = (
	("BUFFET", "All-Day Buffet", "BUFFET", 420, 600, 7.5, 1.35, "buffet_steward"),
	("STEAK", "Steakhouse", "RESTAURANT", 80, 150, 22.0, 0.8, "steakhouse_chef"),
	("MAIN_KIT", "Main Kitchen", "KITCHEN", 250, 380, 6.0, 1.0, "kitchen_steward"),
)

CATEGORIES = (
	("OVERPROD", "Overproduction"),
	("SPOILAGE", "Spoilage"),
	("PLATE", "Plate waste"),
	("TRIM", "Preparation trim"),
	("EXPIRED", "Expired"),
	("OTHER", "Other"),
)

REASONS = (
	("OVERPREP", "Over-preparation"),
	("DEMAND_MISS", "Demand forecast miss"),
	("SHELF_LIFE", "Shelf-life expiry"),
	("MISPREP", "Preparation error"),
	("DAMAGE", "Delivery or storage damage"),
	("OTHER", "Other"),
)

SHIFTS = (
	("BREAK", "Breakfast"),
	("LUNCH", "Lunch"),
	("DINNER", "Dinner"),
	("OVERNIGHT", "Overnight prep"),
)

CHANNELS = (
	("DONATION", "Donation"),
	("COMPOST", "Compost"),
	("ANAEROBIC", "Anaerobic digestion"),
	("ANIMAL_FEED", "Animal feed"),
)

USERS = (
	("buffet_steward", "Demo Buffet Steward", "STEWARD", "BUFFET"),
	("steakhouse_chef", "Demo Steakhouse Chef", "CHEF", "STEAK"),
	("kitchen_steward", "Demo Kitchen Steward", "STEWARD", "MAIN_KIT"),
	("demo_analyst", "Demo Analyst", "ANALYST", None),
)

EVENT_TYPES = (
	("OVERPROD", "DEMAND_MISS", "DINNER", 2.0, 6.0, 11.0),
	("SPOILAGE", "SHELF_LIFE", "BREAK", 0.8, 3.0, 8.0),
	("PLATE", "OTHER", "LUNCH", 0.5, 2.5, 14.0),
	("TRIM", "MISPREP", "LUNCH", 0.7, 2.2, 5.0),
	("EXPIRED", "SHELF_LIFE", "OVERNIGHT", 0.4, 1.8, 9.0),
	("OTHER", "DAMAGE", "DINNER", 0.3, 1.5, 7.0),
)


def seed() -> int:
	"""Insert 30 days of demo records; leave existing waste data untouched."""
	wt.init_db()
	rng = random.Random(RANDOM_SEED)

	with wt.get_conn() as conn:
		if conn.execute("SELECT COUNT(*) FROM waste_entry").fetchone()[0]:
			return 0

		conn.executemany(
			"INSERT OR IGNORE INTO outlet (outlet_id, name, outlet_type) VALUES (?, ?, ?)",
			[(row[0], row[1], row[2]) for row in OUTLETS],
		)
		conn.executemany(
			"INSERT OR IGNORE INTO shift_def (shift_id, label) VALUES (?, ?)", SHIFTS
		)
		conn.executemany(
			"INSERT OR IGNORE INTO waste_category (code, label) VALUES (?, ?)", CATEGORIES
		)
		conn.executemany(
			"INSERT OR IGNORE INTO reason_code (code, label) VALUES (?, ?)", REASONS
		)
		conn.executemany(
			"INSERT OR IGNORE INTO diversion_channel (code, label) VALUES (?, ?)", CHANNELS
		)
		conn.executemany(
			"INSERT OR IGNORE INTO app_user (user_id, display_name, role, home_outlet) "
			"VALUES (?, ?, ?, ?)", USERS,
		)
		conn.executemany(
			"INSERT OR IGNORE INTO target (outlet_id, period, target_pct) VALUES (?, ?, ?)",
			[(outlet[0], "PILOT_90D", 0.15) for outlet in OUTLETS]
			+ [(outlet[0], "YEAR1", 0.25) for outlet in OUTLETS],
		)

		waste_rows = []
		diversion_rows = []
		daily_rows = []
		first_day = date.today() - timedelta(days=DAYS - 1)

		for day_offset in range(DAYS):
			log_date = (first_day + timedelta(days=day_offset)).isoformat()
			trend_factor = 1.0 - 0.12 * day_offset / max(DAYS - 1, 1)

			for outlet in OUTLETS:
				(outlet_id, _, _, min_covers, max_covers, spend_per_cover,
				 waste_scale, user_id) = outlet
				covers = rng.randint(min_covers, max_covers)
				food_spend = round(covers * spend_per_cover * rng.uniform(0.94, 1.06), 2)
				daily_rows.append((log_date, outlet_id, covers, food_spend))

				daily_waste = 0.0
				event_count = rng.randint(2, 4)
				for _ in range(event_count):
					category, reason, shift, low_kg, high_kg, cost_per_kg = rng.choices(
						EVENT_TYPES, weights=(30, 20, 18, 14, 10, 8), k=1
					)[0]
					weight_kg = round(
						rng.uniform(low_kg, high_kg) * waste_scale * trend_factor, 2
					)
					estimated_cost = round(weight_kg * cost_per_kg, 2)
					daily_waste += weight_kg
					waste_rows.append((
						log_date, outlet_id, shift, category, reason, weight_kg,
						estimated_cost, user_id, "Synthetic demo data",
					))

				if rng.random() < 0.8:
					channel = rng.choice(("DONATION", "COMPOST", "ANAEROBIC", "ANIMAL_FEED"))
					diverted_kg = round(daily_waste * rng.uniform(0.15, 0.4), 2)
					diversion_rows.append((log_date, outlet_id, channel, diverted_kg, user_id))

		conn.executemany(
			"INSERT INTO daily_kpi (log_date, outlet_id, covers, food_spend) "
			"VALUES (?, ?, ?, ?)", daily_rows,
		)
		conn.executemany(
			"INSERT INTO waste_entry "
			"(log_date, outlet_id, shift_id, category_code, reason_code, weight_kg, "
			"estimated_cost, logged_by, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
			waste_rows,
		)
		conn.executemany(
			"INSERT INTO diversion_entry "
			"(log_date, outlet_id, channel_code, weight_kg, logged_by) "
			"VALUES (?, ?, ?, ?, ?)", diversion_rows,
		)
		conn.commit()

	return len(waste_rows)


if __name__ == "__main__":
	inserted = seed()
	if inserted:
		print(f"Inserted {inserted} demo waste entries across {DAYS} days.")
	else:
		print("Demo waste data already exists; no changes made.")

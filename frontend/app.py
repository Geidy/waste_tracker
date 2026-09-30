"""
Waste Intelligence — Food Waste Reduction Dashboard (frontend).

Run from the waste_tracker/ directory:
    streamlit run frontend/app.py

Auto-seeds demo data on first launch.
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

# make backend importable when running from the project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import waste_tracker as wt  # noqa: E402

st.set_page_config(page_title="Waste Intelligence Dashboard",
                   page_icon="♻️", layout="wide")
st.title("Food Waste Intelligence Dashboard")
st.caption("Pilot visibility tool — tracks waste by outlet, shift, and category, "
           "estimates cost and CO2e, and supports weekly reduction reviews.")


@st.cache_resource
def ensure_db():
    wt.init_db()
    # seed if empty
    with wt.get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM outlet").fetchone()[0]
    if n == 0:
        from seed import seed
        seed()
    return True


ensure_db()

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
today = date.today()
default_start = today - timedelta(days=30)
col1, col2, col3 = st.columns([1, 1, 2])
start_date = col1.date_input("From", default_start)
end_date = col2.date_input("To", today)
outlets_df = wt.list_outlets()
outlet_opts = outlets_df["outlet_id"].tolist()
outlet_labels = dict(zip(outlets_df["outlet_id"], outlets_df["name"]))
selected = col3.multiselect(
    "Outlets", options=outlet_opts,
    default=outlet_opts,
    format_func=lambda x: outlet_labels.get(x, x))

s, e = str(start_date), str(end_date)

# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------
summary = wt.fetch_summary(s, e, selected or None)
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Total waste (kg)", f"{summary['total_kg']:,.0f}")
c2.metric("Estimated cost", f"${summary['total_cost']:,.0f}")
c3.metric("Diversion rate", f"{summary['diversion_pct']}%")
c4.metric("CO2e (est.)", f"{summary['co2e_kg']:,.0f} kg")
c5.metric("Log entries", f"{summary['entries']:,}")

st.divider()

# ---------------------------------------------------------------------------
# Charts row 1: by category, by outlet
# ---------------------------------------------------------------------------
left, right = st.columns(2)

with left:
    st.subheader("Waste by category")
    cat_df = wt.fetch_by_category(s, e, selected or None)
    if cat_df.empty:
        st.info("No data in this range.")
    else:
        st.bar_chart(cat_df.set_index("category")[
                     "kg"], use_container_width=True)
        st.dataframe(cat_df, hide_index=True, use_container_width=True)

with right:
    st.subheader("Waste by outlet")
    out_df = wt.fetch_by_outlet(s, e)
    if not out_df.empty:
        st.bar_chart(out_df.set_index("outlet")[
                     "kg"], use_container_width=True)
        st.dataframe(out_df, hide_index=True, use_container_width=True)
    else:
        st.info("No data in this range.")

st.divider()

# ---------------------------------------------------------------------------
# Charts row 2: trend over time, per-cover benchmark
# ---------------------------------------------------------------------------
t1, t2 = st.columns(2)

with t1:
    st.subheader("Waste trend (kg/day)")
    trend_df = wt.fetch_trend(s, e, selected or None)
    if not trend_df.empty:
        pivot = trend_df.pivot(
            index="log_date", columns="outlet", values="kg").fillna(0)
        st.line_chart(pivot, use_container_width=True)
    else:
        st.info("No trend data.")

with t2:
    st.subheader("Waste per cover (g) vs benchmark")
    pc_df = wt.fetch_per_cover(s, e)
    if not pc_df.empty:
        avg = pc_df.groupby("outlet")["grams_per_cover"].mean().reset_index()
        st.bar_chart(avg.set_index("outlet")["grams_per_cover"],
                     use_container_width=True)
        lo, hi = wt.BENCH_GRAMS_PER_COVER
        st.caption(
            f"Industry benchmark range: {lo:.0f}–{hi:.0f} g/cover (hotel average).")
        st.dataframe(avg, hide_index=True, use_container_width=True)
    else:
        st.info("No per-cover data (needs daily_kpi entries).")

st.divider()

# ---------------------------------------------------------------------------
# Before/after pilot comparison
# ---------------------------------------------------------------------------
st.subheader("Pilot before / after")
pc = wt.fetch_pilot_comparison(s, e, selected or None)

if pc["status"] == "NO_DATA":
    st.info("No data to compare in this range.")
else:
    b = pc["baseline"]
    c = pc["current"]
    status = pc["status"]
    status_emoji = {"ON_TRACK": "🟢", "AT_RISK": "🟡",
                    "BEHIND": "🔴"}.get(status, "⚪")

    bw = pc.get("baseline_window")
    cw = pc.get("current_window")
    if bw and bw[0]:
        st.caption(f"Baseline window: {bw[0]} → {bw[1]}   |   "
                   f"Current window: {cw[0]} → {cw[1]}")

    # headline comparison tiles
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Baseline kg/day", f"{b['kg_per_day']:,.1f}")
    m2.metric("Current kg/day", f"{c['kg_per_day']:,.1f}",
              f"{pc['reduction_pct']}% vs baseline")
    m3.metric("Pilot target", f"{pc['target_pct']}%")
    m4.metric("Cost/day saved",
              f"${b['cost_per_day'] - c['cost_per_day']:,.0f}")
    m5.metric("Status", f"{status_emoji} {status.replace('_', ' ')}")

    # baseline vs current bar chart (kg/day and cost/day)
    cmp_df = pd.DataFrame({
        "Baseline": [b["kg_per_day"], b["cost_per_day"], b["grams_per_cover"]],
        "Current": [c["kg_per_day"], c["cost_per_day"], c["grams_per_cover"]],
    }, index=["kg/day", "cost/day", "g/cover"])
    st.bar_chart(cmp_df, use_container_width=True)

    # per-outlet breakdown
    by_out = pc["by_outlet"]
    if not by_out.empty:
        st.markdown("**Reduction by outlet**")
        out_cmp = by_out.set_index(
            "outlet")[["baseline_kg_day", "current_kg_day"]]
        st.bar_chart(out_cmp, use_container_width=True)
        st.dataframe(by_out, hide_index=True, use_container_width=True)
        st.caption(f"Target line: {pc['target_pct']}% reduction. "
                   f"Overall: {pc['reduction_pct']}% — {status.replace('_', ' ')}.")

    # one-click CSV export of the before/after summary
    csv_lines = [
        "Waste Intelligence - Pilot Before/After Summary",
        f"Date range,{s},{e}",
        f"Outlets,{';'.join(selected) if selected else 'All'}",
        f"Baseline window,{bw[0]},{bw[1]}",
        f"Current window,{cw[0]},{cw[1]}",
        "",
        "Metric,Baseline,Current,Change",
        f"kg/day,{b['kg_per_day']},{c['kg_per_day']},{round(b['kg_per_day']-c['kg_per_day'], 2)}",
        f"cost/day ($),{b['cost_per_day']},{c['cost_per_day']},{round(b['cost_per_day']-c['cost_per_day'], 2)}",
        f"grams/cover,{b['grams_per_cover']},{c['grams_per_cover']},{round(b['grams_per_cover']-c['grams_per_cover'], 1)}",
        f"CO2e (kg),{b['co2e']},{c['co2e']},{round(b['co2e']-c['co2e'], 2)}",
        "",
        f"Reduction %,{pc['reduction_pct']}",
        f"Pilot target %,{pc['target_pct']}",
        f"Status,{status}",
        "",
        "Outlet,Baseline kg/day,Current kg/day,Reduction %",
    ]
    if not by_out.empty:
        for _, r in by_out.iterrows():
            csv_lines.append(
                f"{r['outlet']},{r['baseline_kg_day']},{r['current_kg_day']},{r['reduction_pct']}")
    csv_text = "\n".join(csv_lines)
    st.download_button(
        label="📥 Export before/after summary (CSV)",
        data=csv_text,
        file_name="pilot_before_after_summary.csv",
        mime="text/csv",
    )

st.divider()

# ---------------------------------------------------------------------------
# Log a new waste entry
# ---------------------------------------------------------------------------
with st.expander("Log a new waste entry"):
    cats = wt.list_categories()
    reasons = wt.list_reasons()
    shifts = wt.list_shifts()
    users = wt.list_users()
    with st.form("log_form"):
        a, b, c = st.columns(3)
        f_date = a.date_input("Date", today)
        f_outlet = a.selectbox("Outlet", outlet_opts,
                               format_func=lambda x: outlet_labels.get(x, x))
        f_shift = a.selectbox("Shift", shifts["shift_id"],
                              format_func=lambda x: shifts.loc[
                                  shifts["shift_id"] == x, "label"].iloc[0])
        f_cat = b.selectbox("Category", cats["code"],
                            format_func=lambda x: cats.loc[cats["code"] == x, "label"].iloc[0])
        f_reason = b.selectbox("Reason", [None] + reasons["code"].tolist(),
                               format_func=lambda x: reasons.loc[
                                   reasons["code"] == x, "label"].iloc[0] if x else "—")
        f_weight = b.number_input(
            "Weight (kg)", min_value=0.0, value=2.5, step=0.1)
        f_cost = c.number_input("Estimated cost ($)",
                                min_value=0.0, value=15.0, step=0.5)
        f_user = c.selectbox("Logged by", users["user_id"],
                             format_func=lambda x: users.loc[
                                 users["user_id"] == x, "display_name"].iloc[0])
        f_notes = c.text_input("Notes")
        submitted = st.form_submit_button("Save entry")
        if submitted:
            wt.log_waste(str(f_date), f_outlet, f_shift, f_cat,
                         f_reason, f_weight, f_cost, f_user, f_notes or None)
            st.success("Entry saved.")
            st.rerun()

st.caption("Prototype • SQLite demo DB • see backend/waste_tracker.py for the "
           "data layer and db/schema.sql for the production PostgreSQL DDL.")

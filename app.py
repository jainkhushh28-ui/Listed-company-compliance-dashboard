from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import engine as E

st.set_page_config(page_title="Listing Compliance Compass", layout="wide",
                   initial_sidebar_state="expanded")

DATA = Path(__file__).parent / "data"
CATALOG_PATH = DATA / "regulatory_catalog.csv"
PATHS = {"companies": DATA / "companies.csv", "status": DATA / "task_status.csv", "events": DATA / "events.csv"}
COLUMNS = {"companies": E.COMPANY_COLUMNS, "status": E.STATUS_COLUMNS, "events": E.EVENT_COLUMNS}
# Local use: set COMPLIANCE_PERSIST=1 to also write changes to the CSV files in /data.
PERSIST = os.environ.get("COMPLIANCE_PERSIST", "0") == "1"

STATE_COLORS = {
    "Completed": "#2f5d9b", "Completed late": "#8fb0d8", "Upcoming": "#d7dde6", "Due soon": "#8b95a5",
    "Overdue": "#1a1f2b", "Date needed": "#eceff3", "Not applicable": "#f3f4f6",
}
NAVY = "#1f3a5f"
MONTHS_FY = [4, 5, 6, 7, 8, 9, 10, 11, 12, 1, 2, 3]


def today() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Kolkata")).date()
    except Exception:
        return date.today()


# ------------------------------------------------------------------ data layer
@st.cache_data
def load_catalog() -> pd.DataFrame:
    return pd.read_csv(CATALOG_PATH, dtype=str).fillna("")


def read_table(name: str) -> pd.DataFrame:
    path = PATHS[name]
    df = pd.read_csv(path, dtype=str).fillna("") if path.exists() else pd.DataFrame()
    if name == "companies" and "listing_category" in df.columns and "entity_type" not in df.columns:
        mapping = {"Main Board": "MB", "Emerge / SME": "SME", "IGP": "IGP", "Debt": "DEBT"}
        df["entity_type"] = df["listing_category"].map(mapping).fillna("MB")
        if "top_1000" in df.columns:
            df["market_cap_tier"] = df["top_1000"].map(lambda v: "Top 1000" if E.as_bool(v) else "Outside Top 2000")
        if "promoter_sast" in df.columns:
            df["promoter_group"] = df["promoter_sast"]
    for column in COLUMNS[name]:
        if column not in df.columns:
            df[column] = ""
    return df[COLUMNS[name]].astype(str).reset_index(drop=True)


def persist() -> None:
    if PERSIST:
        for name, path in PATHS.items():
            st.session_state[name].to_csv(path, index=False)


def init_state() -> None:
    for name in PATHS:
        if name not in st.session_state:
            st.session_state[name] = read_table(name)
    ss = st.session_state
    ids = ss.companies["company_id"].tolist()
    if "pending_company_id" in ss:
        ss.selected_company_id = ss.pop("pending_company_id")
    if "selected_company_id" not in ss or ss.selected_company_id not in ids:
        ss.selected_company_id = ids[0]
    ss.setdefault("tracker_ver", 0)
    ss.setdefault("form_ver", 0)


def make_id(name: str, existing: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "company"
    cid, n = base, 2
    while cid in existing:
        cid, n = f"{base}-{n}", n + 1
    return cid


def export_workspace() -> str:
    ss = st.session_state
    return json.dumps({"version": 2, **{k: ss[k].to_dict("records") for k in PATHS}}, indent=1)


def import_workspace(raw: bytes) -> None:
    data = json.loads(raw)
    for name in PATHS:
        df = pd.DataFrame(data.get(name, []), columns=COLUMNS[name]).fillna("").astype(str)
        if name == "companies" and df.empty:
            raise ValueError("Backup has no companies.")
        st.session_state[name] = df
    st.session_state.tracker_ver += 1
    persist()


# ------------------------------------------------------------------ profile form
def profile_inputs(prefix: str, d: dict) -> dict:
    """Render profile questions; returns the chosen values. Questions adapt to the entity type."""
    out: dict = {}
    types = list(E.ENTITY_LABELS)
    c1, c2 = st.columns([1.3, 1])
    out["entity_type"] = c1.selectbox(
        "1 · Type of entity", types, index=types.index(d["entity_type"]) if d["entity_type"] in types else 2,
        format_func=E.ENTITY_LABELS.get, key=f"{prefix}_et")
    et = out["entity_type"]
    out["also_debt_listed"] = False
    if et in E.EQUITY_LISTED:
        out["also_debt_listed"] = c2.checkbox("Also has listed debt (NCDs)", value=E.as_bool(d["also_debt_listed"]),
                                              key=f"{prefix}_debt")
    out["market_cap_tier"] = "Outside Top 2000"
    if et == "MB":
        tier = d["market_cap_tier"] if d["market_cap_tier"] in E.TIER_OPTIONS else "Outside Top 2000"
        out["market_cap_tier"] = st.select_slider(
            "2 · Market-capitalisation tier (pick the smallest list you are in)", E.TIER_OPTIONS, value=tier,
            key=f"{prefix}_tier", help="Top 100 ⊂ Top 250 ⊂ … ⊂ Top 2000. Tier is set from market cap at the end of the "
            "preceding financial year; confirm averaging and stickiness under Reg 3(2).")
    elif et in E.LISTED:
        st.caption("Market-cap tiers apply to Main Board equity only.")
    st.markdown("**3 · Facts that switch compliances on or off**")
    cols = st.columns(2)
    shown = [f for f in E.FACTS if E.fact_visible(f[3], et, out["also_debt_listed"])]
    for i, (key, label, hlp, _) in enumerate(shown):
        default = E.as_bool(d.get(key, False))
        out[key] = cols[i % 2].checkbox(label, value=default, help=hlp, key=f"{prefix}_{key}")
    for f in E.FACTS:
        out.setdefault(f[0], False)
    return out


def blank_company() -> dict:
    return {"entity_type": "MB", "also_debt_listed": "False", "market_cap_tier": "Outside Top 2000", "promoter_group": "True"}


# ------------------------------------------------------------------ styling
init_state()
catalog = load_catalog()
ss = st.session_state

st.markdown("""
<style>
.block-container { padding-top: 3.5rem; padding-bottom: 3rem; max-width: 1280px; }
h1, h2, h3, h4 { font-weight: 600; letter-spacing: -0.01em; }
h3 { font-size: 1.05rem !important; margin-top: .4rem; }
.title { font-size: 1.7rem; font-weight: 650; letter-spacing: -0.02em; line-height: 1.2; margin: 0; }
.sub { color: #5b6675; font-size: .92rem; margin: .25rem 0 1.1rem; }
.label { color:#5b6675; font-size:.72rem; font-weight:600; letter-spacing:.08em; text-transform:uppercase; margin:.2rem 0 .35rem; }
div[data-testid="stMetric"] { background:#fff; border:1px solid #e3e7ee; border-radius:8px; padding:.75rem 1rem; }
div[data-testid="stMetricLabel"] p { color:#5b6675; font-size:.8rem; }
div[data-testid="stMetricValue"] { font-size:1.6rem; font-weight:600; }
button[data-baseweb="tab"] { font-size:.95rem; }
hr { margin: 1.2rem 0; }
@media (max-width:760px){ .block-container{padding-top:3rem;padding-left:1rem;padding-right:1rem;} .title{font-size:1.4rem;} }
</style>""", unsafe_allow_html=True)

CHART_CFG = dict(labelColor="#5b6675", titleColor="#5b6675")


def style(chart):
    return (chart.configure_view(strokeWidth=0).configure_axis(grid=True, gridColor="#eef1f5", domain=False, **CHART_CFG)
            .configure_legend(labelColor="#5b6675", symbolType="square"))


# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown('<div class="title" style="font-size:1.2rem">Compliance Compass</div>', unsafe_allow_html=True)
    st.markdown('<div class="sub">Compliance tracker for Indian companies</div>', unsafe_allow_html=True)
    ids = ss.companies["company_id"].tolist()
    names = dict(zip(ss.companies["company_id"], ss.companies["company_name"]))
    st.selectbox("Company", ids, format_func=lambda i: names.get(i, i), key="selected_company_id")
    with st.expander("Settings"):
        as_of = st.date_input("As-of date", value=today(), help="Controls overdue and due-soon status.")
    fy_now = E.fy_start_year(as_of)
    fy = st.selectbox("Financial year", [fy_now - 2, fy_now - 1, fy_now, fy_now + 1], index=2, format_func=E.fy_label)
    with st.expander("Save and restore"):
        st.caption("Your data stays in this browser session only. Download a backup before closing the tab and "
                   "upload it next time.")
        st.download_button("Download backup", export_workspace(), "compliance-workspace.json", "application/json")
        up = st.file_uploader("Restore from backup", type="json", key=f"restore_{ss.form_ver}")
        if up is not None and st.button("Restore"):
            try:
                import_workspace(up.getvalue())
                ss.form_ver += 1
                st.rerun()
            except Exception as err:  # noqa: BLE001
                st.error(f"Could not restore: {err}")

company_row = ss.companies[ss.companies["company_id"] == ss.selected_company_id].iloc[0].to_dict()
profile = E.company_profile(company_row)
flags = E.derive_flags(profile)
cid = profile["company_id"]
applicable = E.applicable_rules(catalog, profile)
events_list = ss.events[ss.events["company_id"] == cid].to_dict("records")
df = E.attach_status(E.generate_tasks(catalog, profile, fy, events_list), ss.status, cid, as_of)

# ------------------------------------------------------------------ header + period
sub = [E.ENTITY_LABELS[profile["entity_type"]]]
if profile["entity_type"] == "MB":
    sub.append(profile["market_cap_tier"])
if flags["debt_listed"] and profile["entity_type"] != "DEBT":
    sub.append("debt also listed")
if flags["hvdle"]:
    sub.append("HVDLE")
sub.append(E.fy_label(fy))
st.markdown(f'<div class="title">{profile["company_name"]}</div><div class="sub">{" · ".join(sub)}</div>', unsafe_allow_html=True)
if profile["entity_type"] == "IGP":
    st.info("IGP relaxations are not verified in the source matrix, so Main Board obligations are shown. "
            "Mark items Not applicable where an exchange circular relaxes them.")

month_labels = [date(2000, m, 1).strftime("%b") for m in MONTHS_FY]
default_period = date(2000, as_of.month, 1).strftime("%b") if E.fy_bounds(fy)[0] <= as_of <= E.fy_bounds(fy)[1] else "All year"
st.markdown('<div class="label">Period</div>', unsafe_allow_html=True)
picked = st.segmented_control("Period", ["All year"] + month_labels, default=default_period, key="period",
                              label_visibility="collapsed")
period = picked or "All year"


def in_period(d) -> bool:
    return d is not None and d.strftime("%b") == period


pdf = df if period == "All year" else df[df["due_date"].map(in_period)]
active = pdf[pdf["state"] != "Not applicable"]
total = len(active)
done = int(active["state"].isin(["Completed", "Completed late"]).sum())
overdue = int((active["state"] == "Overdue").sum())
soon = int((active["state"] == "Due soon").sum())
pct = round(100 * done / total) if total else 0
period_text = E.fy_label(fy) if period == "All year" else f"{period} {fy if period in month_labels[:9] else fy + 1}"

tab_dash, tab_track, tab_event, tab_guide, tab_profile = st.tabs(
    ["Overview", "Tracker", "Event log", "Requirements", "Company profile"])

# ------------------------------------------------------------------ overview
with tab_dash:
    k = st.columns(5)
    k[0].metric("Applicable", total)
    k[1].metric("Completed", done)
    k[2].metric("Pending", total - done)
    k[3].metric("Overdue", overdue)
    k[4].metric("Completion", f"{pct}%")
    st.progress(pct / 100)
    need_date = int((df["state"] == "Date needed").sum())
    if need_date and period == "All year":
        st.caption(f"{need_date} AGM-linked items have no date yet. Enter the AGM date in the Tracker tab.")
    if total == 0:
        st.info(f"No compliances fall due in {period_text}.")
    else:
        c1, c2 = st.columns([1, 1.5], gap="large")
        with c1:
            st.markdown(f"### Status – {period_text}")
            counts = pdf[pdf["state"] != "Not applicable"].groupby("state").size().reset_index(name="count")
            donut = alt.Chart(counts).mark_arc(innerRadius=60, outerRadius=100, stroke="#fff").encode(
                theta="count:Q",
                color=alt.Color("state:N", scale=alt.Scale(domain=list(STATE_COLORS), range=list(STATE_COLORS.values())),
                                legend=alt.Legend(title=None, orient="bottom", columns=3)),
                tooltip=["state", "count"]).properties(height=290)
            st.altair_chart(style(donut), width="stretch")
        with c2:
            st.markdown(f"### Due by month – {E.fy_label(fy)}")
            dated = df[df["due_date"].notna() & (df["state"] != "Not applicable")].copy()
            dated["month"] = dated["due_date"].map(lambda d: d.strftime("%b"))
            monthly = dated.groupby(["month", "state"]).size().reset_index(name="count")
            sel = period if period != "All year" else ""
            bars = alt.Chart(monthly).mark_bar().encode(
                x=alt.X("month:N", sort=month_labels, title=None, axis=alt.Axis(labelAngle=0)),
                y=alt.Y("count:Q", title=None),
                color=alt.Color("state:N", scale=alt.Scale(domain=list(STATE_COLORS), range=list(STATE_COLORS.values())), legend=None),
                opacity=alt.condition(alt.datum.month == sel, alt.value(1), alt.value(1 if not sel else 0.4)),
                tooltip=["month", "state", "count"]).properties(height=290)
            st.altair_chart(style(bars), width="stretch")
        c3, c4 = st.columns(2, gap="large")
        with c3:
            st.markdown(f"### By law – {period_text}")
            law = active.assign(Result=active["state"].map(lambda s: "Completed" if s in ("Completed", "Completed late") else "Pending"))
            law = law.groupby(["law", "Result"]).size().reset_index(name="count")
            chart = alt.Chart(law).mark_bar().encode(
                y=alt.Y("law:N", sort=E.LAWS, title=None), x=alt.X("count:Q", title=None),
                color=alt.Color("Result:N", scale=alt.Scale(domain=["Completed", "Pending"], range=[STATE_COLORS["Completed"], "#d7dde6"]),
                                legend=alt.Legend(title=None, orient="bottom")),
                tooltip=["law", "Result", "count"]).properties(height=240)
            st.altair_chart(style(chart), width="stretch")
        with c4:
            st.markdown("### Completed, by month of completion")
            comp = df[df["completed_on"].notna()].copy()
            if comp.empty:
                st.caption("Nothing marked completed yet. Use the Tracker tab to record completions.")
            else:
                comp["m"] = comp["completed_on"].map(lambda d: d.replace(day=1))
                comp["month"] = comp["m"].map(lambda d: d.strftime("%b %Y"))
                cm = comp.groupby(["m", "month"]).size().reset_index(name="count")
                chart = alt.Chart(cm).mark_bar(color=NAVY).encode(
                    x=alt.X("month:N", sort=alt.EncodingSortField("m"), title=None, axis=alt.Axis(labelAngle=0)),
                    y=alt.Y("count:Q", title=None), tooltip=["month", "count"]).properties(height=240)
                st.altair_chart(style(chart), width="stretch")
        st.markdown(f"### Needs attention – {period_text}")
        attn = active[active["state"].isin(["Overdue", "Due soon"])]
        if attn.empty:
            st.caption("Nothing overdue or due in the next 14 days.")
        else:
            t = pd.DataFrame({"Due": attn["due_date"].map(E.fmt), "Requirement": attn["title"], "Regulation": attn["regulation"],
                              "Mode of filing": attn["filing_mode"], "Status": attn["state"]})
            st.dataframe(t, hide_index=True, width="stretch", height=min(420, 36 * (len(t) + 1) + 3))
        lr = E.long_range_items(catalog, profile, as_of)
        if lr:
            with st.expander("Longer-range dates"):
                for it in lr:
                    st.write(f"**{E.fmt(it['due'])}** – {it['title']}. {it['timeline']}")

# ------------------------------------------------------------------ tracker
def apply_status(task_ids: list[str], status: str, done_on: date | None, note: str) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    records = {(r["company_id"], r["task_id"]): r for r in ss.status.to_dict("records")}
    for tid in task_ids:
        old = records.get((cid, tid), {})
        records[(cid, tid)] = {"company_id": cid, "task_id": tid, "status": status,
                               "completion_date": done_on.isoformat() if (status == "Completed" and done_on) else "",
                               "evidence_note": note or old.get("evidence_note", ""), "updated_at": stamp}
    ss.status = pd.DataFrame(list(records.values()), columns=E.STATUS_COLUMNS).astype(str)
    persist()
    ss.tracker_ver += 1
    st.toast(f"{len(task_ids)} compliance(s) updated.")
    st.rerun()


with tab_track:
    st.caption(f"Showing {period_text}. Tick the compliances you want to update, then choose an action below the table.")
    f1, f2, f3, f4 = st.columns([1.3, 1.1, 1.1, 1.5])
    f_law = f1.multiselect("Law", E.LAWS, placeholder="All laws")
    f_type = f2.multiselect("Type", ["Filing", "Disclosure", "Governance", "Record / policy"], placeholder="All types")
    f_view = f3.selectbox("Show", ["All", "Pending", "Overdue", "Completed"])
    f_text = f4.text_input("Search", placeholder="AOC-4, Reg 31, FC-GPR")

    v = pdf.copy()
    if f_law: v = v[v["law"].isin(f_law)]
    if f_type: v = v[v["nature"].isin(f_type)]
    if f_view == "Pending": v = v[v["state"].isin(["Overdue", "Due soon", "Upcoming", "Date needed"])]
    elif f_view == "Overdue": v = v[v["state"] == "Overdue"]
    elif f_view == "Completed": v = v[v["state"].isin(["Completed", "Completed late"])]
    if f_text:
        needle = f_text.lower()
        v = v[v.apply(lambda r: needle in f"{r['title']} {r['regulation']} {r['filing_mode']}".lower(), axis=1)]
    v = v.reset_index(drop=True)

    a1, a2 = st.columns([1, 3])
    agm_now = profile["agm_date"]
    lo_fy, hi_fy = E.fy_bounds(fy)
    agm_in = a1.date_input("AGM date this year", value=agm_now if agm_now and lo_fy <= agm_now <= hi_fy else None,
                           key=f"agm_{cid}_{fy}", help="Sets AOC-4, MGT-7, annual report and voting-results dates.")
    if agm_in and agm_in != agm_now:
        idx = ss.companies.index[ss.companies["company_id"] == cid][0]
        ss.companies.loc[idx, "agm_date"] = agm_in.isoformat()
        persist()
        st.rerun()

    if v.empty:
        st.info("No compliances match the selected period and filters.")
    else:
        table = pd.DataFrame({
            "Due": v["due_date"].map(E.fmt), "Requirement": v["title"], "Regulation": v["regulation"], "Law": v["law"],
            "Mode of filing": v["filing_mode"], "Type": v["nature"], "Status": v["state"],
            "Completed on": v["completed_on"].map(E.fmt),
        })
        event = st.dataframe(
            table, hide_index=True, width="stretch", height=min(540, 36 * (len(table) + 1) + 3),
            on_select="rerun", selection_mode="multi-row", key=f"tbl_{cid}_{fy}_{period}_{ss.tracker_ver}_{f_view}_{len(v)}",
            column_config={"Due": st.column_config.TextColumn(width="small"), "Requirement": st.column_config.TextColumn(width="large"),
                           "Regulation": st.column_config.TextColumn(width="medium"), "Law": st.column_config.TextColumn(width="small"),
                           "Mode of filing": st.column_config.TextColumn(width="large"), "Type": st.column_config.TextColumn(width="small"),
                           "Status": st.column_config.TextColumn(width="small"), "Completed on": st.column_config.TextColumn(width="small")})
        rows = list(event.selection.rows)
        chosen = v.loc[rows, "task_id"].tolist() if rows else []
        st.markdown(f'<div class="label">Update status{" – " + str(len(chosen)) + " selected" if chosen else ""}</div>', unsafe_allow_html=True)
        b1, b2, b3 = st.columns([1, 2, 2.2])
        done_date = b1.date_input("Completion date", value=as_of, key=f"cd_{ss.tracker_ver}")
        note = b2.text_input("Note or evidence reference (optional)", key=f"nt_{ss.tracker_ver}", placeholder="e.g. NEAPS acknowledgement no.")
        with b3:
            st.write("")
            st.write("")
            x1, x2, x3 = st.columns(3)
            if x1.button("Mark completed", type="primary", disabled=not chosen, width="stretch"):
                apply_status(chosen, "Completed", done_date, note)
            if x2.button("Mark pending", disabled=not chosen, width="stretch"):
                apply_status(chosen, "Pending", None, note)
            if x3.button("Not applicable", disabled=not chosen, width="stretch"):
                apply_status(chosen, "Not applicable", None, note)
        st.download_button("Download this list (CSV)", table.to_csv(index=False), f"{cid}-{fy}-{period}-compliance.csv", "text/csv")

# ------------------------------------------------------------------ events
with tab_event:
    st.markdown("### Event-based compliances")
    st.caption("Log an event such as a Board meeting, allotment, director change or insider trade. The due date is "
               "calculated and the item appears in the Tracker for that month.")
    ev_rules = applicable[applicable["deadline_rule"].str.startswith("event_")]
    if ev_rules.empty:
        st.info("No event-based compliances apply to this profile.")
    else:
        labels = {r["id"]: f"{r['regulation']} – {r['title']}" for r in ev_rules.to_dict("records")}
        with st.form(f"event_form_{ss.form_ver}", border=False):
            e1, e2 = st.columns([2.4, 1])
            rule_id = e1.selectbox("Compliance triggered", list(labels), format_func=labels.get)
            ev_date = e2.date_input("Event date", value=as_of)
            ev_note = st.text_input("Note (optional)", placeholder="e.g. Board meeting for Q2 results")
            if st.form_submit_button("Add event", type="primary"):
                new = pd.DataFrame([{"event_id": uuid.uuid4().hex[:8], "company_id": cid, "rule_id": rule_id,
                                     "event_date": ev_date.isoformat(), "event_note": ev_note}])
                ss.events = pd.concat([ss.events, new], ignore_index=True)
                persist()
                ss.form_ver += 1
                st.rerun()
        mine_events = ss.events[ss.events["company_id"] == cid]
        if mine_events.empty:
            st.caption("No events logged yet.")
        else:
            rows = []
            for r in mine_events.to_dict("records"):
                rule = catalog[catalog["id"] == r["rule_id"]]
                if rule.empty:
                    continue
                rr = rule.iloc[0]
                ed = E.parse_date(r["event_date"])
                rows.append({"event_id": r["event_id"], "Event date": E.fmt(ed), "Due": E.fmt(E.event_due(rr["deadline_rule"], ed)),
                             "Compliance": rr["title"], "Regulation": rr["regulation"], "Note": r["event_note"]})
            st.dataframe(pd.DataFrame(rows).drop(columns="event_id"), hide_index=True, width="stretch")
            d1, d2 = st.columns([2.4, 1])
            drop = d1.multiselect("Remove events", [x["event_id"] for x in rows],
                                  format_func=lambda i: next(f"{x['Event date']} – {x['Compliance']}" for x in rows if x["event_id"] == i))
            d2.write("")
            d2.write("")
            if d2.button("Remove selected", width="stretch") and drop:
                ss.events = ss.events[~ss.events["event_id"].isin(drop)].reset_index(drop=True)
                persist()
                st.rerun()

# ------------------------------------------------------------------ requirements
with tab_guide:
    st.markdown("### Applicable requirements")
    st.caption(f"{len(applicable)} requirements apply to this entity across {applicable['law'].nunique()} laws.")
    g_law = st.multiselect("Law", E.LAWS, placeholder="All laws", key="guide_law")
    gv = applicable if not g_law else applicable[applicable["law"].isin(g_law)]
    matrix = gv[["law", "frequency", "nature", "title", "regulation", "timeline", "filing_mode", "verification"]].rename(columns={
        "law": "Law", "frequency": "Frequency", "nature": "Type", "title": "Requirement", "regulation": "Regulation",
        "timeline": "Timeline", "filing_mode": "Mode of filing", "verification": "Verification"})
    st.dataframe(matrix, hide_index=True, width="stretch", height=380)
    st.download_button("Download applicability matrix (CSV)", matrix.to_csv(index=False), f"{cid}-applicability-matrix.csv", "text/csv")
    if len(gv):
        pick = st.selectbox("Requirement details", gv["id"].tolist(), format_func=lambda i: gv.set_index("id").loc[i, "title"])
        r = gv.set_index("id").loc[pick]
        x, y = st.columns(2, gap="large")
        x.markdown(f"**Law** – {r['law']}\n\n**Regulation** – {r['regulation']}\n\n**Type** – {r['nature']}, {r['frequency']}\n\n**Timeline** – {r['timeline']}")
        y.markdown(f"**Mode of filing** – {r['filing_mode']}\n\n**Verification** – {r['verification']}\n\n**Source** – [{r['source_url']}]({r['source_url']})")
        if r["note"]:
            st.info(r["note"])
    with st.expander("Basis and limits of this catalogue"):
        st.markdown(
            "Built from the *Entity-wise Regulatory Compliance Matrix (India)* dated 24 October 2026. It is a planning "
            "tool, not legal advice.\n\n"
            "- **Confirmed in the matrix:** small-company thresholds, HVDLE at ₹5,000 cr, LODR market-cap tiers, SME "
            "exemptions and Reg 23, PIT 2025 amendment and PAN-freeze circular, FLA, FC-GPR, FC-TRS, APR, triennial "
            "DIR-3 KYC and the 30-day Integrated Filing (Governance) timeline.\n"
            "- **Check current text:** section-level size thresholds, PAS-6 timing, ECB reporting, SAST open-offer "
            "mechanics, IGP relaxations and Reg 17(1B).\n"
            "- Working-day calculations skip weekends only, not exchange or bank holidays.\n"
            "- Rules are in `data/regulatory_catalog.csv`; edit that file to update a rule.")

# ------------------------------------------------------------------ profile
with tab_profile:
    left, right = st.columns([1.4, 1], gap="large")
    with left:
        st.markdown(f"### Edit – {profile['company_name']}")
        p1, p2 = st.columns([1.6, 1])
        name_e = p1.text_input("Company name", company_row["company_name"], key=f"nm_{cid}")
        sym_e = p2.text_input("Symbol", company_row["symbol"], key=f"sy_{cid}")
        vals = profile_inputs(f"ed_{cid}", company_row)
        o1, o2 = st.columns(2)
        owner_e = o1.text_input("Profile owner", company_row["profile_owner"], key=f"ow_{cid}")
        notes_e = o2.text_input("Notes", company_row["notes"], key=f"no_{cid}")
        if st.button("Save profile", type="primary"):
            if not name_e.strip():
                st.error("Enter a company name.")
            elif name_e.strip().casefold() in set(ss.companies[ss.companies["company_id"] != cid]["company_name"].str.casefold()):
                st.error("Another company already uses that name.")
            else:
                idx = ss.companies.index[ss.companies["company_id"] == cid][0]
                upd = {"company_name": name_e.strip(), "symbol": sym_e.strip().upper(), "profile_owner": owner_e, "notes": notes_e,
                       "profile_last_reviewed": today().isoformat(), **{k: str(v) for k, v in vals.items()}}
                for k, v in upd.items():
                    ss.companies.loc[idx, k] = v
                persist()
                ss.tracker_ver += 1
                st.rerun()
        with st.expander("Delete this company"):
            if len(ss.companies) == 1:
                st.caption("At least one company must remain.")
            else:
                ok = st.checkbox("Permanently remove this company, its statuses and its events.", key=f"del_{cid}")
                if st.button("Delete company") and ok:
                    ss.companies = ss.companies[ss.companies["company_id"] != cid].reset_index(drop=True)
                    ss.status = ss.status[ss.status["company_id"] != cid].reset_index(drop=True)
                    ss.events = ss.events[ss.events["company_id"] != cid].reset_index(drop=True)
                    persist()
                    st.rerun()
    with right:
        st.markdown("### Add a company")
        with st.container(border=True):
            n_name = st.text_input("Company name", key=f"new_name_{ss.form_ver}")
            n_sym = st.text_input("Symbol", key=f"new_sym_{ss.form_ver}")
            n_vals = profile_inputs(f"new{ss.form_ver}", blank_company())
            if st.button("Add company", type="primary"):
                if not n_name.strip():
                    st.error("Enter a company name.")
                elif n_name.strip().casefold() in set(ss.companies["company_name"].str.casefold()):
                    st.error("That company already exists.")
                else:
                    new_id = make_id(n_name, set(ss.companies["company_id"]))
                    rec = {c: "" for c in E.COMPANY_COLUMNS}
                    rec.update({"company_id": new_id, "company_name": n_name.strip(), "symbol": n_sym.strip().upper(),
                                "profile_last_reviewed": today().isoformat(), **{k: str(v) for k, v in n_vals.items()}})
                    ss.companies = pd.concat([ss.companies, pd.DataFrame([rec])], ignore_index=True)
                    persist()
                    ss.pending_company_id = new_id
                    ss.form_ver += 1
                    st.rerun()
    st.markdown("### All companies")
    reg = ss.companies[["company_name", "symbol", "entity_type", "market_cap_tier", "agm_date", "profile_owner", "profile_last_reviewed"]].copy()
    reg["entity_type"] = reg["entity_type"].map(E.ENTITY_LABELS)
    st.dataframe(reg.rename(columns={"company_name": "Company", "symbol": "Symbol", "entity_type": "Entity type", "market_cap_tier": "Tier",
                                     "agm_date": "AGM date", "profile_owner": "Owner", "profile_last_reviewed": "Last reviewed"}),
                 hide_index=True, width="stretch")

st.divider()
st.caption("Planning prototype based on the Entity-wise Regulatory Compliance Matrix (24 Oct 2026). Not legal advice. Verify every "
           "requirement against current law before filing.")

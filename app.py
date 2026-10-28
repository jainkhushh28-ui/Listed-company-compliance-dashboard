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

st.set_page_config(page_title="Listing Compliance Compass", page_icon="◈", layout="wide",
                   initial_sidebar_state="expanded")

DATA = Path(__file__).parent / "data"
CATALOG_PATH = DATA / "regulatory_catalog.csv"
PATHS = {"companies": DATA / "companies.csv", "status": DATA / "task_status.csv", "events": DATA / "events.csv"}
COLUMNS = {"companies": E.COMPANY_COLUMNS, "status": E.STATUS_COLUMNS, "events": E.EVENT_COLUMNS}
# Local use: set COMPLIANCE_PERSIST=1 to also write changes to the CSV files in /data.
PERSIST = os.environ.get("COMPLIANCE_PERSIST", "0") == "1"

STATE_COLORS = {
    "Completed": "#1f9d6b", "Completed late": "#2bb3a6", "Upcoming": "#4c8bf5", "Due soon": "#f5a524",
    "Overdue": "#e5484d", "Date needed": "#8b8fa3", "Not applicable": "#c3c7d1",
}
STATE_ICON = {"Completed": "🟢", "Completed late": "🟢", "Upcoming": "🔵", "Due soon": "🟠", "Overdue": "🔴",
              "Date needed": "⚪", "Not applicable": "⚫"}
LAW_COLORS = {"Companies Act, 2013": "#e8833a", "SEBI LODR": "#4c6ef5", "SEBI PIT": "#12a594",
              "SEBI SAST": "#9b59d0", "FEMA": "#d6409f"}
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
.block-container { padding-top: 5rem; padding-bottom: 3rem; max-width: 1500px; }
[data-testid="stSidebar"] { background: var(--secondary-background-color); }
.eyebrow { opacity:.72; font-size:.8rem; font-weight:600; letter-spacing:.08em; text-transform:uppercase; }
.hero-title { font-size:2rem; font-weight:700; letter-spacing:-.03em; margin:.1rem 0 .3rem; }
.chip { display:inline-block; padding:.18rem .65rem; margin:0 .35rem .35rem 0; border-radius:999px; font-size:.8rem;
  background: color-mix(in srgb, var(--primary-color) 14%, transparent);
  border:1px solid color-mix(in srgb, var(--primary-color) 40%, transparent); }
div[data-testid="stMetric"] { background: var(--secondary-background-color); border:1px solid
  color-mix(in srgb, var(--text-color) 14%, transparent); border-radius:14px; padding:.7rem 1rem; }
.notice { background: color-mix(in srgb, var(--primary-color) 12%, transparent); border:1px solid
  color-mix(in srgb, var(--primary-color) 40%, transparent); border-radius:10px; padding:.7rem 1rem; font-size:.88rem; }
@media (max-width:760px){ .block-container{padding-top:4rem;padding-left:1rem;padding-right:1rem;} .hero-title{font-size:1.6rem;} }
</style>""", unsafe_allow_html=True)

# ------------------------------------------------------------------ sidebar
with st.sidebar:
    st.markdown("### ◈ Compliance Compass")
    st.caption("Entity-wise compliance tracker for listed and unlisted companies in India")
    ids = ss.companies["company_id"].tolist()
    names = dict(zip(ss.companies["company_id"], ss.companies["company_name"]))
    st.selectbox("Company", ids, format_func=lambda i: names.get(i, i), key="selected_company_id")
    as_of = st.date_input("As-of date", value=today(), help="Controls overdue / due-soon status.")
    fy_now = E.fy_start_year(as_of)
    fy = st.selectbox("Financial year", [fy_now - 2, fy_now - 1, fy_now, fy_now + 1], index=2, format_func=E.fy_label)
    st.divider()
    with st.expander("💾 Save / restore my work", expanded=False):
        st.caption("Your data lives only in this browser session. Download a backup before you close the tab; "
                   "upload it next time to continue.")
        st.download_button("Download backup (.json)", export_workspace(), "compliance-workspace.json", "application/json")
        up = st.file_uploader("Restore from backup", type="json", key=f"restore_{ss.form_ver}")
        if up is not None and st.button("Restore now"):
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
tasks = E.generate_tasks(catalog, profile, fy, events_list)
df = E.attach_status(tasks, ss.status, cid, as_of)

# ------------------------------------------------------------------ header
chips = [E.ENTITY_LABELS[profile["entity_type"]]]
if profile["entity_type"] == "MB":
    chips.append(profile["market_cap_tier"])
if flags["debt_listed"] and profile["entity_type"] != "DEBT":
    chips.append("Debt also listed")
if flags["hvdle"]:
    chips.append("HVDLE")
if flags["small"]:
    chips.append("Small company")
chips.append(E.fy_label(fy))
st.markdown('<div class="eyebrow">Compliance management dashboard</div>', unsafe_allow_html=True)
st.markdown(f'<div class="hero-title">{profile["company_name"]}</div>', unsafe_allow_html=True)
st.markdown("".join(f'<span class="chip">{c}</span>' for c in chips), unsafe_allow_html=True)
if profile["entity_type"] == "IGP":
    st.warning("IGP-specific relaxations could not be verified in the source matrix. IGP is shown with Main Board "
               "obligations; check exchange circulars for relaxations and mark items Not applicable where relaxed.")

tab_dash, tab_track, tab_event, tab_guide, tab_profile = st.tabs(
    ["📊 Dashboard", "✅ Compliance tracker", "⚡ Event log", "📖 Requirement guide", "🏢 Company profile"])

active = df[df["state"] != "Not applicable"]
total = len(active)
done = int(active["state"].isin(["Completed", "Completed late"]).sum())
overdue = int((active["state"] == "Overdue").sum())
soon = int((active["state"] == "Due soon").sum())
need_date = int((active["state"] == "Date needed").sum())
pct = round(100 * done / total) if total else 0

# ------------------------------------------------------------------ dashboard
with tab_dash:
    k = st.columns(6)
    k[0].metric("Applicable", total)
    k[1].metric("Completed", done)
    k[2].metric("Pending", total - done)
    k[3].metric("Overdue", overdue)
    k[4].metric("Due in 14 days", soon)
    k[5].metric("Completion", f"{pct}%")
    st.progress(pct / 100)
    if need_date:
        st.info(f"{need_date} AGM-linked items need the AGM date. Set it in the Compliance tracker tab.")
    if total == 0:
        st.info("No compliances match this profile for the selected year.")
    else:
        c1, c2 = st.columns([0.8, 1.2], gap="large")
        with c1:
            st.subheader("Status")
            counts = df.groupby("state").size().reset_index(name="count")
            donut = alt.Chart(counts).mark_arc(innerRadius=62, outerRadius=105).encode(
                theta="count:Q",
                color=alt.Color("state:N", scale=alt.Scale(domain=list(STATE_COLORS), range=list(STATE_COLORS.values())),
                                legend=alt.Legend(title=None, orient="bottom", columns=2)),
                tooltip=["state", "count"]).properties(height=300)
            st.altair_chart(donut, width="stretch")
        with c2:
            st.subheader("Monthly workload by status")
            dated = df[df["due_date"].notna()].copy()
            dated["month"] = dated["due_date"].map(lambda d: d.strftime("%b %Y"))
            order = [date(fy if m >= 4 else fy + 1, m, 1).strftime("%b %Y") for m in MONTHS_FY]
            monthly = dated.groupby(["month", "state"]).size().reset_index(name="count")
            bars = alt.Chart(monthly).mark_bar().encode(
                x=alt.X("month:N", sort=order, title=None),
                y=alt.Y("count:Q", title="Compliances due"),
                color=alt.Color("state:N", scale=alt.Scale(domain=list(STATE_COLORS), range=list(STATE_COLORS.values())),
                                legend=None),
                tooltip=["month", "state", "count"]).properties(height=300)
            st.altair_chart(bars, width="stretch")
        c3, c4 = st.columns(2, gap="large")
        with c3:
            st.subheader("By law")
            law = active.assign(done=active["state"].isin(["Completed", "Completed late"]))
            law = law.groupby(["law", "done"]).size().reset_index(name="count")
            law["Result"] = law["done"].map({True: "Completed", False: "Pending"})
            chart = alt.Chart(law).mark_bar().encode(
                y=alt.Y("law:N", sort=E.LAWS, title=None), x=alt.X("count:Q", title="Compliances"),
                color=alt.Color("law:N", scale=alt.Scale(domain=list(LAW_COLORS), range=list(LAW_COLORS.values())), legend=None),
                opacity=alt.Opacity("Result:N", scale=alt.Scale(domain=["Completed", "Pending"], range=[1, 0.35]),
                                    legend=alt.Legend(title=None, orient="bottom")),
                tooltip=["law", "Result", "count"]).properties(height=260)
            st.altair_chart(chart, width="stretch")
        with c4:
            st.subheader("Completed, by month of completion")
            comp = df[df["completed_on"].notna()].copy()
            if comp.empty:
                st.info("Mark compliances as completed in the tracker to see completions by month.")
            else:
                comp["month"] = comp["completed_on"].map(lambda d: d.strftime("%b %Y"))
                comp["m"] = comp["completed_on"].map(lambda d: d.replace(day=1))
                cm = comp.groupby(["m", "month", "law"]).size().reset_index(name="count")
                chart = alt.Chart(cm).mark_bar().encode(
                    x=alt.X("month:N", sort=alt.EncodingSortField("m"), title=None), y=alt.Y("count:Q", title="Completed"),
                    color=alt.Color("law:N", scale=alt.Scale(domain=list(LAW_COLORS), range=list(LAW_COLORS.values())),
                                    legend=alt.Legend(title=None, orient="bottom", columns=2)),
                    tooltip=["month", "law", "count"]).properties(height=260)
                st.altair_chart(chart, width="stretch")
        l, r = st.columns(2, gap="large")
        show = ["due_date", "title", "regulation", "filing_mode"]
        ren = {"due_date": "Due", "title": "Requirement", "regulation": "Regulation", "filing_mode": "Mode of filing"}
        with l:
            st.subheader("🔴 Overdue")
            o = df[df["state"] == "Overdue"]
            st.dataframe(o[show].rename(columns=ren), hide_index=True, width="stretch", height=260) if len(o) else st.success("Nothing overdue.")
        with r:
            st.subheader("🟠 Next 14 days")
            s = df[df["state"] == "Due soon"]
            st.dataframe(s[show].rename(columns=ren), hide_index=True, width="stretch", height=260) if len(s) else st.caption("Nothing due in the next 14 days.")
        lr = E.long_range_items(catalog, profile, as_of)
        if lr:
            with st.expander("Longer-range dates"):
                for it in lr:
                    st.write(f"**{E.fmt(it['due'])}** – {it['title']}. {it['timeline']}")

# ------------------------------------------------------------------ tracker
with tab_track:
    a, b = st.columns([1, 2.2])
    with a:
        agm_now = profile["agm_date"]
        agm_in = st.date_input("AGM date for this year", value=agm_now if agm_now and E.fy_bounds(fy)[0] <= agm_now <= E.fy_bounds(fy)[1] else None,
                               key=f"agm_{cid}_{fy}", help="Unlocks AOC-4, MGT-7, annual report, voting results and other AGM-linked dates.")
        if agm_in and agm_in != agm_now:
            idx = ss.companies.index[ss.companies["company_id"] == cid][0]
            ss.companies.loc[idx, "agm_date"] = agm_in.isoformat()
            persist()
            st.rerun()
    with b:
        f1, f2, f3 = st.columns(3)
        f_law = f1.multiselect("Law", E.LAWS, placeholder="All laws")
        f_freq = f2.multiselect("Frequency", ["Quarterly", "Half-yearly", "Annual", "Triennial", "Event-based", "Continuous"], placeholder="All")
        month_opts = ["All months"] + [date(fy if m >= 4 else fy + 1, m, 1).strftime("%b %Y") for m in MONTHS_FY]
        f_month = f3.selectbox("Due month", month_opts)
    g1, g2 = st.columns([2, 1.4])
    f_view = g1.radio("Show", ["All", "To do", "Overdue", "Completed"], horizontal=True)
    f_text = g2.text_input("Search", placeholder="e.g. AOC-4, Reg 31, FC-GPR")

    v = df.copy()
    if f_law: v = v[v["law"].isin(f_law)]
    if f_freq: v = v[v["frequency"].isin(f_freq)]
    if f_month != "All months":
        v = v[v["due_date"].map(lambda d: bool(d) and d.strftime("%b %Y") == f_month)]
    if f_view == "To do": v = v[v["state"].isin(["Overdue", "Due soon", "Upcoming", "Date needed"])]
    elif f_view == "Overdue": v = v[v["state"] == "Overdue"]
    elif f_view == "Completed": v = v[v["state"].isin(["Completed", "Completed late"])]
    if f_text:
        needle = f_text.lower()
        v = v[v.apply(lambda r: needle in f"{r['title']} {r['regulation']} {r['filing_mode']}".lower(), axis=1)]

    st.caption(f"{len(v)} of {len(df)} compliances shown. Set **Status** and **Completed on**, add a note, then click Save.")
    if v.empty:
        st.info("No compliances match these filters.")
    else:
        view = pd.DataFrame({
            "task_id": v["task_id"], "State": v["state"].map(lambda s: f"{STATE_ICON[s]} {s}"),
            "Status": v["status"], "Completed on": pd.to_datetime(v["completed_on"]),
            "Due": pd.to_datetime(v["due_date"]), "Law": v["law"], "Requirement": v["title"],
            "Regulation": v["regulation"], "Type": v["nature"], "Frequency": v["frequency"],
            "Mode of filing": v["filing_mode"], "Period": v["period"], "Notes / evidence": v["note"],
        }).reset_index(drop=True)
        order = ["State", "Status", "Completed on", "Due", "Law", "Requirement", "Regulation", "Type", "Frequency",
                 "Mode of filing", "Period", "Notes / evidence"]
        edited = st.data_editor(
            view, hide_index=True, width="stretch", height=520, column_order=order, key=f"trk_{cid}_{fy}_{ss.tracker_ver}_{hash((tuple(f_law), tuple(f_freq), f_month, f_view, f_text))}",
            disabled=[c for c in order if c not in ("Status", "Completed on", "Notes / evidence")],
            column_config={
                "Status": st.column_config.SelectboxColumn("Status", options=E.STATUS_OPTIONS, required=True, width="small"),
                "Completed on": st.column_config.DateColumn("Completed on", format="DD MMM YYYY", width="small"),
                "Due": st.column_config.DateColumn("Due", format="DD MMM YYYY", width="small"),
                "Requirement": st.column_config.TextColumn(width="large"),
                "Mode of filing": st.column_config.TextColumn(width="large"),
                "Notes / evidence": st.column_config.TextColumn(width="medium"),
            })
        s1, s2, _ = st.columns([1, 1.4, 3])
        if s1.button("Save changes", type="primary"):
            stamp = datetime.now().isoformat(timespec="seconds")
            records = {(r["company_id"], r["task_id"]): r for r in ss.status.to_dict("records")}
            changed = 0
            for _, row in edited.iterrows():
                status = row["Status"] if row["Status"] in E.STATUS_OPTIONS else "Pending"
                done_on = E.parse_date(row["Completed on"])
                if status == "Completed" and not done_on:
                    done_on = as_of
                if status != "Completed":
                    done_on = None
                note = str(row["Notes / evidence"] or "")
                key = (cid, row["task_id"])
                old = records.get(key)
                if old is None and status == "Pending" and not note:
                    continue
                new = {"company_id": cid, "task_id": row["task_id"], "status": status,
                       "completion_date": done_on.isoformat() if done_on else "", "evidence_note": note, "updated_at": stamp}
                if old is None or any(old[c] != new[c] for c in ("status", "completion_date", "evidence_note")):
                    records[key] = new
                    changed += 1
            ss.status = pd.DataFrame(list(records.values()), columns=E.STATUS_COLUMNS).astype(str)
            persist()
            ss.tracker_ver += 1
            st.toast(f"Saved {changed} change(s).")
            st.rerun()
        s2.download_button("Download this view (CSV)", view.drop(columns="task_id").to_csv(index=False),
                           f"{cid}-{fy}-compliance.csv", "text/csv")

# ------------------------------------------------------------------ events
with tab_event:
    st.subheader("Event-based compliances")
    st.caption("Disclosures such as Board outcomes, allotments, director changes or insider trades are triggered by an "
               "event. Log the event date and the due date is calculated and added to your tracker.")
    ev_rules = applicable[applicable["deadline_rule"].str.startswith("event_")]
    if ev_rules.empty:
        st.info("No event-based compliances apply to this profile.")
    else:
        labels = {r["id"]: f"{r['law']} · {r['regulation']} · {r['title']}" for r in ev_rules.to_dict("records")}
        with st.form(f"event_form_{ss.form_ver}"):
            e1, e2 = st.columns([2.2, 1])
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
        mine = ev_rules.set_index("id")
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
                rows.append({"event_id": r["event_id"], "Event date": E.fmt(E.parse_date(r["event_date"])),
                             "Due": E.fmt(E.event_due(rr["deadline_rule"], E.parse_date(r["event_date"]))),
                             "Compliance": rr["title"], "Regulation": rr["regulation"], "Timeline": rr["timeline"], "Note": r["event_note"]})
            st.dataframe(pd.DataFrame(rows).drop(columns="event_id"), hide_index=True, width="stretch")
            d1, d2 = st.columns([2.2, 1])
            drop = d1.multiselect("Remove events", [x["event_id"] for x in rows],
                                  format_func=lambda i: next(f"{x['Event date']} – {x['Compliance']}" for x in rows if x["event_id"] == i))
            if d2.button("Remove selected") and drop:
                ss.events = ss.events[~ss.events["event_id"].isin(drop)].reset_index(drop=True)
                persist()
                st.rerun()
        with st.expander("All event-based compliances for this entity"):
            st.dataframe(ev_rules[["law", "regulation", "title", "timeline", "filing_mode"]].rename(
                columns={"law": "Law", "regulation": "Regulation", "title": "Requirement", "timeline": "Timeline", "filing_mode": "Mode of filing"}),
                hide_index=True, width="stretch")

# ------------------------------------------------------------------ guide
with tab_guide:
    st.subheader("Applicable requirements")
    st.caption(f"{len(applicable)} requirements apply to this entity profile across {applicable['law'].nunique()} laws. "
               "Each row shows the regulation number, timeline and mode of filing.")
    g_law = st.multiselect("Law ", E.LAWS, placeholder="All laws", key="guide_law")
    gv = applicable if not g_law else applicable[applicable["law"].isin(g_law)]
    matrix = gv[["law", "frequency", "nature", "title", "regulation", "timeline", "filing_mode", "verification"]].rename(columns={
        "law": "Law", "frequency": "Frequency", "nature": "Type", "title": "Requirement", "regulation": "Regulation",
        "timeline": "Timeline", "filing_mode": "Mode of filing", "verification": "Verification"})
    st.dataframe(matrix, hide_index=True, width="stretch", height=380)
    st.download_button("Download applicability matrix (CSV)", matrix.to_csv(index=False), f"{cid}-applicability-matrix.csv", "text/csv")
    if len(gv):
        pick = st.selectbox("Open a requirement", gv["id"].tolist(), format_func=lambda i: gv.set_index("id").loc[i, "title"])
        r = gv.set_index("id").loc[pick]
        x, y = st.columns(2, gap="large")
        x.markdown(f"**Law** – {r['law']}\n\n**Regulation** – {r['regulation']}\n\n**Type** – {r['nature']} · {r['frequency']}\n\n**Timeline** – {r['timeline']}")
        y.markdown(f"**Mode of filing** – {r['filing_mode']}\n\n**Verification** – {r['verification']}\n\n**Source** – [{r['source_url']}]({r['source_url']})")
        if r["note"]:
            st.info(r["note"])
    with st.expander("What this catalogue is based on, and what to verify"):
        st.markdown(
            "Built from the *Entity-wise Regulatory Compliance Matrix (India)* prepared 24 October 2026. It is a planning "
            "tool, not legal advice.\n\n"
            "- **Confirmed in the matrix:** small-company thresholds (1 Dec 2025), HVDLE at ₹5,000 cr, LODR market-cap "
            "tiers, SME exemptions and Reg 23, PIT 2025 amendment and PAN-freeze circular, FLA, FC-GPR, FC-TRS, APR, "
            "triennial DIR-3 KYC, and the 30-day Integrated Filing (Governance) timeline.\n"
            "- **Check current text:** section-level size thresholds, PAS-6 timing, ECB reporting, trading-plan periods, "
            "SAST open-offer mechanics, Reg 3(2) stickiness, IGP relaxations, Reg 17(1B), and the 2026 ECB/Guarantee regulations.\n"
            "- Working-day calculations skip weekends only, not exchange or bank holidays.\n"
            "- Rules live in `data/regulatory_catalog.csv`; edit that file to update a rule.")

# ------------------------------------------------------------------ profile
with tab_profile:
    st.subheader("Company profile")
    st.caption("Pick the entity type and answer the questions; the compliance list updates automatically.")
    left, right = st.columns([1.5, 1], gap="large")
    with left:
        st.markdown(f"#### Edit: {profile['company_name']}")
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
        st.markdown("#### Add a company")
        with st.container(border=True):
            n_name = st.text_input("Company name *", key=f"new_name_{ss.form_ver}")
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
    st.markdown("#### All companies")
    reg = ss.companies[["company_name", "symbol", "entity_type", "market_cap_tier", "agm_date", "profile_owner", "profile_last_reviewed"]].copy()
    reg["entity_type"] = reg["entity_type"].map(E.ENTITY_LABELS)
    st.dataframe(reg.rename(columns={"company_name": "Company", "symbol": "Symbol", "entity_type": "Entity type", "market_cap_tier": "Tier",
                                     "agm_date": "AGM date", "profile_owner": "Owner", "profile_last_reviewed": "Last reviewed"}),
                 hide_index=True, width="stretch")

st.divider()
st.caption("Planning prototype based on the Entity-wise Regulatory Compliance Matrix (24 Oct 2026). Not legal advice – verify every "
           "requirement against the current Act, Rules, Regulations and circulars before filing.")

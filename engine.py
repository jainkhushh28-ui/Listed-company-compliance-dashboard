"""Rules engine for Listing Compliance Compass.

Turns a company profile + the regulatory catalogue into a dated task list.
Kept free of Streamlit so it can be tested on its own.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

# ---------------------------------------------------------------- vocabulary
ENTITY_LABELS = {
    "PVT": "Private company (unlisted)",
    "UPUB": "Unlisted public company",
    "MB": "Listed – Main Board (equity)",
    "SME": "Listed – SME platform (NSE Emerge / BSE SME)",
    "IGP": "Listed – Innovators Growth Platform (IGP)",
    "DEBT": "Listed – Debt securities only",
}
EQUITY_LISTED = {"MB", "SME", "IGP"}
LISTED = {"MB", "SME", "IGP", "DEBT"}

TIER_OPTIONS = ["Outside Top 2000", "Top 2000", "Top 1000", "Top 500", "Top 250", "Top 100"]
TIER_N = {"Top 100": 100, "Top 250": 250, "Top 500": 500, "Top 1000": 1000, "Top 2000": 2000}
NO_TIER = 10**9

LAWS = ["Companies Act, 2013", "SEBI LODR", "SEBI PIT", "SEBI SAST", "FEMA"]
STATUS_OPTIONS = ["Pending", "Completed", "Not applicable"]

# key, label, help, entity types where the question is shown (None = all)
FACTS = [
    ("small_company", "Small company (paid-up ≤ ₹10 cr and turnover ≤ ₹100 cr)",
     "Both limits must be met. Holding companies, subsidiaries, Section 8 and special-Act companies are excluded.", {"PVT"}),
    ("hvdle", "High Value Debt Listed Entity (listed NCDs of ₹5,000 cr or more)",
     "Adds governance chapters through Reg 15(1A).", "debt"),
    ("promoter_group", "Has a promoter / promoter group",
     "Drives SAST Reg 30(2) and Reg 31 disclosures.", "equity"),
    ("holds_25pct", "A shareholder holds 25% or more",
     "Drives SAST Reg 30(1) annual disclosure.", "equity"),
    ("sast_acquirer", "Holds or may acquire shares of a listed company",
     "Brings in SAST acquirer disclosures even if this entity is not listed.", None),
    ("has_issue_proceeds", "Has unutilised public / rights / preferential issue proceeds",
     "Drives Reg 32 deviation statement.", "equity"),
    ("has_subsidiaries", "Has subsidiaries, associates or JVs", "Consolidation and AOC-1 (s.129(3)).", None),
    ("has_foreign_investment", "Has foreign investment (FDI / FPI / NRI shareholding)",
     "Drives FC-GPR, FC-TRS, Form DI and the FLA return.", None),
    ("has_overseas_investment", "Invests abroad (ODI / overseas portfolio)", "Drives Form FC, APR and FLA.", None),
    ("has_ecb", "Has external commercial borrowings", "Drives ECB reporting.", None),
    ("msme_dues", "Owes MSME suppliers beyond 45 days", "Drives MSME-1.", None),
    ("csr_applicable", "CSR applies (net worth ≥ ₹500 cr, turnover ≥ ₹1,000 cr or profit ≥ ₹5 cr)",
     "Drives CSR-2 and s.135 requirements.", None),
    ("declares_dividend", "Declares dividend", "Drives IEPF transfers.", None),
    ("holders_1000", "More than 1,000 security holders", "Stakeholders Relationship Committee (s.178(5)).", None),
    ("cost_audit", "Cost audit applies (notified sector above turnover limit)", "Drives CRA forms.", None),
    ("mgt8_threshold", "Paid-up capital ≥ ₹10 cr or turnover ≥ ₹50 cr",
     "Brings in MGT-8 for unlisted companies.", {"PVT", "UPUB"}),
    ("secretarial_audit_applicable", "Secretarial audit applies (s.204)",
     "Larger public companies and companies with large bank borrowings.", {"PVT", "UPUB"}),
    ("size_thresholds", "Meets Companies Act size thresholds for committees, KMP, internal audit, auditor rotation",
     "See s.149, 177, 178, 203, 138, 139.", {"PVT", "UPUB"}),
]
FACT_KEYS = [f[0] for f in FACTS]

COMPANY_COLUMNS = [
    "company_id", "company_name", "symbol", "entity_type", "also_debt_listed", "market_cap_tier",
    *FACT_KEYS, "agm_date", "profile_owner", "profile_last_reviewed", "notes",
]
STATUS_COLUMNS = ["company_id", "task_id", "status", "completion_date", "evidence_note", "updated_at"]
EVENT_COLUMNS = ["event_id", "company_id", "rule_id", "event_date", "event_note"]
BOOL_COLUMNS = ["also_debt_listed", *FACT_KEYS]


def fact_visible(show, entity_type: str, also_debt: bool) -> bool:
    if show is None:
        return True
    if show == "equity":
        return entity_type in EQUITY_LISTED
    if show == "debt":
        return entity_type == "DEBT" or (entity_type in EQUITY_LISTED and also_debt)
    return entity_type in show


# ---------------------------------------------------------------- helpers
def as_bool(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def parse_date(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"nat", "nan", "none"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def add_working_days(start: date, n: int) -> date:
    current, added = start, 0
    while added < n:
        current += timedelta(days=1)
        if current.weekday() < 5:
            added += 1
    return current


def sub_working_days(start: date, n: int) -> date:
    current, removed = start, 0
    while removed < n:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            removed += 1
    return current


def fy_start_year(d: date) -> int:
    return d.year if d.month >= 4 else d.year - 1


def fy_label(start_year: int) -> str:
    return f"FY {start_year}-{str(start_year + 1)[-2:]}"


def fy_bounds(fy: int) -> tuple[date, date]:
    return date(fy, 4, 1), date(fy + 1, 3, 31)


def fmt(d: date | None) -> str:
    return d.strftime("%d %b %Y") if d else "—"


# ---------------------------------------------------------------- profile
def company_profile(row: dict) -> dict:
    profile = {
        "company_id": row.get("company_id", ""),
        "company_name": row.get("company_name", ""),
        "entity_type": row.get("entity_type") or "MB",
        "market_cap_tier": row.get("market_cap_tier") or "Outside Top 2000",
        "agm_date": parse_date(row.get("agm_date")),
    }
    for key in BOOL_COLUMNS:
        profile[key] = as_bool(row.get(key, False))
    return profile


def derive_flags(p: dict) -> dict:
    et = p["entity_type"]
    equity = et in EQUITY_LISTED
    listed = et in LISTED
    debt_listed = et == "DEBT" or (equity and p["also_debt_listed"])
    small = et == "PVT" and p["small_company"]
    tier_n = TIER_N.get(p["market_cap_tier"], NO_TIER) if et == "MB" else NO_TIER
    flags = {k: bool(p.get(k, False)) for k in FACT_KEYS}
    flags.update(
        listed=listed,
        equity_listed=equity,
        debt_listed=debt_listed,
        hvdle=debt_listed and p["hvdle"],
        small=small,
        not_small=not small,
        mgt8_cond=listed or (p["mgt8_threshold"] and not small),
        sec_audit=listed or p["secretarial_audit_applicable"],
        gov=listed or p["size_thresholds"],
        sast_scope=equity or p["sast_acquirer"],
        fdi=p["has_foreign_investment"],
        odi=p["has_overseas_investment"],
        ecb=p["has_ecb"],
        fdi_or_odi=p["has_foreign_investment"] or p["has_overseas_investment"],
        tier_n=tier_n,
    )
    return flags


def rule_applies(rule: dict, flags: dict, entity_type: str) -> bool:
    entities = [e for e in str(rule.get("entities", "")).split(";") if e]
    if entities and entity_type not in entities:
        return False
    for token in [t for t in str(rule.get("requires", "")).split(";") if t]:
        negate = token.startswith("!")
        value = bool(flags.get(token.lstrip("!"), False))
        if value == negate:
            return False
    min_tier = str(rule.get("min_tier", "")).strip()
    if min_tier and flags["tier_n"] > int(min_tier):
        return False
    return True


def applicable_rules(catalog: pd.DataFrame, profile: dict) -> pd.DataFrame:
    flags = derive_flags(profile)
    mask = [rule_applies(r, flags, profile["entity_type"]) for r in catalog.to_dict("records")]
    return catalog[mask].reset_index(drop=True)


# ---------------------------------------------------------------- scheduling
def _quarter_label(pe: date) -> str:
    q = {6: 1, 9: 2, 12: 3, 3: 4}[pe.month]
    start = pe.year - 1 if pe.month <= 3 else pe.year
    return f"Q{q} {fy_label(start)} (ended {fmt(pe)})"


def _half_label(pe: date) -> str:
    start = pe.year - 1 if pe.month <= 3 else pe.year
    return f"{'H1' if pe.month == 9 else 'H2'} {fy_label(start)} (ended {fmt(pe)})"


def _period_ends(fy: int, months: tuple[int, ...]) -> list[date]:
    last_day = {3: 31, 6: 30, 9: 30, 12: 31}
    ends = [date(y, m, last_day[m]) for y in (fy - 1, fy, fy + 1) for m in months]
    return sorted(ends)


def event_due(rule_code: str, event_date: date) -> date:
    kind, _, arg = rule_code.partition(":")
    n = int(arg) if arg else 0
    if kind == "event_days":
        return event_date + timedelta(days=n)
    if kind == "event_wd":
        return add_working_days(event_date, n)
    if kind == "event_before_wd":
        return sub_working_days(event_date, n)
    return event_date  # event_manual


def parse_extension(text) -> tuple[int, date, str] | None:
    """'2026|2026-12-31|reference' -> (fy, extended date, reference)."""
    parts = str(text or "").split("|")
    if len(parts) != 3:
        return None
    try:
        return int(parts[0]), date.fromisoformat(parts[1]), parts[2]
    except ValueError:
        return None


def _base(rule: dict) -> dict:
    return {"remark": "",
        "rule_id": rule["id"], "law": rule["law"], "frequency": rule["frequency"], "nature": rule["nature"],
        "title": rule["title"], "regulation": rule["regulation"], "timeline": rule["timeline"],
        "filing_mode": rule["filing_mode"], "note_rule": rule.get("note", ""),
        "verification": rule["verification"], "source_url": rule["source_url"],
        "is_event": False, "event_id": "",
    }


def generate_tasks(catalog: pd.DataFrame, profile: dict, fy: int, events: list[dict]) -> list[dict]:
    flags = derive_flags(profile)
    lo, hi = fy_bounds(fy)
    agm = profile["agm_date"]
    tasks: list[dict] = []
    rules_by_id = {r["id"]: r for r in catalog.to_dict("records")}

    for rule in rules_by_id.values():
        if not rule_applies(rule, flags, profile["entity_type"]):
            continue
        code = rule["deadline_rule"]
        kind, _, arg = code.partition(":")
        from_fy = str(rule.get("from_fy", "")).strip()
        base = _base(rule)

        def add(task_id: str, due: date | None, period: str, remark: str = "") -> None:
            tasks.append({**base, "task_id": f"{rule['id']}|{task_id}", "due_date": due, "period": period, "remark": remark})

        if kind in {"q", "qres", "tw"}:
            for pe in _period_ends(fy, (3, 6, 9, 12)):
                if kind == "q":
                    due = pe + timedelta(days=int(arg))
                elif kind == "qres":
                    due = pe + timedelta(days=60 if pe.month == 3 else 45)
                else:
                    due = sub_working_days(pe, 2)
                if lo <= due <= hi:
                    add(pe.isoformat(), due, _quarter_label(pe))
        elif kind in {"half_45_60", "half_60"}:
            for pe in _period_ends(fy, (3, 9)):
                days = 60 if (kind == "half_60" or pe.month == 3) else 45
                due = pe + timedelta(days=days)
                if lo <= due <= hi:
                    add(pe.isoformat(), due, _half_label(pe))
        elif kind in {"fyend", "fyend_wd"}:
            for pe in _period_ends(fy, (3,)):
                due = pe + timedelta(days=int(arg)) if kind == "fyend" else add_working_days(pe, int(arg))
                if lo <= due <= hi:
                    add(pe.isoformat(), due, f"For {fy_label(pe.year - 1)} (ended {fmt(pe)})")
        elif kind == "fixed_md":
            month, day = (int(x) for x in arg.split("-"))
            due = date(fy if month >= 4 else fy + 1, month, day)
            period = f"Confirm for {fy_label(fy)}" if rule["frequency"] == "Continuous" else f"Due in {fy_label(fy)}"
            add(due.isoformat(), due, period)
        elif kind == "fixed_date":
            due = date.fromisoformat(arg)
            if lo <= due <= hi:
                add(due.isoformat(), due, "Triennial cycle")
        elif kind in {"agm", "agm_wd"}:
            if from_fy and fy - 1 < int(from_fy):
                continue
            n = int(arg)
            ext = parse_extension(rule.get("extension"))
            ext = ext if ext and ext[0] == fy else None
            if agm and lo <= agm <= hi:
                due = add_working_days(agm, n) if kind == "agm_wd" else agm + timedelta(days=n)
                remark = ""
                if ext and due < ext[1]:
                    remark = f"Extended to {fmt(ext[1])} without additional fees ({ext[2]}). Statutory date: {fmt(due)}."
                    due = ext[1]
                add(f"AGM-FY{fy}", due, f"AGM held {fmt(agm)}", remark)
            elif ext:
                add(f"AGM-FY{fy}", ext[1], "AGM date not set – extended date shown",
                    f"Extended to {fmt(ext[1])} without additional fees ({ext[2]}).")
            else:
                add(f"AGM-FY{fy}", None, "AGM date needed")
        # event_* rules are created from the event log below

    for ev in events:
        rule = rules_by_id.get(ev["rule_id"])
        ev_date = parse_date(ev["event_date"])
        if not rule or not ev_date:
            continue
        due = event_due(rule["deadline_rule"], ev_date)
        if not (lo <= due <= hi or lo <= ev_date <= hi):
            continue
        base = _base(rule)
        label = f"Event on {fmt(ev_date)}" + (f" – {ev['event_note']}" if str(ev.get("event_note", "")).strip() else "")
        tasks.append({**base, "task_id": f"event|{ev['event_id']}", "due_date": due, "period": label,
                      "is_event": True, "event_id": ev["event_id"]})
    return tasks


def long_range_items(catalog: pd.DataFrame, profile: dict, after: date) -> list[dict]:
    flags = derive_flags(profile)
    items = []
    for rule in catalog.to_dict("records"):
        if rule["deadline_rule"].startswith("fixed_date:") and rule_applies(rule, flags, profile["entity_type"]):
            due = date.fromisoformat(rule["deadline_rule"].split(":")[1])
            if due >= after:
                items.append({"title": rule["title"], "due": due, "timeline": rule["timeline"]})
    return items


# ---------------------------------------------------------------- status
STATE_ORDER = ["Overdue", "Due soon", "Upcoming", "Date needed", "Completed", "Completed late", "Not applicable"]


def attach_status(tasks: list[dict], status: pd.DataFrame, company_id: str, as_of: date) -> pd.DataFrame:
    columns = ["task_id", "rule_id", "law", "frequency", "nature", "title", "regulation", "timeline", "filing_mode",
               "period", "due_date", "verification", "source_url", "note_rule", "remark", "is_event", "event_id",
               "status", "completed_on", "note", "state"]
    if not tasks:
        return pd.DataFrame(columns=columns)
    lookup = {}
    if status is not None and len(status):
        for rec in status[status["company_id"] == company_id].to_dict("records"):
            lookup[rec["task_id"]] = rec
    rows = []
    for t in tasks:
        rec = lookup.get(t["task_id"], {})
        st_value = rec.get("status") if rec.get("status") in STATUS_OPTIONS else "Pending"
        completed = parse_date(rec.get("completion_date")) if st_value == "Completed" else None
        due = t["due_date"]
        if st_value == "Not applicable":
            state = "Not applicable"
        elif st_value == "Completed":
            state = "Completed late" if (due and completed and completed > due) else "Completed"
        elif due is None:
            state = "Date needed"
        elif due < as_of:
            state = "Overdue"
        elif (due - as_of).days <= 14:
            state = "Due soon"
        else:
            state = "Upcoming"
        rows.append({**t, "status": st_value, "completed_on": completed, "note": rec.get("evidence_note", ""), "state": state})
    df = pd.DataFrame(rows)
    df["_sort"] = df["due_date"].map(lambda d: d if d else date(2999, 1, 1))
    df = df.sort_values(["_sort", "law", "title"]).drop(columns="_sort").reset_index(drop=True)
    return df[columns]


def update_status(status: pd.DataFrame, company_id: str, task_ids: list[str], new_status: str,
                  completed_on: date | None, note: str, stamp: str) -> pd.DataFrame:
    """Set the status of several tasks at once and return the new status table."""
    records = {(r["company_id"], r["task_id"]): r for r in status.to_dict("records")}
    for tid in task_ids:
        old = records.get((company_id, tid), {})
        records[(company_id, tid)] = {
            "company_id": company_id, "task_id": tid, "status": new_status,
            "completion_date": completed_on.isoformat() if (new_status == "Completed" and completed_on) else "",
            "evidence_note": note.strip() if note and note.strip() else old.get("evidence_note", ""),
            "updated_at": stamp,
        }
    return pd.DataFrame(list(records.values()), columns=STATUS_COLUMNS).astype(str)

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import re

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="Listing Compliance Compass",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

CATALOG_PATH = Path(__file__).parent / "data" / "compliance_catalog.csv"
COMPANY_PATH = Path(__file__).parent / "data" / "companies.csv"
SOURCE_URL = "https://www.nseindia.com/static/companies-listing/compliance-information-compliance-calendar-main-board"
LISTING_CATEGORIES = ["Main Board", "Emerge / SME", "IGP", "Debt", "Other / review required"]
BOOLEAN_COLUMNS = ["top_1000", "large_entity", "promoter_sast"]
COMPANY_COLUMNS = [
    "company_id",
    "company_name",
    "nse_symbol",
    "listing_category",
    "top_1000",
    "large_entity",
    "promoter_sast",
    "profile_last_reviewed",
    "profile_owner",
    "notes",
]


def as_boolean(value: object) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def profile_from_company(company: pd.Series) -> dict[str, bool]:
    is_main_board = company["listing_category"] == "Main Board"
    return {
        "main_board": is_main_board,
        "top_1000": is_main_board and bool(company["top_1000"]),
        "large_entity": is_main_board and bool(company["large_entity"]),
        "sast_promoter": is_main_board and bool(company["promoter_sast"]),
    }


def create_company_id(company_name: str, existing_ids: set[str]) -> str:
    base_id = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-") or "company"
    company_id = base_id
    number = 2
    while company_id in existing_ids:
        company_id = f"{base_id}-{number}"
        number += 1
    return company_id


def add_working_days(start_date: date, number_of_days: int) -> date:
    current_date = start_date
    added_days = 0
    while added_days < number_of_days:
        current_date += timedelta(days=1)
        if current_date.weekday() < 5:
            added_days += 1
    return current_date


def financial_year_start(reference_date: date) -> int:
    return reference_date.year if reference_date.month >= 4 else reference_date.year - 1


def fiscal_year_label(start_year: int) -> str:
    return f"FY {start_year}–{str(start_year + 1)[-2:]}"


def format_due_date(due_date: date) -> str:
    return due_date.strftime("%d %b %Y")


def determine_status(due_date: date, reference_date: date) -> str:
    days_to_due = (due_date - reference_date).days
    if days_to_due < 0:
        return "Passed"
    if days_to_due == 0:
        return "Due today"
    if days_to_due <= 14:
        return "Due soon"
    return "Planned"


def calculate_due_date(rule: str, period_end: date) -> date | None:
    if rule == "quarterly_30":
        return period_end + timedelta(days=30)
    if rule == "quarterly_21":
        return period_end + timedelta(days=21)
    if rule == "financial_results":
        days = 60 if period_end.month == 3 else 45
        return period_end + timedelta(days=days)
    if rule == "annual_60":
        return period_end + timedelta(days=60)
    if rule == "fy_start_30":
        return date(period_end.year, 4, 1) + timedelta(days=29)
    if rule == "fy_end_45":
        return period_end + timedelta(days=45)
    if rule == "annual_7_working":
        return add_working_days(period_end, 7)
    return None


def schedule_for_catalog(catalog: pd.DataFrame, reference_date: date) -> pd.DataFrame:
    scheduled_rows: list[dict[str, object]] = []
    current_financial_year = financial_year_start(reference_date)

    for _, compliance in catalog.iterrows():
        rule = compliance["deadline_rule"]
        if rule in {"with_financial_results", "with_annual_report"}:
            continue

        if compliance["cadence"] == "Quarterly":
            for year in range(current_financial_year - 1, current_financial_year + 2):
                period_ends = [
                    (date(year, 6, 30), "Quarter ended June"),
                    (date(year, 9, 30), "Quarter ended September"),
                    (date(year, 12, 31), "Quarter ended December"),
                    (date(year + 1, 3, 31), "Quarter ended March"),
                ]
                for period_end, period_label in period_ends:
                    due_date = calculate_due_date(rule, period_end)
                    if due_date:
                        scheduled_rows.append(
                            {
                                **compliance.to_dict(),
                                "period": f"{period_label} {period_end.year}",
                                "due_date": due_date,
                            }
                        )
        elif rule in {"annual_60", "fy_start_30", "fy_end_45", "annual_7_working"}:
            for year in range(current_financial_year - 1, current_financial_year + 2):
                period_end = date(year + 1, 3, 31)
                due_date = calculate_due_date(rule, period_end)
                if due_date:
                    scheduled_rows.append(
                        {
                            **compliance.to_dict(),
                            "period": fiscal_year_label(year),
                            "due_date": due_date,
                        }
                    )

    schedule = pd.DataFrame(scheduled_rows)
    if schedule.empty:
        columns = [*catalog.columns, "period", "due_date", "status"]
        return pd.DataFrame(
            {
                column: pd.Series(dtype="datetime64[ns]" if column == "due_date" else "object")
                for column in columns
            }
        )

    schedule["due_date"] = pd.to_datetime(schedule["due_date"])
    schedule = schedule.sort_values(["due_date", "title"]).reset_index(drop=True)
    schedule["status"] = schedule["due_date"].dt.date.map(
        lambda due_date: determine_status(due_date, reference_date)
    )
    return schedule


@st.cache_data
def load_catalog() -> pd.DataFrame:
    return pd.read_csv(CATALOG_PATH)


@st.cache_data
def load_companies() -> pd.DataFrame:
    companies = pd.read_csv(COMPANY_PATH, dtype=str).fillna("")
    for column in BOOLEAN_COLUMNS:
        companies[column] = companies[column].map(as_boolean)
    return companies[COMPANY_COLUMNS]


def save_companies(companies: pd.DataFrame) -> None:
    cleaned = companies[COMPANY_COLUMNS].copy().fillna("")
    cleaned["company_name"] = cleaned["company_name"].astype(str).str.strip()
    cleaned["nse_symbol"] = cleaned["nse_symbol"].astype(str).str.upper().str.strip()
    cleaned["listing_category"] = cleaned["listing_category"].astype(str).str.strip()
    cleaned["profile_owner"] = cleaned["profile_owner"].astype(str).str.strip()
    cleaned["notes"] = cleaned["notes"].astype(str).str.strip()
    cleaned["profile_last_reviewed"] = cleaned["profile_last_reviewed"].astype(str).str.strip()
    for column in BOOLEAN_COLUMNS:
        cleaned[column] = cleaned[column].map(as_boolean)

    if cleaned["company_name"].eq("").any():
        raise ValueError("Every company needs a name.")
    if cleaned["company_name"].str.casefold().duplicated().any():
        raise ValueError("Company names must be unique.")
    if not cleaned["listing_category"].isin(LISTING_CATEGORIES).all():
        raise ValueError("Every company needs one of the supported listing categories.")

    cleaned.to_csv(COMPANY_PATH, index=False)
    load_companies.clear()


def requirement_table(frame: pd.DataFrame, reference_date: date) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame()

    view = frame.copy()
    view["Due date"] = view["due_date"].dt.date.map(format_due_date)
    view["Days"] = (view["due_date"].dt.date - reference_date).map(lambda value: value.days)
    view = view.rename(
        columns={
            "title": "Requirement",
            "regulation": "Regulation",
            "cadence": "Cadence",
            "period": "Period",
            "portal": "Filing destination",
            "status": "Status",
        }
    )
    return view[
        [
            "Status",
            "Due date",
            "Days",
            "Requirement",
            "Regulation",
            "Cadence",
            "Period",
            "Filing destination",
        ]
    ]


catalog = load_catalog()
companies = load_companies()

if "pending_company_id" in st.session_state:
    st.session_state.selected_company_id = st.session_state.pop("pending_company_id")
if "selected_company_id" not in st.session_state or st.session_state.selected_company_id not in set(companies["company_id"]):
    st.session_state.selected_company_id = companies.iloc[0]["company_id"]

st.markdown(
    """
    <style>
      .block-container { padding-top: 5.25rem; padding-bottom: 3rem; max-width: 1500px; }
      [data-testid="stSidebar"] { background: var(--secondary-background-color); }
      .eyebrow { color: var(--text-color); opacity: 0.72; font-size: 0.85rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; }
      .hero-title { color: var(--text-color); font-size: 2.15rem; font-weight: 700; letter-spacing: -0.035em; margin-bottom: 0.2rem; }
      .hero-copy { color: var(--text-color); opacity: 0.74; font-size: 1rem; margin-bottom: 1.4rem; }
      div[data-testid="stMetric"] { background: var(--secondary-background-color); border: 1px solid color-mix(in srgb, var(--text-color) 16%, transparent); border-radius: 14px; padding: 0.75rem 1rem; }
      div[data-testid="stMetricValue"] { color: var(--text-color); }
      .notice { background: color-mix(in srgb, var(--primary-color) 13%, transparent); border: 1px solid color-mix(in srgb, var(--primary-color) 42%, transparent); border-radius: 10px; padding: 0.8rem 1rem; color: var(--text-color); font-size: 0.9rem; }
      @media (max-width: 760px) { .block-container { padding-top: 4.25rem; padding-left: 1rem; padding-right: 1rem; } .hero-title { font-size: 1.75rem; } }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown("### Compliance Compass")
    st.caption("Company-aware Main Board compliance workspace")
    st.divider()
    selected_company_id = st.selectbox(
        "Selected company",
        options=companies["company_id"].tolist(),
        format_func=lambda company_id: (
            f"{companies.loc[companies['company_id'] == company_id, 'company_name'].iloc[0]}"
            f" ({companies.loc[companies['company_id'] == company_id, 'nse_symbol'].iloc[0] or 'No symbol'})"
        ),
        key="selected_company_id",
    )
    selected_date = st.date_input("Dashboard date", value=date.today())
    st.caption("The selected date controls deadlines, status and monthly view.")
    st.divider()
    selected_company = companies[companies["company_id"] == selected_company_id].iloc[0]
    profile = profile_from_company(selected_company)
    st.markdown("#### Applicability profile")
    st.caption(f"Category: {selected_company['listing_category']}")
    st.caption(f"Top 1,000: {'Yes' if selected_company['top_1000'] else 'No'}")
    st.caption(f"Large entity: {'Yes' if selected_company['large_entity'] else 'No'}")
    st.caption(f"Promoter SAST workflow: {'Yes' if selected_company['promoter_sast'] else 'No'}")
    if not profile["main_board"]:
        st.warning("The active catalogue is for Main Board equity issuers. This company needs its own category-specific rule set.")
    st.divider()
    st.caption("Add or update companies in the Company register tab.")

applicable_catalog = catalog[catalog["flag"].map(lambda flag: profile.get(flag, False))].copy()
calendar_catalog = applicable_catalog[
    applicable_catalog["cadence"].isin(["Quarterly", "Annual"])
].copy()
schedule = schedule_for_catalog(calendar_catalog, selected_date)
current_fy = financial_year_start(selected_date)

st.markdown('<div class="eyebrow">Listed company compliance workspace</div>', unsafe_allow_html=True)
st.markdown('<div class="hero-title">Know what is due, where to file, and why.</div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="hero-copy">{selected_company["company_name"]} · {selected_company["listing_category"]} · {fiscal_year_label(current_fy)} · Based on {selected_date.strftime("%d %B %Y")}</div>',
    unsafe_allow_html=True,
)

month_schedule = schedule[
    (schedule["due_date"].dt.year == selected_date.year)
    & (schedule["due_date"].dt.month == selected_date.month)
].copy()
upcoming_schedule = schedule[schedule["due_date"].dt.date >= selected_date].copy()
next_item = upcoming_schedule.iloc[0] if not upcoming_schedule.empty else None
due_soon = schedule[
    (schedule["due_date"].dt.date >= selected_date)
    & (schedule["due_date"].dt.date <= selected_date + timedelta(days=14))
]

metric_one, metric_two, metric_three, metric_four = st.columns(4)
metric_one.metric("Due this month", len(month_schedule))
metric_two.metric("Due in 14 days", len(due_soon))
metric_three.metric("Next deadline", format_due_date(next_item["due_date"].date()) if next_item is not None else "—")
metric_four.metric("Event-driven checks", len(applicable_catalog[applicable_catalog["cadence"].isin(["Event-based", "Corporate action"])]))

st.markdown(
    '<div class="notice">Use this dashboard for planning, not as legal advice. Filing tiles, formats, applicability and deadlines must be checked against the latest NSE, SEBI and company-specific requirements before submission.</div>',
    unsafe_allow_html=True,
)

dashboard_tab, schedule_tab, event_tab, guide_tab, company_tab = st.tabs(
    ["Dashboard", "Full schedule", "Event-driven", "Filing guide", "Company register"]
)

with dashboard_tab:
    left_column, right_column = st.columns([1.15, 0.85], gap="large")
    with left_column:
        st.subheader(f"Due in {selected_date.strftime('%B %Y')}")
        if month_schedule.empty:
            st.info("No calculated periodic deadlines fall in this month for the selected profile.")
        else:
            st.dataframe(requirement_table(month_schedule, selected_date), hide_index=True, width="stretch")
    with right_column:
        st.subheader("Periodic workload")
        fy_schedule = schedule[
            (schedule["due_date"].dt.date >= date(current_fy, 4, 1))
            & (schedule["due_date"].dt.date <= date(current_fy + 1, 3, 31) + timedelta(days=70))
        ]
        workload = (
            fy_schedule.assign(month=fy_schedule["due_date"].dt.strftime("%b"))
            .groupby("month", sort=False)
            .size()
            .reindex(["Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec", "Jan", "Feb", "Mar"], fill_value=0)
        )
        st.bar_chart(workload, color="#0b6e69", height=260)
        if next_item is not None:
            st.caption(f"Next: {next_item['title']} — {next_item['regulation']}")

    st.subheader("Requirements needing a date anchor")
    anchored = applicable_catalog[
        applicable_catalog["deadline_rule"].isin(["with_financial_results", "with_annual_report", "manual_annual_report"])
    ]
    st.caption("These cannot be calculated until the linked financial-results or annual-report date is known.")
    st.dataframe(
        anchored.rename(
            columns={
                "title": "Requirement",
                "timeline": "Timing rule",
                "regulation": "Regulation",
                "portal": "Filing destination",
            }
        )[["Requirement", "Regulation", "Timing rule", "Filing destination"]],
        hide_index=True,
        width="stretch",
    )

with schedule_tab:
    st.subheader("Calculated periodic schedule")
    st.caption("Shows rolling quarterly and annual deadlines. Select a requirement below to inspect its filing details.")
    selected_cadence = st.multiselect(
        "Cadence", options=["Quarterly", "Annual"], default=["Quarterly", "Annual"], key="cadence_filter"
    )
    selected_status = st.multiselect(
        "Status", options=["Passed", "Due today", "Due soon", "Planned"], default=["Due today", "Due soon", "Planned"], key="status_filter"
    )
    filtered_schedule = schedule[
        schedule["cadence"].isin(selected_cadence) & schedule["status"].isin(selected_status)
    ]
    st.dataframe(requirement_table(filtered_schedule, selected_date), hide_index=True, width="stretch", height=440)
    st.download_button(
        "Download filtered schedule (CSV)",
        data=requirement_table(filtered_schedule, selected_date).to_csv(index=False),
        file_name="listing-compliance-schedule.csv",
        mime="text/csv",
    )

with event_tab:
    st.subheader("Event-driven and corporate-action requirements")
    st.caption("These obligations need a transaction, meeting or event date before a filing deadline can be calculated.")
    event_requirements = applicable_catalog[
        applicable_catalog["cadence"].isin(["Event-based", "Corporate action"])
    ].copy()
    st.dataframe(
        event_requirements.rename(
            columns={
                "title": "Requirement",
                "regulation": "Regulation",
                "timeline": "Timing rule",
                "portal": "Filing destination",
                "filing_manner": "How to file",
            }
        )[["Requirement", "Regulation", "Timing rule", "Filing destination", "How to file"]],
        hide_index=True,
        width="stretch",
        height=430,
    )

with guide_tab:
    st.subheader("Filing guide")
    guide_options = applicable_catalog["title"].tolist()
    if not guide_options:
        st.info("No requirements match this profile in the current Main Board catalogue. Adjust the assumptions or add the applicable IGP catalogue data.")
    else:
        selected_requirement = st.selectbox("Requirement", guide_options)
        requirement = applicable_catalog[applicable_catalog["title"] == selected_requirement].iloc[0]
        guide_left, guide_right = st.columns(2, gap="large")
        with guide_left:
            st.markdown("#### Legal reference")
            st.write(requirement["regulation"])
            st.caption(requirement["law"])
            st.markdown("#### Timing")
            st.write(requirement["timeline"])
            st.markdown("#### Applicability")
            st.write(requirement["applicability"])
        with guide_right:
            st.markdown("#### Filing destination")
            st.write(requirement["portal"])
            st.markdown("#### Manner of filing")
            st.write(requirement["filing_manner"])
            st.markdown("#### Source")
            st.markdown(f"[NSE compliance calendar]({requirement['source_url']})")

with company_tab:
    st.subheader("Company register")
    st.caption("The selected company’s category and applicability facts decide which rules are shown. Keep these facts reviewed and attributable.")

    with st.form("add_company_form", clear_on_submit=True):
        st.markdown("#### Add a company")
        name_column, symbol_column, category_column = st.columns([1.5, 1, 1])
        company_name = name_column.text_input("Company name *")
        nse_symbol = symbol_column.text_input("NSE symbol")
        listing_category = category_column.selectbox("Listing category", LISTING_CATEGORIES)
        top_1000_column, large_entity_column, sast_column = st.columns(3)
        top_1000 = top_1000_column.checkbox("Top 1,000 listed entity")
        large_entity = large_entity_column.checkbox("Large entity disclosure applies")
        promoter_sast = sast_column.checkbox("Promoter SAST workflow applies")
        owner_column, review_column = st.columns(2)
        profile_owner = owner_column.text_input("Profile owner")
        profile_last_reviewed = review_column.date_input("Profile last reviewed", value=date.today())
        notes = st.text_area("Notes", placeholder="Record the source or decision supporting this company profile.")
        add_company = st.form_submit_button("Add company", type="primary")

    if add_company:
        normalised_name = company_name.strip()
        if not normalised_name:
            st.error("Enter a company name before adding it.")
        elif normalised_name.casefold() in set(companies["company_name"].str.casefold()):
            st.error("That company is already in the register. Update the existing record below instead.")
        else:
            company_id = create_company_id(normalised_name, set(companies["company_id"]))
            new_company = pd.DataFrame(
                [
                    {
                        "company_id": company_id,
                        "company_name": normalised_name,
                        "nse_symbol": nse_symbol,
                        "listing_category": listing_category,
                        "top_1000": top_1000,
                        "large_entity": large_entity,
                        "promoter_sast": promoter_sast,
                        "profile_last_reviewed": profile_last_reviewed.isoformat(),
                        "profile_owner": profile_owner,
                        "notes": notes,
                    }
                ]
            )
            save_companies(pd.concat([companies, new_company], ignore_index=True))
            st.session_state.pending_company_id = company_id
            st.rerun()

    st.markdown("#### Review and maintain the register")
    edited_companies = st.data_editor(
        companies,
        column_config={
            "company_id": st.column_config.TextColumn("Record ID"),
            "company_name": st.column_config.TextColumn("Company name", required=True),
            "nse_symbol": st.column_config.TextColumn("NSE symbol"),
            "listing_category": st.column_config.SelectboxColumn("Listing category", options=LISTING_CATEGORIES, required=True),
            "top_1000": st.column_config.CheckboxColumn("Top 1,000"),
            "large_entity": st.column_config.CheckboxColumn("Large entity"),
            "promoter_sast": st.column_config.CheckboxColumn("Promoter SAST"),
            "profile_last_reviewed": st.column_config.TextColumn("Last reviewed"),
            "profile_owner": st.column_config.TextColumn("Profile owner"),
            "notes": st.column_config.TextColumn("Notes", width="large"),
        },
        disabled=["company_id"],
        hide_index=True,
        width="stretch",
        key="company_register_editor",
    )
    register_actions, _ = st.columns([0.35, 0.65])
    if register_actions.button("Save company register", type="primary"):
        try:
            save_companies(edited_companies)
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Company register saved.")
            st.rerun()

    st.markdown("#### Delete a company")
    if len(companies) == 1:
        st.caption("Add another company before deleting this one. The dashboard needs at least one company record.")
    else:
        with st.form("delete_company_form"):
            company_to_delete = st.selectbox(
                "Company to delete",
                options=companies["company_id"].tolist(),
                format_func=lambda company_id: (
                    f"{companies.loc[companies['company_id'] == company_id, 'company_name'].iloc[0]}"
                    f" ({companies.loc[companies['company_id'] == company_id, 'nse_symbol'].iloc[0] or 'No symbol'})"
                ),
            )
            confirm_delete = st.checkbox("I understand this permanently removes the company record from this register.")
            delete_company = st.form_submit_button("Delete selected company")

        if delete_company:
            if not confirm_delete:
                st.error("Confirm the deletion before removing the company.")
            else:
                remaining_companies = companies[companies["company_id"] != company_to_delete].copy()
                save_companies(remaining_companies)
                st.session_state.pending_company_id = remaining_companies.iloc[0]["company_id"]
                st.rerun()

    st.download_button(
        "Download company register (CSV)",
        data=companies.to_csv(index=False),
        file_name="companies.csv",
        mime="text/csv",
    )
    st.info("When run locally, Save updates data/companies.csv. Streamlit Community Cloud does not guarantee permanent file writes, so use the CSV download as a backup or connect a managed database before shared production use.")

st.divider()
st.caption(f"Source catalogue: [NSE compliance calendar]({SOURCE_URL}). Last verified by the dashboard owner before use; do not rely on this prototype as a complete compliance opinion.")

# Listing Compliance Compass

A free Streamlit prototype for planning recurring and event-driven NSE/SEBI listed-company compliance work. It turns the compliance calendar in the supplied brief into a dashboard with filing destinations, timing rules, a rolling schedule and a CSV download.

## Run locally

```bash
cd /Users/khushjain/Documents/Codex/2026-10-24/files-mentioned-by-the-user-i/outputs/listed-company-compliance-dashboard
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Streamlit opens the dashboard in your browser. Stop it with `Ctrl+C`.

## Free deployment

1. Create a GitHub repository and upload this project.
2. Sign in to [Streamlit Community Cloud](https://share.streamlit.io/).
3. Choose the repository, branch, and `app.py` as the entrypoint.
4. Deploy. The `requirements.txt` file supplies the needed packages.

## Update the compliance catalogue

Edit `data/compliance_catalog.csv` whenever NSE/SEBI changes a rule, deadline, filing tile or applicability condition. The app intentionally labels company profiles as demos; connect a maintained company master only after you have authoritative coverage data.

## Add companies and applicability facts

Use the **Company register** tab in the app to add a company, choose its listing category, and record the applicability facts that control filtering. It also supports reviewing, editing and deleting company records; deletion requires an explicit confirmation and the final remaining record cannot be deleted. The register is stored in `data/companies.csv` when running locally. Download that CSV before making material changes so it can be reviewed and restored.

The current rule catalogue covers Main Board equity issuers. Do not apply it to Emerge/SME, IGP, debt or other categories until a category-specific catalogue has been independently checked and added. Streamlit Community Cloud does not provide durable file storage for shared production data; use a managed database or a controlled shared source before relying on edits made in the deployed app.

## Important limitation

The dashboard is a planning aid, not legal advice. Several requirements depend on an event, board meeting, annual-report dispatch, financial-results publication, security type or company-specific status. Check the latest official NSE/SEBI material, prescribed forms and portal instructions before filing.

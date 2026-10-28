# Listing Compliance Compass

A compliance management dashboard for Indian companies: choose your entity type, see every applicable obligation
under **Companies Act 2013, SEBI LODR, SEBI PIT, SEBI SAST and FEMA** with regulation number, timeline and mode of
filing, and mark each one Pending / Completed / Not applicable with a completion date.

## Files
| File | Purpose |
|---|---|
| `app.py` | Streamlit interface |
| `engine.py` | Applicability and due-date logic |
| `data/regulatory_catalog.csv` | The rulebook (100 requirements). Edit this to update a rule |
| `data/companies.csv` | Demo companies loaded at start |
| `data/updates.csv` | Dated changes shown in the Overview. Add a row when a rule changes (`affects` = catalogue ids, blank = everyone) |
| `data/task_status.csv`, `data/events.csv` | Empty starting files |
| `requirements.txt` | Python dependencies |

## Run locally
```
pip install -r requirements.txt
streamlit run app.py
```
To save changes to the CSV files while running locally: `COMPLIANCE_PERSIST=1 streamlit run app.py`

## Entity types covered
Private (small / other), unlisted public, listed Main Board (Top 100/250/500/1000/2000), SME, IGP, debt-only
(with HVDLE), and equity + debt. FDI, ODI, ECB, subsidiaries, MSME dues, CSR, dividend, cost audit and
SAST facts switch extra compliances on.

## How data is stored (important for the public app)
On Streamlit Community Cloud every visitor gets a private session; nothing is shared between users and nothing
survives a refresh. Use **Save / restore my work** in the sidebar to download and re-upload a backup (.json).
For permanent team use, connect a database or Google Sheet.

## Updating a rule
Each row of `regulatory_catalog.csv` has: `entities` (who), `requires` (facts needed, `;` = AND), `min_tier`,
`deadline_rule` (e.g. `q:30`, `agm:60`, `fixed_md:6-30`, `event_days:30`), regulation, timeline, filing mode and a
verification flag. Commit the change to GitHub and the live app updates.

*Planning tool, not legal advice. Verify against current law before filing.*

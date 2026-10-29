"""Small, dependency-light shared utilities.

Kept separate from main.py (rather than having services/*.py import these
from main.py directly) specifically to avoid a circular import: main.py
imports route logic from services/*.py, so anything services/*.py needs from
main.py has to live somewhere both can import from instead.
"""
import os
import duckdb
from fastapi import HTTPException

DB_PATH = os.getenv('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'strava_warehouse.duckdb'))


def get_db_connection():
    try:
        # read_only=False allows API to perform CRUD operations.
        # Connections must be closed quickly to prevent holding the writer lock.
        return duckdb.connect(DB_PATH, read_only=False)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database connection error: {str(e)}")


# Fallback used when an athlete's own max heart rate isn't on record.
# Previously three independent hardcoded "190"/"190.0" literals in main.py;
# centralized here so services/*.py can share it too. (src/pipeline.py's own
# DEFAULT_MAX_HR is 192 — the two files' fallback values were never unified,
# see agent_prompting/improvement_plan.md Section 2/6.)
DEFAULT_MAX_HR = 190

# Resting heart rate used for heart rate reserve until the profile stores one.
DEFAULT_REST_HR = 60

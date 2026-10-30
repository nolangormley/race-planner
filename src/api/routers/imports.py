"""Import FIT and GPX files as activities."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
import logging

from src.api import deps
from src.api.auth_routes import get_current_user
from src.api.database import User
from src.api.services.imports import import_files
from src.pipeline import StravaDB

logger = logging.getLogger("race_planner.api")
router = APIRouter()

MAX_FILES = 20
MAX_BYTES = 25 * 1024 * 1024


@router.post("/api/activities/import")
def import_activities(files: list[UploadFile] = File(...), current_user: User = Depends(get_current_user)):
    """Reads each upload, writes the new activities and runs the analysis
    passes before answering, so the list is complete on reload."""
    if len(files) > MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Import up to {MAX_FILES} files at a time.")
    payloads, refused = [], []
    for upload in files:
        name = upload.filename or "upload"
        data = upload.file.read()
        if len(data) > MAX_BYTES:
            refused.append({"file": name, "status": "error", "detail": "Larger than 25 MB"})
            continue
        payloads.append((name, data))
    db = StravaDB(db_path=deps.DB_PATH)
    try:
        results = import_files(db, current_user, payloads)
    finally:
        db.con.close()
    results += refused
    return {"results": results, "imported": sum(1 for result in results if result["status"] == "imported")}

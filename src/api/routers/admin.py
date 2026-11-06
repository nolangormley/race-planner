"""The admin page's data. ADMIN_EMAILS names the admins; for anyone else
the route answers 404, like the page."""
from datetime import datetime

from fastapi import APIRouter, Depends

from src.api.auth_routes import get_admin
from src.api.database import SessionLocal, User
from src.api.deps import get_db_connection
from src.api.services.admin import activation, recent_feedback, user_facts

router = APIRouter()


@router.get("/api/admin/stats")
def admin_stats(admin: User = Depends(get_admin)):
    db = SessionLocal()
    con = get_db_connection()
    try:
        report = activation(user_facts(db, con), datetime.now())
        report["feedback"] = recent_feedback(db)
    finally:
        con.close()
        db.close()
    return report

from fastapi import APIRouter, Request, Form, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import os
import requests
from dotenv import load_dotenv
import urllib.parse
from datetime import datetime

from .database import SessionLocal, User

load_dotenv()

router = APIRouter()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))

STRAVA_CLIENT_ID = os.getenv("STRAVA_CLIENT_ID", "")
STRAVA_CLIENT_SECRET = os.getenv("STRAVA_CLIENT_SECRET", "")
# In production, this should be the actual host
HOST_URL = os.getenv("HOST_URL", "http://localhost:8000")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_current_user_from_cookie(request: Request, db: Session):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == int(user_id)).first()

@router.get("/login", response_class=HTMLResponse)
async def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
async def login_post(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == username, User.password == password).first()
    if not user:
        return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid email or password."})
    
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="user_id", value=str(user.id))
    return response

@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/login")
    response.delete_cookie("user_id")
    return response

@router.get("/register", response_class=HTMLResponse)
async def register_get(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@router.post("/register")
async def register_post(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    date_of_birth: str = Form(...),
    gender: str = Form(...),
    height: float = Form(...),
    weight: float = Form(...),
    db: Session = Depends(get_db)
):
    existing_user = db.query(User).filter(User.email == email).first()
    if existing_user:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Email already registered."})
    
    dob = datetime.strptime(date_of_birth, "%Y-%m-%d").date()
    new_user = User(
        name=name, email=email, password=password, 
        date_of_birth=dob, gender=gender, height=height, weight=weight
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    response = RedirectResponse(url="/setup-race", status_code=status.HTTP_302_FOUND)
    response.set_cookie(key="user_id", value=str(new_user.id))
    return response

@router.get("/setup-race", response_class=HTMLResponse)
async def setup_race_get(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("setup.html", {"request": request, "user": user})

@router.post("/setup-race")
async def setup_race_post(
    request: Request,
    race_length: str = Form(...),
    race_date: str = Form(...),
    race_goal_time: str = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login")
    
    try:
        r_date = datetime.strptime(race_date, "%Y-%m-%d").date()
    except:
        return templates.TemplateResponse("setup.html", {"request": request, "error": "Invalid date format."})
        
    user.race_length = race_length
    user.race_date = r_date
    user.race_goal_time = race_goal_time
    db.commit()
    
    return RedirectResponse(url="/strava-auth", status_code=status.HTTP_302_FOUND)

@router.get("/strava-auth", response_class=HTMLResponse)
async def strava_auth(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login")
        
    redirect_uri = f"{HOST_URL}/strava-callback"
    params = {
        "client_id": STRAVA_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "approval_prompt": "force",
        "scope": "read,activity:read_all,profile:read_all"
    }
    strava_auth_url = "https://www.strava.com/oauth/authorize?" + urllib.parse.urlencode(params)
    
    return templates.TemplateResponse("strava_auth.html", {"request": request, "user": user, "strava_auth_url": strava_auth_url})

@router.get("/strava-callback")
async def strava_callback(request: Request, code: str = None, error: str = None, db: Session = Depends(get_db)):
    user = get_current_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/login")
        
    if error:
        return HTMLResponse(f"Strava authentication returned an error: {error}. <a href='/strava-auth'>Try again</a>")
        
    if not code:
        return HTMLResponse("No code provided. <a href='/strava-auth'>Try again</a>")
        
    # Exchange code for access token
    token_url = "https://www.strava.com/oauth/token"
    payload = {
        "client_id": STRAVA_CLIENT_ID,
        "client_secret": STRAVA_CLIENT_SECRET,
        "code": code,
        "grant_type": "authorization_code"
    }
    
    try:
        resp = requests.post(token_url, data=payload)
        resp.raise_for_status()
        data = resp.json()
        
        user.strava_access_token = data.get("access_token")
        user.strava_refresh_token = data.get("refresh_token")
        if "athlete" in data:
            user.strava_athlete_id = data["athlete"].get("id")
            
        db.commit()
    except Exception as e:
        return HTMLResponse(f"Failed to authenticate with Strava: {str(e)}. <a href='/strava-auth'>Try again</a>")
        
    # Strava complete! Redirect to dashboard.
    # In a real app we would kick off a background task here to ingest their data.
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_302_FOUND)

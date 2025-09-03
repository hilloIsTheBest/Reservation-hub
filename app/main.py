from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from icalendar import Calendar, Event

from .database import Base, engine, SessionLocal
from .models import User, Resource, Reservation
from .auth import router as auth_router
from .utils import overlaps

SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")

app = FastAPI(title="Family Fleet Booker (ICS)")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False, same_site="lax")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
app.include_router(auth_router)

Base.metadata.create_all(bind=engine)

with SessionLocal() as db:
    if db.query(Resource).count() == 0:
        db.add_all([Resource(name="Car 1", color="#1e90ff"),
                    Resource(name="Car 2", color="#28a745"),
                    Resource(name="Bike",  color="#ffc107")])
        db.commit()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

async def current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(User).get(uid)

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, db: Session = Depends(get_db)):
    user = await current_user(request, db)
    return templates.TemplateResponse("index.html", {"request": request, "user": user})

# --- Resources (admin can add/delete) ---
@app.get("/api/resources")
async def list_resources(db: Session = Depends(get_db)):
    rs = db.query(Resource).all()
    return [{"id": r.id, "name": r.name, "color": r.color} for r in rs]

@app.post("/api/resources")
async def create_resource(request: Request, db: Session = Depends(get_db)):
    user = await current_user(request, db)
    if not (user and user.is_admin):
        raise HTTPException(status_code=403, detail="Admin only")
    data = await request.json()
    name = (data.get("name") or "").strip()
    color = (data.get("color") or "#3788d8").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    exists = db.query(Resource).filter_by(name=name).first()
    if exists:
        raise HTTPException(status_code=409, detail="Resource name already exists")
    r = Resource(name=name, color=color)
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "name": r.name, "color": r.color}

@app.delete("/api/resources/{rid}")
async def delete_resource(rid: int, request: Request, db: Session = Depends(get_db)):
    user = await current_user(request, db)
    if not (user and user.is_admin):
        raise HTTPException(status_code=403, detail="Admin only")
    res = db.query(Resource).get(rid)
    if not res:
        raise HTTPException(status_code=404, detail="Not found")
    # Keep simple: prevent delete if reservations exist
    if db.query(Reservation).filter(Reservation.resource_id == rid).count() > 0:
        raise HTTPException(status_code=409, detail="Cannot delete: reservations exist")
    db.delete(res); db.commit()
    return {"ok": True}

# --- Events ---
@app.get("/api/events")
async def list_events(start: str, end: str, resource_id: int | None = None, db: Session = Depends(get_db)):
    s = datetime.fromisoformat(start.replace("Z", "+00:00")).replace(tzinfo=None)
    e = datetime.fromisoformat(end.replace("Z", "+00:00")).replace(tzinfo=None)
    q = db.query(Reservation).filter(Reservation.end_utc > s, Reservation.start_utc < e)
    if resource_id:
        q = q.filter(Reservation.resource_id == resource_id)
    out = []
    for r in q.all():
        out.append({"id": r.id,
                    "title": f"{r.resource.name}: {r.title}",
                    "start": r.start_utc.isoformat()+"Z",
                    "end":   r.end_utc.isoformat()+"Z",
                    "backgroundColor": r.resource.color})
    return out

@app.post("/api/bookings")
async def create_booking(request: Request, db: Session = Depends(get_db)):
    user = await current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    data = await request.json()
    title = data.get("title") or "Reservation"
    resource_id = int(data.get("resource_id"))
    start = datetime.fromisoformat(data.get("start").replace("Z", "+00:00")).replace(tzinfo=None)
    end   = datetime.fromisoformat(data.get("end").replace("Z", "+00:00")).replace(tzinfo=None)

    existing = db.query(Reservation).filter(Reservation.resource_id == resource_id).all()
    for e in existing:
        if overlaps(start, end, e.start_utc, e.end_utc):
            raise HTTPException(status_code=409, detail="Time overlaps with an existing reservation for this resource")

    r = Reservation(title=title, start_utc=start, end_utc=end, resource_id=resource_id, user_id=user.id)
    db.add(r); db.commit(); db.refresh(r)
    return {"ok": True, "id": r.id}

@app.delete("/api/bookings/{res_id}")
async def delete_booking(res_id: int, request: Request, db: Session = Depends(get_db)):
    user = await current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    r = db.query(Reservation).get(res_id)
    if not r:
        raise HTTPException(status_code=404, detail="Not found")
    if not (user.is_admin or r.user_id == user.id):
        raise HTTPException(status_code=403, detail="Not allowed")
    db.delete(r); db.commit()
    return {"ok": True}

# --- ICS Feeds ---
def _ics_for_reservations(res_list: list[Reservation]):
    cal = Calendar()
    cal.add("prodid", "-//Family Fleet//EN")
    cal.add("version", "2.0")
    now = datetime.now(timezone.utc)
    for r in res_list:
        ev = Event()
        ev.add("uid", f"reservation-{r.id}@family-fleet")
        ev.add("summary", f"{r.resource.name}: {r.title}")
        ev.add("dtstart", r.start_utc.replace(tzinfo=timezone.utc))
        ev.add("dtend",   r.end_utc.replace(tzinfo=timezone.utc))
        ev.add("dtstamp", now)
        if r.description:
            ev.add("description", r.description)
        cal.add_component(ev)
    return cal.to_ical()

@app.get("/ics/all.ics")
async def ics_all(db: Session = Depends(get_db)):
    rs = db.query(Reservation).all()
    return Response(_ics_for_reservations(rs), media_type="text/calendar; charset=utf-8")

@app.get("/ics/resource/{rid}.ics")
async def ics_resource(rid: int, db: Session = Depends(get_db)):
    rs = db.query(Reservation).filter(Reservation.resource_id == rid).all()
    return Response(_ics_for_reservations(rs), media_type="text/calendar; charset=utf-8")

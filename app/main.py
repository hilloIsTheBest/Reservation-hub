from __future__ import annotations
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError
from icalendar import Calendar, Event

from .database import Base, engine, SessionLocal, run_migrations
from .models import User, Resource, Reservation, Home, HomeMember, HomeResource, HomeReservation
from .auth import router as auth_router
from .utils import overlaps, parse_to_utc_naive, isoformat_z
from .paths import static_dir, templates_dir
from icalendar import Calendar as ICal, Event as ICalEvent
import requests

SECRET_KEY = os.getenv("SECRET_KEY", "change-me")
BASE_URL = os.getenv("BASE_URL", "http://localhost:9629")
OFFLINE_MODE = os.getenv("OFFLINE", "0") == "1"

app = FastAPI(title="Family Fleet Booker (ICS)")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, https_only=False, same_site="lax")
app.mount("/static", StaticFiles(directory=static_dir()), name="static")
templates = Jinja2Templates(directory=templates_dir())
app.include_router(auth_router)

# Migrate legacy DBs, then create any missing tables
run_migrations(engine)
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
    if OFFLINE_MODE:
        # Ensure a local offline admin user exists and return it
        u = db.query(User).filter_by(sub="offline-local").first()
        if not u:
            u = User(sub="offline-local", email="offline@local", name="Offline User", is_admin=True)
            db.add(u); db.commit(); db.refresh(u)
        return u
    uid = request.session.get("user_id")
    if not uid:
        return None
    return db.query(User).get(uid)

# --- Helpers for new multi-home feature ---
def require_user(user: Optional[User]):
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    return user


def require_home_member(db: Session, user: User, home_id: int) -> Home:
    home = db.query(Home).get(home_id)
    if not home:
        raise HTTPException(status_code=404, detail="Home not found")
    if not (home.owner_id == user.id or db.query(HomeMember).filter_by(home_id=home_id, user_id=user.id).first()):
        raise HTTPException(status_code=403, detail="Not a member of this home")
    return home


def require_home_owner(db: Session, user: User, home_id: int) -> Home:
    home = db.query(Home).get(home_id)
    if not home:
        raise HTTPException(status_code=404, detail="Home not found")
    if home.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Only the home owner can perform this action")
    return home

@app.get("/", response_class=HTMLResponse)
async def home_new(request: Request, db: Session = Depends(get_db)):
    user = await current_user(request, db)
    return templates.TemplateResponse("home.html", {"request": request, "user": user, "offline": OFFLINE_MODE})

@app.get("/og", response_class=HTMLResponse)
async def home_og(request: Request, db: Session = Depends(get_db)):
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
    # Convert incoming window to naive UTC for DB comparisons
    s = parse_to_utc_naive(start)
    e = parse_to_utc_naive(end)
    q = db.query(Reservation).filter(Reservation.end_utc > s, Reservation.start_utc < e)
    if resource_id:
        q = q.filter(Reservation.resource_id == resource_id)
    out = []
    for r in q.all():
        out.append({
            "id": r.id,
            "title": f"{r.resource.name}: {r.title}",
            # Serve UTC with explicit Z so the browser renders correctly
            "start": isoformat_z(r.start_utc),
            "end":   isoformat_z(r.end_utc),
            "backgroundColor": r.resource.color,
        })
    return out

@app.post("/api/bookings")
async def create_booking(request: Request, db: Session = Depends(get_db)):
    user = await current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    data = await request.json()
    title = data.get("title") or "Reservation"
    resource_id = int(data.get("resource_id"))
    # Normalize inputs to UTC (naive) so comparisons and storage are consistent
    start = parse_to_utc_naive(data.get("start"))
    end   = parse_to_utc_naive(data.get("end"))

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


# =====================
# New Multi-Home APIs
# =====================

@app.get("/api/users")
async def list_users(request: Request, db: Session = Depends(get_db)):
    user = await current_user(request, db)
    require_user(user)
    us = db.query(User).all()
    return [{"id": u.id, "name": u.name, "email": u.email} for u in us]


@app.get("/api/homes")
async def api_list_homes(request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    try:
        owned = db.query(Home).filter_by(owner_id=user.id).all()
        member_home_ids = [m.home_id for m in db.query(HomeMember).filter_by(user_id=user.id).all()]
        members = db.query(Home).filter(Home.id.in_(member_home_ids)).all() if member_home_ids else []
        homes = {h.id: h for h in owned}
        for h in members:
            homes[h.id] = h
        return [{"id": h.id, "name": h.name, "is_owner": h.owner_id == user.id} for h in homes.values()]
    except OperationalError as e:
        if "no such column" in str(e).lower():
            # Self-heal legacy DBs at runtime
            run_migrations(engine)
            db.rollback()
            try:
                with SessionLocal() as db2:
                    owned = db2.query(Home).filter_by(owner_id=user.id).all()
                    member_home_ids = [m.home_id for m in db2.query(HomeMember).filter_by(user_id=user.id).all()]
                    members = db2.query(Home).filter(Home.id.in_(member_home_ids)).all() if member_home_ids else []
                    homes = {h.id: h for h in owned}
                    for h in members:
                        homes[h.id] = h
                    return [{"id": h.id, "name": h.name, "is_owner": h.owner_id == user.id} for h in homes.values()]
            except Exception:
                pass
        raise


@app.post("/api/homes")
async def api_create_home(request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    data = await request.json()
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    try:
        if db.query(Home).filter_by(name=name).first():
            raise HTTPException(status_code=409, detail="Home name already exists")
        h = Home(name=name, owner_id=user.id)
        db.add(h); db.commit(); db.refresh(h)
        db.add(HomeMember(home_id=h.id, user_id=user.id)); db.commit()
        return {"id": h.id, "name": h.name, "is_owner": True}
    except OperationalError as e:
        if "no such column" in str(e).lower():
            run_migrations(engine)
            db.rollback()
            with SessionLocal() as db2:
                if db2.query(Home).filter_by(name=name).first():
                    raise HTTPException(status_code=409, detail="Home name already exists")
                h = Home(name=name, owner_id=user.id)
                db2.add(h); db2.commit(); db2.refresh(h)
                db2.add(HomeMember(home_id=h.id, user_id=user.id)); db2.commit()
                return {"id": h.id, "name": h.name, "is_owner": True}
        raise


@app.get("/api/homes/{home_id}")
async def api_get_home(home_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    h = require_home_member(db, user, home_id)
    members = db.query(HomeMember).filter_by(home_id=home_id).all()
    member_users = db.query(User).filter(User.id.in_([m.user_id for m in members] or [0])).all()
    return {
        "id": h.id,
        "name": h.name,
        "is_owner": h.owner_id == user.id,
        "members": [{"id": u.id, "name": u.name, "email": u.email} for u in member_users],
    }


@app.post("/api/homes/{home_id}/members")
async def api_add_member(home_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    require_home_owner(db, user, home_id)
    data = await request.json()
    user_id = data.get("user_id")
    if not user_id:
        raise HTTPException(status_code=400, detail="user_id required")
    if db.query(HomeMember).filter_by(home_id=home_id, user_id=user_id).first():
        return {"ok": True}
    db.add(HomeMember(home_id=home_id, user_id=user_id)); db.commit()
    return {"ok": True}


@app.delete("/api/homes/{home_id}/members/{member_user_id}")
async def api_remove_member(home_id: int, member_user_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    h = require_home_owner(db, user, home_id)
    if member_user_id == h.owner_id:
        raise HTTPException(status_code=400, detail="Owner cannot be removed")
    m = db.query(HomeMember).filter_by(home_id=home_id, user_id=member_user_id).first()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found")
    db.delete(m); db.commit()
    return {"ok": True}


@app.get("/api/homes/{home_id}/resources")
async def api_home_resources(home_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    require_home_member(db, user, home_id)
    rs = db.query(HomeResource).filter_by(home_id=home_id).all()
    return [{"id": r.id, "name": r.name, "color": r.color} for r in rs]


@app.post("/api/homes/{home_id}/resources")
async def api_home_create_resource(home_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    require_home_member(db, user, home_id)
    data = await request.json()
    name = (data.get("name") or "").strip()
    color = (data.get("color") or "#3788d8").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name required")
    if db.query(HomeResource).filter_by(home_id=home_id, name=name).first():
        raise HTTPException(status_code=409, detail="Resource name already exists in this home")
    r = HomeResource(home_id=home_id, name=name, color=color)
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "name": r.name, "color": r.color}


@app.delete("/api/homes/{home_id}/resources/{rid}")
async def api_home_delete_resource(home_id: int, rid: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    require_home_member(db, user, home_id)
    res = db.query(HomeResource).filter_by(id=rid, home_id=home_id).first()
    if not res:
        raise HTTPException(status_code=404, detail="Not found")
    if db.query(HomeReservation).filter(HomeReservation.resource_id == rid).count() > 0:
        raise HTTPException(status_code=409, detail="Cannot delete: reservations exist")
    db.delete(res); db.commit()
    return {"ok": True}


from dateutil.rrule import rrulestr


def _expand_recurring(ev: HomeReservation, window_start: datetime, window_end: datetime) -> list[dict]:
    if not ev.rrule:
        return [{
            "id": ev.id,
            "title": f"{ev.resource.name}: {ev.title}",
            "start": isoformat_z(ev.start_utc),
            "end": isoformat_z(ev.end_utc),
            "resource_id": ev.resource_id,
            "backgroundColor": ev.resource.color,
            "recurring": False,
            "canDelete": True,
        }]
    try:
        dtstart = ev.start_utc
        rule = rrulestr(ev.rrule, dtstart=dtstart)
        out = []
        duration = ev.end_utc - ev.start_utc
        for dt in rule.between(window_start, window_end, inc=False):
            start = dt
            end = dt + duration
            out.append({
                "id": ev.id,
                "title": f"{ev.resource.name}: {ev.title}",
                "start": isoformat_z(start),
                "end": isoformat_z(end),
                "resource_id": ev.resource_id,
                "backgroundColor": ev.resource.color,
                "recurring": True,
                "canDelete": True,
            })
        return out
    except Exception:
        return [{
            "id": ev.id,
            "title": f"{ev.resource.name}: {ev.title}",
            "start": isoformat_z(ev.start_utc),
            "end": isoformat_z(ev.end_utc),
            "resource_id": ev.resource_id,
            "backgroundColor": ev.resource.color,
            "recurring": False,
            "canDelete": True,
        }]


@app.get("/api/homes/{home_id}/events")
async def api_home_events(home_id: int, start: str, end: str, resource_id: int | None = None, request: Request = None, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    require_home_member(db, user, home_id)
    s = parse_to_utc_naive(start)
    e = parse_to_utc_naive(end)
    q = db.query(HomeReservation).filter(HomeReservation.home_id == home_id, HomeReservation.end_utc > s, HomeReservation.start_utc < e)
    if resource_id:
        q = q.filter(HomeReservation.resource_id == resource_id)
    out = []
    for r in q.all():
        out.extend(_expand_recurring(r, s, e))
    return out


@app.post("/api/homes/{home_id}/bookings")
async def api_home_create_booking(home_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    require_home_member(db, user, home_id)
    data = await request.json()
    title = data.get("title") or "Reservation"
    resource_id = int(data.get("resource_id"))
    start = parse_to_utc_naive(data.get("start"))
    end = parse_to_utc_naive(data.get("end"))
    rrule = (data.get("rrule") or "").strip()

    existing = db.query(HomeReservation).filter(HomeReservation.resource_id == resource_id).all()
    for e0 in existing:
        if not e0.rrule and overlaps(start, end, e0.start_utc, e0.end_utc):
            raise HTTPException(status_code=409, detail="Time overlaps with an existing reservation for this resource")
    r = HomeReservation(title=title, start_utc=start, end_utc=end, resource_id=resource_id, user_id=user.id, home_id=home_id, rrule=rrule)
    db.add(r); db.commit(); db.refresh(r)
    return {"ok": True, "id": r.id}


@app.delete("/api/homes/{home_id}/bookings/{res_id}")
async def api_home_delete_booking(home_id: int, res_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    require_home_member(db, user, home_id)
    r = db.query(HomeReservation).filter_by(id=res_id, home_id=home_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Not found")
    if not (user.is_admin or r.user_id == user.id or db.query(Home).get(home_id).owner_id == user.id):
        raise HTTPException(status_code=403, detail="Not allowed")
    db.delete(r); db.commit()
    return {"ok": True}


def _ics_for_home_reservations(res_list: list[HomeReservation], horizon_days: int = 365):
    cal = Calendar()
    cal.add("prodid", "-//Family Fleet Home//EN")
    cal.add("version", "2.0")
    now = datetime.now(timezone.utc)
    window_start = now.replace(tzinfo=None)
    window_end = window_start + timedelta(days=horizon_days)
    for r in res_list:
        if r.rrule:
            for occ in _expand_recurring(r, window_start, window_end):
                ev = Event()
                ev.add("uid", f"home-resv-{r.id}-{occ['start']}@family-fleet")
                ev.add("summary", f"{r.resource.name}: {r.title}")
                start = datetime.fromisoformat(occ["start"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
                end = datetime.fromisoformat(occ["end"].replace("Z", "+00:00")).replace(tzinfo=timezone.utc)
                ev.add("dtstart", start)
                ev.add("dtend",   end)
                ev.add("dtstamp", now)
                if r.description:
                    ev.add("description", r.description)
                cal.add_component(ev)
        else:
            ev = Event()
            ev.add("uid", f"home-resv-{r.id}@family-fleet")
            ev.add("summary", f"{r.resource.name}: {r.title}")
            ev.add("dtstart", r.start_utc.replace(tzinfo=timezone.utc))
            ev.add("dtend",   r.end_utc.replace(tzinfo=timezone.utc))
            ev.add("dtstamp", now)
            if r.description:
                ev.add("description", r.description)
            cal.add_component(ev)
    return cal.to_ical()


@app.get("/ics/home/{home_id}.ics")
async def ics_home(home_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(await current_user(request, db))
    require_home_member(db, user, home_id)
    rs = db.query(HomeReservation).filter_by(home_id=home_id).all()
    return Response(_ics_for_home_reservations(rs), media_type="text/calendar; charset=utf-8")


# =====================
# Offline sync endpoints
# =====================

def _get_or_create_offline_home(db: Session, user: User) -> Home:
    h = db.query(Home).filter_by(name="Offline Home").first()
    if not h:
        h = Home(name="Offline Home", owner_id=user.id)
        db.add(h); db.commit(); db.refresh(h)
        db.add(HomeMember(home_id=h.id, user_id=user.id)); db.commit()
    return h


@app.get("/offline/status")
async def offline_status():
    return {"offline": OFFLINE_MODE}


@app.post("/offline/sync")
async def offline_sync(request: Request, db: Session = Depends(get_db)):
    if not OFFLINE_MODE:
        raise HTTPException(status_code=400, detail="Not in offline mode")
    user = await current_user(request, db)
    data = await request.json()
    server_url = (data.get("server_url") or "").strip().rstrip('/')
    if not server_url:
        raise HTTPException(status_code=400, detail="server_url required")

    h = _get_or_create_offline_home(db, user)

    # 1) Fetch resources from server
    resources_created = 0
    resources_updated = 0
    try:
        resp = requests.get(f"{server_url}/api/resources", timeout=10)
        resp.raise_for_status()
        remote_resources = resp.json() if resp.content else []
        # Merge by name
        for rr in remote_resources:
            name = rr.get("name") or ""
            color = rr.get("color") or "#3788d8"
            if not name:
                continue
            existing = db.query(HomeResource).filter_by(home_id=h.id, name=name).first()
            if not existing:
                db.add(HomeResource(home_id=h.id, name=name, color=color)); db.commit(); resources_created += 1
            else:
                if existing.color != color:
                    existing.color = color
                    db.add(existing); db.commit(); resources_updated += 1
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": f"Failed to fetch resources: {e}"})

    # Build resource map
    res_map = {r.name: r for r in db.query(HomeResource).filter_by(home_id=h.id).all()}

    # 2) Fetch ICS and import events
    events_imported = 0
    try:
        ics = requests.get(f"{server_url}/ics/all.ics", timeout=15)
        ics.raise_for_status()
        cal = ICal.from_ical(ics.content)
        # For simplicity, wipe existing imported reservations in Offline Home before import
        db.query(HomeReservation).filter_by(home_id=h.id).delete()
        db.commit()
        for comp in cal.walk():
            if comp.name != "VEVENT":
                continue
            summary = str(comp.get("summary", "")).strip()
            title = summary
            res_name = None
            if ":" in summary:
                res_name, title = [p.strip() for p in summary.split(":", 1)]
            dtstart = comp.get("dtstart")
            dtend = comp.get("dtend")
            if not dtstart or not dtend:
                continue
            sdt = dtstart.dt
            edt = dtend.dt
            # Convert to UTC naive
            if getattr(sdt, 'tzinfo', None) is not None:
                sdt = sdt.astimezone(timezone.utc).replace(tzinfo=None)
            if getattr(edt, 'tzinfo', None) is not None:
                edt = edt.astimezone(timezone.utc).replace(tzinfo=None)
            if res_name and res_name in res_map:
                resource = res_map[res_name]
            else:
                # Create resource if missing
                resource = res_map.get(res_name or "Imported")
                if resource is None:
                    resource = HomeResource(home_id=h.id, name=res_name or "Imported", color="#6c5ce7")
                    db.add(resource); db.commit(); db.refresh(resource)
                    res_map[resource.name] = resource
            r = HomeReservation(title=title or "Reservation", start_utc=sdt, end_utc=edt, resource_id=resource.id, user_id=user.id, home_id=h.id, rrule="")
            db.add(r); db.commit(); events_imported += 1
    except Exception as e:
        return JSONResponse(status_code=502, content={"ok": False, "error": f"Failed to import ICS: {e}"})

    return {"ok": True, "resources_created": resources_created, "resources_updated": resources_updated, "events_imported": events_imported}

from __future__ import annotations
import os
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import RedirectResponse
from authlib.integrations.starlette_client import OAuth
from sqlalchemy.orm import Session
from .database import SessionLocal
from .models import User

router = APIRouter()
oauth = OAuth()

OIDC_ISSUER = os.getenv("OIDC_ISSUER", "")
OIDC_CLIENT_ID = os.getenv("OIDC_CLIENT_ID", "")
OIDC_CLIENT_SECRET = os.getenv("OIDC_CLIENT_SECRET", "")
OIDC_REDIRECT_PATH = os.getenv("OIDC_REDIRECT_PATH", "/auth/callback")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
OIDC_SCOPES = os.getenv("OIDC_SCOPES", "openid profile email")

if not all([OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET]):
    print("[WARN] OIDC env vars not fully configured. Login will fail until set.")

oauth.register(
    name="authentik",
    server_metadata_url=f"{OIDC_ISSUER.rstrip('/')}/.well-known/openid-configuration",
    client_id=OIDC_CLIENT_ID,
    client_secret=OIDC_CLIENT_SECRET,
    client_kwargs={"scope": OIDC_SCOPES},
)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.get("/login")
async def login(request: Request):
    redirect_uri = f"{BASE_URL.rstrip('/')}{OIDC_REDIRECT_PATH}"
    return await oauth.authentik.authorize_redirect(request, redirect_uri)

@router.get(OIDC_REDIRECT_PATH)
async def auth_callback(request: Request, db: Session = Depends(get_db)):
    try:
        token = await oauth.authentik.authorize_access_token(request)
        userinfo = await oauth.authentik.userinfo(token=token) or oauth.authentik.parse_id_token(request, token)
        sub = str(userinfo.get("sub"))
        email = userinfo.get("email") or ""
        name = userinfo.get("name") or userinfo.get("preferred_username") or email
        if not sub:
            raise HTTPException(status_code=400, detail="Invalid OIDC response: missing sub")
        user = db.query(User).filter_by(sub=sub).first()
        if not user:
            is_first = db.query(User).count() == 0
            user = User(sub=sub, email=email, name=name, is_admin=is_first)
            db.add(user); db.commit()
        request.session.update({"user_id": user.id, "name": user.name, "email": user.email, "is_admin": user.is_admin})
        return RedirectResponse(url="/")
    except Exception as e:
        print("OIDC callback error:", e)
        raise HTTPException(status_code=400, detail="Login failed")

@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/")

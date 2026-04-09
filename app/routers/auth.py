import json
import logging
import urllib.error
import urllib.request
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

logger = logging.getLogger(__name__)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings
from app.supabase_client import get_supabase, get_supabase_admin
from app.schemas.auth import (
    SignupRequest,
    LoginRequest,
    RefreshRequest,
    UserResponse,
    TokenResponse,
    SignupSuccessResponse,
    ResendVerificationRequest,
    ResendVerificationResponse,
    ProfileUpdateRequest,
)

router = APIRouter(prefix="/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)


def _user_to_response(user) -> UserResponse:
    if hasattr(user, "user_metadata"):
        meta = user.user_metadata or {}
        email = getattr(user, "email", "") or ""
    else:
        meta = (user.get("user_metadata") or {}) if isinstance(user, dict) else {}
        email = user.get("email", "") if isinstance(user, dict) else ""
    return UserResponse(
        id=user.get("id", "") if isinstance(user, dict) else str(getattr(user, "id", "")),
        email=email,
        full_name=meta.get("full_name") or meta.get("name") or "",
        username=meta.get("username") or (email.split("@")[0] if email else ""),
    )


def _upsert_profile_on_signup(
    user,
    full_name: str | None,
    username: str | None,
    zip_code: str | None = None,
    phone: str | None = None,
) -> None:
    """Insert or update public.profiles after signup. Uses service_role client if configured."""
    admin = get_supabase_admin()
    if not admin:
        return
    uid = getattr(user, "id", None) or (user.get("id") if isinstance(user, dict) else None)
    email = getattr(user, "email", "") or (user.get("email", "") if isinstance(user, dict) else "")
    meta = getattr(user, "user_metadata", None) or (user.get("user_metadata") or {}) if isinstance(user, dict) else {}
    if not uid:
        return
    name = full_name or meta.get("full_name") or meta.get("name") or ""
    uname = username or meta.get("username") or (email.split("@")[0] if email else "")
    row = {
        "id": str(uid),
        "email": email or None,
        "full_name": name or None,
        "username": uname or None,
        "zip_code": (zip_code or meta.get("zip_code") or "").strip() or None,
        "phone": (phone or meta.get("phone") or "").strip() or None,
    }
    try:
        admin.table("profiles").upsert(row, on_conflict="id").execute()
    except Exception as e:
        logger.error("Profile upsert failed: %s", e)


def _get_user_stats(admin, uid: str) -> dict:
    try:
        contributions_res = admin.table("posts").select("id", count="exact").eq("author_id", uid).execute()
        contributions_count = contributions_res.count if contributions_res else 0

        collab_res = admin.table("posts").select("collaboration_parent_id").eq("author_id", uid).not_.is_("collaboration_parent_id", "null").execute()
        
        if collab_res and collab_res.data:
            unique_parents = set(item['collaboration_parent_id'] for item in collab_res.data)
            collaborations_count = len(unique_parents)
        else:
            collaborations_count = 0

        return {
            "contributions_count": contributions_count,
            "collaborations_count": collaborations_count
        }
    except Exception as e:
        logger.error("Failed to fetch stats for uid %s: %s", uid, e)
        return {"contributions_count": 0, "collaborations_count": 0}

def _user_response_from_profile_or_meta(user, profile: dict | None) -> UserResponse:
    """Build UserResponse preferring profile row (full_name, username, email) over auth metadata."""
    # User can be Supabase user object or dict
    uid = user.get("id", "") if isinstance(user, dict) else str(getattr(user, "id", ""))
    email_from_user = (
        getattr(user, "email", "") if not isinstance(user, dict) else user.get("email", "")
    ) or ""
    meta = (
        user.user_metadata
        if hasattr(user, "user_metadata")
        else (user.get("user_metadata") or {}) if isinstance(user, dict) else {}
    ) or {}
    full_name_meta = meta.get("full_name") or meta.get("name") or ""
    username_meta = meta.get("username") or (email_from_user.split("@")[0] if email_from_user else "")

    email = (profile or {}).get("email") or email_from_user
    full_name = (profile or {}).get("full_name") or full_name_meta
    username = (profile or {}).get("username") or username_meta
    bio = (profile or {}).get("bio") or None
    avatar_url = (profile or {}).get("avatar_url") or None

    stats = {"contributions_count": 0, "collaborations_count": 0}
    admin = get_supabase_admin()
    if admin and str(uid):
        stats = _get_user_stats(admin, str(uid))

    return UserResponse(
        id=str((profile or {}).get("id") or uid),
        email=email,
        full_name=full_name,
        username=username,
        bio=bio,
        avatar_url=avatar_url,
        contributions_count=stats["contributions_count"],
        collaborations_count=stats["collaborations_count"],
    )


def _signup_via_admin_auto_confirm(body: SignupRequest):
    """Create user with email already confirmed (no verification email). Uses service_role."""
    admin = get_supabase_admin()
    if not admin:
        return None
    try:
        user_response = admin.auth.admin.create_user(
            {
                "email": body.email,
                "password": body.password,
                "email_confirm": True,
                "data": {
                    "full_name": body.full_name,
                    "username": body.username,
                    "zip_code": body.zip_code or "",
                    "phone": body.phone or "",
                },
            }
        )
    except Exception as e:
        err_msg = str(e)
        err = err_msg.lower()
        if "already registered" in err or "already exists" in err or "user already" in err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg or "Signup failed",
        )
    user = getattr(user_response, "user", None)
    if user:
        _upsert_profile_on_signup(
            user, body.full_name, body.username,
            zip_code=body.zip_code, phone=body.phone,
        )
    return user


@router.post("/signup", response_model=SignupSuccessResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest):
    """Register a new user. If SIGNUP_AUTO_CONFIRM_EMAIL=true, user can log in immediately without verifying email."""
    if settings.signup_auto_confirm_email and get_supabase_admin():
        try:
            user = _signup_via_admin_auto_confirm(body)
            if user:
                return SignupSuccessResponse()
        except HTTPException:
            raise
        except Exception as e:
            logger.warning("Signup (auto-confirm) error: %s", e, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e) or "Signup failed",
            )

    # Normal flow: sign_up sends verification email
    supabase = get_supabase()
    redirect_to = f"{settings.frontend_url.rstrip('/')}/login?verified=1"

    try:
        response = supabase.auth.sign_up(
            {
                "email": body.email,
                "password": body.password,
                "options": {
                    "data": {
                        "full_name": body.full_name,
                        "username": body.username,
                        "zip_code": body.zip_code or "",
                        "phone": body.phone or "",
                    },
                    "email_redirect_to": redirect_to,
                },
            }
        )
    except Exception as e:
        err_msg = str(e)
        logger.warning("Signup Supabase error: %s", err_msg, exc_info=True)
        err = err_msg.lower()
        if "already registered" in err or "already exists" in err or "user already" in err:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already registered",
            )
        if "rate limit" in err or "rate_limit" in err:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many signup attempts. Please wait a few minutes or try a different email.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=err_msg or "Signup failed",
        )

    # Create profile row so signup data appears in public.profiles
    user = getattr(response, "user", None)
    if user:
        _upsert_profile_on_signup(
            user, body.full_name, body.username,
            zip_code=body.zip_code, phone=body.phone,
        )

    # Supabase may return error in response instead of raising
    if user and getattr(response, "session", None) is None:
        # Email confirmation required – session is null until verified
        return SignupSuccessResponse()

    if user:
        return SignupSuccessResponse()

    # No user in response – log and return a clear error
    logger.warning("Signup returned no user. response keys: %s", getattr(response, "__dict__", response))
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Signup failed. Please try again.",
    )


def _is_email_not_verified_error(err: str) -> bool:
    err_lower = err.lower()
    return (
        "email not confirmed" in err_lower
        or "not confirmed" in err_lower
        or "confirm your email" in err_lower
        or "email_not_confirmed" in err_lower
        or "verify your email" in err_lower
        or "verification" in err_lower
    )


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest):
    """Sign in with email and password. Returns tokens only for verified users."""
    supabase = get_supabase()

    try:
        response = supabase.auth.sign_in_with_password(
            {"email": body.email, "password": body.password}
        )
    except Exception as e:
        # Supabase/GoTrue can return error code "email_not_confirmed" for existing but unverified users
        code = getattr(e, "code", None)
        if code == "email_not_confirmed":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Please verify your email before logging in. Check your inbox or resend the verification email.",
                    "code": "email_not_verified",
                },
            )
        err = str(e).lower()
        if getattr(e, "message", None):
            err = err + " " + str(getattr(e, "message", "")).lower()
        if _is_email_not_verified_error(err):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "message": "Please verify your email before logging in. Check your inbox or resend the verification email.",
                    "code": "email_not_verified",
                },
            )
        if "invalid" in err or "invalid_credentials" in err or "invalid login" in err:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session = getattr(response, "session", None)
    user = getattr(response, "user", None)

    if not session or not user:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "message": "Please verify your email before logging in. Check your inbox or resend the verification email.",
                "code": "email_not_verified",
            },
        )

    access_token = getattr(session, "access_token", None) or (session.get("access_token") if isinstance(session, dict) else None)
    refresh_token = getattr(session, "refresh_token", None) or (session.get("refresh_token") if isinstance(session, dict) else None)

    # Prefer profile data for display fields
    profile = None
    admin = get_supabase_admin()
    if admin:
        profile = _fetch_profile_by_id(admin, str(user.id if not isinstance(user, dict) else user.get("id")))

    return TokenResponse(
        access_token=access_token or "",
        refresh_token=refresh_token,
        user=_user_response_from_profile_or_meta(user, profile),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest):
    """Exchange a refresh token for new access token and user. Keeps session alive without re-login."""
    data = _refresh_supabase_session(body.refresh_token)
    if not data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Supabase returns: access_token, refresh_token (new one), token_type, user (with user_metadata), etc.
    access_token = data.get("access_token") or ""
    new_refresh = data.get("refresh_token")
    user_data = data.get("user")
    if not user_data or not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh response",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Prefer profile row for display data
    profile = None
    admin = get_supabase_admin()
    if admin and user_data and user_data.get("id"):
        profile = _fetch_profile_by_id(admin, str(user_data["id"]))
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        user=_user_response_from_profile_or_meta(user_data, profile),
    )


@router.post("/resend-verification", response_model=ResendVerificationResponse)
async def resend_verification(body: ResendVerificationRequest):
    """Resend the signup confirmation email for an unverified user."""
    supabase = get_supabase()
    redirect_to = f"{settings.frontend_url.rstrip('/')}/login?verified=1"

    try:
        supabase.auth.resend(
            {
                "type": "signup",
                "email": body.email,
                "options": {"email_redirect_to": redirect_to},
            }
        )
    except Exception as e:
        err = str(e).lower()
        if "rate limit" in err or "already" in err:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Please wait a few minutes before requesting another email.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not send verification email. Please try again later.",
        )

    return ResendVerificationResponse()


def _refresh_supabase_session(refresh_token: str) -> dict | None:
    """Exchange refresh_token for new access/refresh tokens and user. Returns None on failure."""
    url = settings.supabase_url.rstrip("/") + "/auth/v1/token?grant_type=refresh_token"
    body = json.dumps({"refresh_token": refresh_token}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "apikey": settings.supabase_anon_key,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.load(resp)
            return data
    except urllib.error.HTTPError as e:
        if e.code in (401, 400, 403):
            return None
        raise
    except Exception:
        return None


def _get_user_from_supabase_token(access_token: str) -> dict | None:
    """Validate token with Supabase Auth and return user dict or None."""
    url = settings.supabase_url.rstrip("/") + "/auth/v1/user"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "apikey": settings.supabase_anon_key,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return None
        raise
    except Exception:
        return None


def get_current_user_data(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> dict:
    """Dependency: return raw user dict from Bearer token. Use in other routers for auth."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_data = _get_user_from_supabase_token(credentials.credentials)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_data


def get_current_user_and_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(security)],
) -> tuple[dict, str]:
    """Dependency: return (user_data, access_token) for use when calling Supabase as the user (no service role)."""
    if not credentials or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_data = _get_user_from_supabase_token(credentials.credentials)
    if not user_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_data, credentials.credentials


def _fetch_profile_by_id(admin, uid: str) -> dict | None:
    """Fetch one profile by ID using admin client."""
    try:
        r = admin.table("profiles").select("*").eq("id", uid).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None


@router.get("/me", response_model=UserResponse)
async def get_me(user_data: Annotated[dict, Depends(get_current_user_data)]):
    """Return current user profile from public.profiles; fall back to auth metadata if missing."""
    uid = user_data.get("id")
    admin = get_supabase_admin()
    if uid and admin:
        prof = _fetch_profile_by_id(admin, str(uid))
        if prof:
            return _user_response_from_profile_or_meta(user_data, prof)
    
    # Fallback to metadata
    return _user_to_response(user_data)


@router.get("/profile/{user_id}", response_model=UserResponse)
async def get_profile(user_id: str):
    """Fetch a user profile by its ID (public access)."""
    admin = get_supabase_admin()
    if not admin:
        raise HTTPException(status_code=500, detail="Supabase admin not configured")
    
    prof = _fetch_profile_by_id(admin, user_id)
    if not prof:
        raise HTTPException(status_code=404, detail="User not found")
    stats = _get_user_stats(admin, user_id)

    # We don't have the full user_data (auth meta) here easily if we only have the profile,
    # but we can build a UserResponse from the profile.
    return UserResponse(
        id=str(prof["id"]),
        email=prof.get("email") or "",
        full_name=prof.get("full_name") or "",
        username=prof.get("username") or "",
        bio=prof.get("bio") or None,
        avatar_url=prof.get("avatar_url") or None,
        contributions_count=stats["contributions_count"],
        collaborations_count=stats["collaborations_count"],
    )

@router.put("/profile", response_model=UserResponse)
async def update_profile(
    body: ProfileUpdateRequest,
    user_data: dict = Depends(get_current_user_data),
):
    """Update profile details (full_name, bio, avatar_url) for the logged in user."""
    uid = user_data.get("id")
    admin = get_supabase_admin()
    if not admin or not uid:
        raise HTTPException(status_code=500, detail="Configuration error")
    
    update_data = {}
    if body.full_name is not None:
        update_data["full_name"] = body.full_name
    if body.bio is not None:
        update_data["bio"] = body.bio
    if body.avatar_url is not None:
        update_data["avatar_url"] = body.avatar_url

    if not update_data:
        # Nothing to update, just fetch and return
        prof = _fetch_profile_by_id(admin, str(uid))
        return _user_response_from_profile_or_meta(user_data, prof)

    try:
        res = admin.table("profiles").update(update_data).eq("id", str(uid)).execute()
        updated_prof = res.data[0] if res.data else None
    except Exception as e:
        logger.error("Failed to update profile for %s: %s", uid, e)
        raise HTTPException(status_code=400, detail="Failed to update profile")

    return _user_response_from_profile_or_meta(user_data, updated_prof)

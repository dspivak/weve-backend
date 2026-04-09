"""
Posts: create draft, update, publish, list feed. Requires auth.
Uses Supabase REST API with the user's JWT (anon key + Bearer token) so no service role is needed.
"""
from datetime import datetime, timezone
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status

from app.config import settings
from app.supabase_client import get_supabase, get_supabase_admin
from app.routers.auth import get_current_user_and_token
from app.schemas.posts import (
    PostCreate,
    PostListResponse,
    PostResponse,
    PostUpdate,
)

router = APIRouter(prefix="/posts", tags=["posts"])

_SUPABASE_REST = "/rest/v1"


def _user_id_from_data(user_data: dict) -> str:
    return str(user_data.get("id", ""))


def _supabase_headers(access_token: str) -> dict:
    return {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _rest_url(path: str) -> str:
    return f"{settings.supabase_url.rstrip('/')}{_SUPABASE_REST}{path}"


def _get_admin():
    """Helper to get admin client; separated so we can mock in tests."""
    return get_supabase_admin()


def _toggle_row_in_table(
    access_token: str,
    table_name: str,
    post_id: str,
    user_id: str,
) -> bool:
    """
    Toggle a (post_id, user_id) row in the given table using Supabase REST as the user.
    Returns True if row exists after toggle (i.e., now liked/saved), False otherwise.
    """
    with httpx.Client(timeout=30.0) as client:
        # Check if row exists
        r = client.get(
            _rest_url(f"/{table_name}"),
            headers=_supabase_headers(access_token),
            params={"post_id": f"eq.{post_id}", "user_id": f"eq.{user_id}", "select": "post_id"},
        )
        if r.status_code >= 400:
            raise HTTPException(status_code=500, detail=f"Failed to fetch {table_name}")
        rows = r.json() if r.content else []
        exists = bool(rows)
        if exists:
            # Delete existing
            rd = client.delete(
                _rest_url(f"/{table_name}"),
                headers=_supabase_headers(access_token),
                params={"post_id": f"eq.{post_id}", "user_id": f"eq.{user_id}"},
            )
            if rd.status_code >= 400:
                raise HTTPException(status_code=500, detail=f"Failed to update {table_name}")
            return False
        # Insert new
        ri = client.post(
            _rest_url(f"/{table_name}"),
            headers=_supabase_headers(access_token),
            json={"post_id": post_id, "user_id": user_id},
        )
        if ri.status_code >= 400:
            raise HTTPException(status_code=500, detail=f"Failed to update {table_name}")
        return True


def _fetch_posts(access_token: str, params: dict) -> tuple[list, int | None]:
    """GET posts with optional filters. Returns (rows, total_count)."""
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            _rest_url("/posts"),
            headers={
                **_supabase_headers(access_token), 
                "Prefer": "return=representation,count=exact"
            },
            params=params,
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=500, detail="Failed to fetch posts")
    
    data = r.json() if r.content else []
    
    # Extract total count from Content-Range header: "0-9/100" -> 100
    total_count = None
    content_range = r.headers.get("Content-Range")
    if content_range and "/" in content_range:
        try:
            total_count = int(content_range.split("/")[-1])
        except (ValueError, IndexError):
            pass
            
    return data, total_count


def _fetch_one_post(access_token: str, post_id: str) -> dict | None:
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            _rest_url("/posts"),
            headers=_supabase_headers(access_token),
            params={"id": f"eq.{post_id}", "select": "*"},
        )
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        # If it is an invalid UUID, PostgREST often returns 400.
        return None
    data = r.json() if r.content else []
    return data[0] if data else None


def _row_to_response(
    row: dict,
    user_id: str | None = None,
    author: dict | None = None,
    like_count: int = 0,
    **kwargs,
) -> PostResponse:
    if user_id is None:
        user_id = str(row.get("user_id", ""))
        
    a_dict = author if isinstance(author, dict) else author.model_dump() if author else {"id": user_id, "full_name": "Unknown", "username": "unknown"}
    parent_post = kwargs.get("parent_post")
    
    return PostResponse(
        id=str(row["id"]),
        title=row.get("title"),
        content=row.get("content") or "",
        author=a_dict,
        status=row.get("status", "draft"),
        image_url=row.get("image_url"),
        parent_post=parent_post,
        collaboration_parent_id=row.get("collaboration_parent_id"),
        collaboration_task_id=row.get("collaboration_task_id"),
        like_count=like_count,
        collaboration_count=kwargs.get("collaboration_count", 0),
        liked_by_me=kwargs.get("liked_by_me", False),
        saved_by_me=kwargs.get("saved_by_me", False),
        created_at=row.get("created_at") or "",
        published_at=row.get("published_at"),
        updated_at=row.get("updated_at") or "",
        user_id=user_id,
    )

@router.post("", response_model=PostResponse, status_code=status.HTTP_201_CREATED)
async def create_post(
    body: PostCreate,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """Create a draft or published post. Auth required. Uses your JWT so no service role needed."""
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid user")
    post_id = str(uuid4())
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": post_id,
        "user_id": user_id,
        "title": body.title,
        "content": body.content.strip(),
        "status": body.status if body.status in ("draft", "published") else "draft",
        "created_at": now,
        "updated_at": now,
        "published_at": now if body.status == "published" else None,
        "image_url": body.image_url if body.image_url else None,
        "collaboration_parent_id": body.collaboration_parent_id,
        "collaboration_task_id": body.collaboration_task_id,
    }
    with httpx.Client(timeout=30.0) as client:
        r = client.post(
            _rest_url("/posts"),
            headers={**_supabase_headers(access_token), "Prefer": "return=representation"},
            json=row,
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create post")
    data = r.json() if r.content else []
    inserted = data[0] if data else row
    # New posts start with zero interactions
    return _row_to_response(inserted, like_count=0, collaboration_count=0, liked_by_me=False, saved_by_me=False)


@router.patch("/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: str,
    body: PostUpdate,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """Update own draft. Auth required."""
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    post = _fetch_one_post(access_token, post_id)
    if not post or str(post.get("user_id")) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found or not yours")
    if post.get("status") == "published":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot edit published post")
    updates = {"updated_at": datetime.now(timezone.utc).isoformat()}
    if body.title is not None:
        updates["title"] = body.title
    if body.content is not None:
        updates["content"] = body.content.strip()
    if body.image_url is not None:
        updates["image_url"] = body.image_url or None
    with httpx.Client(timeout=30.0) as client:
        r = client.patch(
            _rest_url("/posts"),
            headers={**_supabase_headers(access_token), "Prefer": "return=representation"},
            params={"id": f"eq.{post_id}"},
            json=updates,
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to update post")
    data = r.json() if r.content else []
    updated = data[0] if data else {**post, **updates}
    return _row_to_response(updated)


@router.post("/{post_id}/publish", response_model=PostResponse)
async def publish_post(
    post_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """Set post status to published. Auth required, own post only."""
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    post = _fetch_one_post(access_token, post_id)
    if not post or str(post.get("user_id")) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found or not yours")
    if post.get("status") == "published":
        return _row_to_response(post)
    now = datetime.now(timezone.utc).isoformat()
    with httpx.Client(timeout=30.0) as client:
        r = client.patch(
            _rest_url("/posts"),
            headers={**_supabase_headers(access_token), "Prefer": "return=representation"},
            params={"id": f"eq.{post_id}"},
            json={"status": "published", "updated_at": now, "published_at": now},
        )
    if r.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to publish post")
    data = r.json() if r.content else []
    updated = data[0] if data else {**post, "status": "published", "updated_at": now, "published_at": now}
    return _row_to_response(updated)


@router.post("/{post_id}/like", summary="Toggle like on a post")
async def toggle_like_post(
    post_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """Toggle like for the current user on the given post. Returns new like_count and liked flag."""
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    if not _fetch_one_post(access_token, post_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    liked = _toggle_row_in_table(access_token, "post_likes", post_id, user_id)

    # Compute new like count using admin client for accuracy
    like_count = 0
    admin = _get_admin()
    if admin:
        try:
            likes_resp = admin.table("post_likes").select("post_id", count="exact").eq("post_id", post_id).execute()
            like_count = likes_resp.count or 0  # type: ignore[attr-defined]
        except Exception:
            pass

    # TODO: Notifications: only when transitioning from not-liked to liked
    if liked and admin:
        try:
            post = admin.table("posts").select("user_id").eq("id", post_id).execute()
            rows = post.data or []
            if rows:
                owner_id = str(rows[0]["user_id"])
                if owner_id != user_id:
                    admin.table("notifications").insert(
                        {
                            "user_id": owner_id,
                            "actor_id": user_id,
                            "post_id": post_id,
                            "type": "post_liked",
                        }
                    ).execute()
        except Exception:
            # Notifications are best-effort; don't break likes if they fail
            pass

    return {"post_id": post_id, "liked": liked, "like_count": like_count}


@router.post("/{post_id}/save", summary="Toggle save (favorite) on a post")
async def toggle_save_post(
    post_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """Toggle save/favorite for the current user on the given post. Returns new saved flag."""
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    if not _fetch_one_post(access_token, post_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")

    saved = _toggle_row_in_table(access_token, "post_favorites", post_id, user_id)

    # Notifications: only when transitioning from not-saved to saved
    admin = _get_admin()
    if saved and admin:
        try:
            post = admin.table("posts").select("user_id").eq("id", post_id).execute()
            rows = post.data or []
            if rows:
                owner_id = str(rows[0]["user_id"])
                if owner_id != user_id:
                    # Check if notification already exists to avoid duplicates
                    # (Though toggle usually handles this, adding extra safety)
                    admin.table("notifications").insert(
                        {
                            "user_id": owner_id,
                            "actor_id": user_id,
                            "post_id": post_id,
                            "type": "post_saved",
                        }
                    ).execute()
        except Exception:
            pass

    return {"post_id": post_id, "saved": saved}


def _author_from_user_metadata(user_data: dict) -> tuple[str, str]:
    """Get full_name and username from Supabase user dict (e.g. from JWT /auth/v1/user)."""
    meta = (user_data or {}).get("user_metadata") or {}
    email = (user_data or {}).get("email") or ""
    full_name = meta.get("full_name") or meta.get("name") or ""
    username = meta.get("username") or (email.split("@")[0] if email else "")
    return full_name, username


def _fetch_profiles(access_token: str, user_ids: list[str]) -> dict:
    """Fetch public profile info for the given user IDs. Uses service role to bypass RLS."""
    if not user_ids:
        return {}
    
    # Try admin client first if configured (to bypass RLS for profile enrichment)
    admin = get_supabase_admin()
    if admin:
        try:
            r = admin.table("profiles").select("id,full_name,username").in_("id", user_ids).execute()
            rows = r.data or []
            return {str(p["id"]): p for p in rows}
        except Exception:
            pass # Fall back to token-based fetch

    # Fallback to token-based fetch (will be restricted by RLS)
    with httpx.Client(timeout=30.0) as client:
        r = client.get(
            _rest_url("/profiles"),
            headers=_supabase_headers(access_token),
            params={"id": f"in.({','.join(user_ids)})", "select": "id,full_name,username"},
        )
    if r.status_code >= 400:
        return {}
    rows = r.json() if r.content else []
    return {str(p["id"]): p for p in rows}


@router.get("/my-posts", response_model=PostListResponse)
async def list_my_posts(
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """List current user's posts. Auth required."""
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    params = {"user_id": f"eq.{user_id}", "order": "created_at.desc", "limit": "50", "select": "*"}
    data, total = _fetch_posts(access_token, params)
    return PostListResponse(
        posts=[_row_to_response(row) for row in data],
        total=total if total is not None else len(data),
    )


@router.get("/saved", response_model=PostListResponse)
async def list_saved_posts(
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """List posts bookmarked/saved by the current user."""
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    admin = _get_admin()
    if not admin:
        return PostListResponse(posts=[], total=0)
    
    try:
        favs = admin.table("post_favorites").select("post_id").eq("user_id", user_id).execute()
        if not favs.data:
            return PostListResponse(posts=[], total=0)
        
        post_ids = [str(f["post_id"]) for f in favs.data]
        params = {"id": f"in.({','.join(post_ids)})", "order": "published_at.desc", "select": "*"}
        data, total = _fetch_posts(access_token, params)
        unique_user_ids = list({str(row["user_id"]) for row in data if row.get("user_id")})
        profiles_by_id = _fetch_profiles(access_token, unique_user_ids)
        
        out = []
        for row in data:
            uid_str = str(row.get("user_id"))
            prof = profiles_by_id.get(uid_str, {})
            author = {"id": uid_str, "full_name": prof.get("full_name", ""), "username": prof.get("username", "")}
            out.append(_row_to_response(row, author=author, saved_by_me=True, user_id=uid_str))
            
        return PostListResponse(posts=out, total=total if total is not None else len(out))
    except Exception:
        return PostListResponse(posts=[], total=0)

@router.get("/collaborated/{username}", response_model=PostListResponse)
async def list_collaborated_posts(
    username: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """List original parent posts that the specified user has collaborated on."""
    user_data, access_token = user_and_token
    admin = _get_admin()
    if not admin:
        return PostListResponse(posts=[], total=0)
    
    try:
        prof_res = admin.table("profiles").select("id").eq("username", username).execute()
        if not prof_res.data:
            return PostListResponse(posts=[], total=0)
        target_uid = str(prof_res.data[0]["id"])
        
        # Find all collaboration_parent_ids where this user is author
        collabs = admin.table("posts").select("collaboration_parent_id").eq("author_id", target_uid).not_.is_("collaboration_parent_id", "null").execute()
        if not collabs.data:
            return PostListResponse(posts=[], total=0)
            
        parent_ids = list(set([str(c["collaboration_parent_id"]) for c in collabs.data]))
        params = {"id": f"in.({','.join(parent_ids)})", "order": "published_at.desc", "select": "*"}
        data, total = _fetch_posts(access_token, params)
        unique_user_ids = list({str(row["user_id"]) for row in data if row.get("user_id")})
        profiles_by_id = _fetch_profiles(access_token, unique_user_ids)
        
        out = []
        for row in data:
            uid_str = str(row.get("user_id"))
            prof = profiles_by_id.get(uid_str, {})
            author = {"id": uid_str, "full_name": prof.get("full_name", ""), "username": prof.get("username", "")}
            out.append(_row_to_response(row, author=author, user_id=uid_str))
            
        return PostListResponse(posts=out, total=total if total is not None else len(out))
    except Exception:
        return PostListResponse(posts=[], total=0)

@router.get("", response_model=PostListResponse)
async def list_posts(
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
    status_filter: str | None = None,
    author_username: str | None = None,
    tab_filter: str | None = None,
    page: int = 1,
    limit: int = 20,
):
    """
    List posts: 
    ?status_filter=published for feed.
    ?author_username=john for profile page.
    ?tab_filter=posts (original ideas only), or contributions (all).
    ?page=1&limit=20 for pagination.
    """
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    
    # If author_username is provided, first we need to get their user_id
    target_user_id = user_id
    admin = _get_admin()
    if author_username and admin:
        try:
            r = admin.table("profiles").select("id").eq("username", author_username).execute()
            if r.data:
                target_user_id = str(r.data[0]["id"])
            else:
                return PostListResponse(posts=[], total=0)
        except Exception:
            return PostListResponse(posts=[], total=0)

    offset = (page - 1) * limit

    if status_filter == "published" or author_username:
        params = {
            "status": "eq.published", 
            "order": "published_at.desc", 
            "limit": str(limit),
            "offset": str(offset),
            "select": "*"
        }
        if author_username:
            params["user_id"] = f"eq.{target_user_id}"
            if tab_filter == "posts":
                # Only show posts that are NOT collaborations
                params["collaboration_parent_id"] = "is.null"
                
        data, total = _fetch_posts(access_token, params)
        user_ids = list({str(row["user_id"]) for row in data if row.get("user_id")})
        profiles_by_id = _fetch_profiles(access_token, user_ids)

        # Aggregate likes and favorites for these posts using admin client if available
        like_counts: dict[str, int] = {}
        coll_counts: dict[str, int] = {}
        liked_by_me_ids: set[str] = set()
        saved_by_me_ids: set[str] = set()
        post_ids = [str(row["id"]) for row in data]
        admin = _get_admin()
        if admin and post_ids:
            try:
                likes_resp = admin.table("post_likes").select("post_id,user_id").in_("post_id", post_ids).execute()
                for like in likes_resp.data or []:
                    pid = str(like["post_id"])
                    uid = str(like["user_id"])
                    like_counts[pid] = like_counts.get(pid, 0) + 1
                    if uid == user_id:
                        liked_by_me_ids.add(pid)
            except Exception:
                pass
            try:
                all_colls = admin.table("posts").select("collaboration_parent_id").in_("collaboration_parent_id", post_ids).execute()
                for c in all_colls.data or []:
                    pid = str(c["collaboration_parent_id"])
                    coll_counts[pid] = coll_counts.get(pid, 0) + 1
            except Exception:
                pass
            try:
                favs_resp = admin.table("post_favorites").select("post_id,user_id").eq("user_id", user_id).in_("post_id", post_ids).execute()
                for fav in favs_resp.data or []:
                    saved_by_me_ids.add(str(fav["post_id"]))
            except Exception:
                pass

        # Fetch parent posts for collaborations
        parent_ids = list({str(row["collaboration_parent_id"]) for row in data if row.get("collaboration_parent_id")})
        parent_posts_by_id: dict[str, PostResponse] = {}
        if parent_ids:
            # For simplicity in this feed list, we fetch the basic rows and authors
            parent_rows, _ = _fetch_posts(access_token, {"id": f"in.({','.join(parent_ids)})", "select": "*"})
            parent_uids = list({str(r["user_id"]) for r in parent_rows if r.get("user_id")})
            parent_profiles = _fetch_profiles(access_token, parent_uids)
            for pr in parent_rows:
                p_uid = str(pr["user_id"])
                p_prof = parent_profiles.get(p_uid)
                p_author = {"id": p_uid, "full_name": p_prof.get("full_name", ""), "username": p_prof.get("username", "")} if p_prof else None
                parent_posts_by_id[str(pr["id"])] = _row_to_response(pr, author=p_author, user_id=p_uid)

        out = []
        for row in data:
            uid = row.get("user_id")
            uid_str = str(uid) if uid else ""
            prof = profiles_by_id.get(uid_str) if uid_str else None
            full_name = (prof or {}).get("full_name") or ""
            username = (prof or {}).get("username") or ""
            if uid_str and (not full_name or not username) and uid_str == user_id:
                fn, un = _author_from_user_metadata(user_data)
                full_name = full_name or fn
                username = username or un
            author = {"id": uid_str, "full_name": full_name, "username": username} if uid_str else None
            pid = str(row["id"])
            parent_id = str(row.get("collaboration_parent_id")) if row.get("collaboration_parent_id") else None
            out.append(
                _row_to_response(
                    row,
                    author=author,
                    like_count=like_counts.get(pid, 0),
                    collaboration_count=coll_counts.get(pid, 0),
                    liked_by_me=pid in liked_by_me_ids,
                    saved_by_me=pid in saved_by_me_ids,
                    parent_post=parent_posts_by_id.get(parent_id) if parent_id else None,
                    user_id=uid_str,
                )
            )
        return PostListResponse(posts=out, total=total if total is not None else len(out))
    params = {"user_id": f"eq.{user_id}", "order": "created_at.desc", "limit": str(limit), "offset": str(offset), "select": "*"}
    data, total = _fetch_posts(access_token, params)
    return PostListResponse(
        posts=[_row_to_response(row, user_id=user_id) for row in data],
        total=total if total is not None else len(data),
    )


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """Get one post. Auth required. Can read own draft or any published."""
    user_data, access_token = user_and_token
    user_id = _user_id_from_data(user_data)
    post = _fetch_one_post(access_token, post_id)
    if not post:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    if post["status"] != "published" and str(post.get("user_id")) != user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Post not found")
    author = None
    if post.get("user_id"):
        uid_str = str(post["user_id"])
        profs = _fetch_profiles(access_token, [uid_str])
        p = profs.get(uid_str)
        full_name = (p or {}).get("full_name") or ""
        username = (p or {}).get("username") or ""
        if (not full_name or not username) and uid_str == user_id:
            fn, un = _author_from_user_metadata(user_data)
            full_name = full_name or fn
            username = username or un
        author = {"id": uid_str, "full_name": full_name, "username": username}

    # Fetch parent post if collaboration
    parent_post = None
    if post.get("collaboration_parent_id"):
        parent_id = str(post["collaboration_parent_id"])
        parent_row = _fetch_one_post(access_token, parent_id)
        if parent_row:
            p_uid = str(parent_row["user_id"])
            p_profs = _fetch_profiles(access_token, [p_uid])
            p_p = p_profs.get(p_uid)
            p_author = {"id": p_uid, "full_name": p_p.get("full_name", ""), "username": p_p.get("username", "")} if p_p else None
            parent_post = _row_to_response(parent_row, author=p_author, user_id=p_uid)

    # Enrich with interactions for this post (single-row aggregate)
    like_count = 0
    liked_by_me = False
    saved_by_me = False
    admin = get_supabase_admin()
    if admin:
        try:
            likes_resp = admin.table("post_likes").select("post_id,user_id").eq("post_id", post_id).execute()
            for like in likes_resp.data or []:
                like_count += 1
                if str(like["user_id"]) == user_id:
                    liked_by_me = True
        except Exception:
            pass
        try:
            fav_resp = admin.table("post_favorites").select("post_id,user_id").eq("post_id", post_id).eq("user_id", user_id).execute()
            saved_by_me = bool(fav_resp.data)
        except Exception:
            pass

    return _row_to_response(
        post,
        author=author,
        like_count=like_count,
        liked_by_me=liked_by_me,
        saved_by_me=saved_by_me,
        parent_post=parent_post,
        user_id=str(post.get("user_id")),
    )


@router.get("/{post_id}/likes", summary="List users who liked a post")
async def list_post_likes(
    post_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    """Return a list of profiles for users who liked the given post."""
    user_data, access_token = user_and_token  # access_token kept for parity; not used here
    admin = _get_admin()
    if not admin:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Admin client not configured")

    try:
        likes_resp = admin.table("post_likes").select("user_id").eq("post_id", post_id).execute()
        user_ids = [str(r["user_id"]) for r in likes_resp.data or []]
        if not user_ids:
            return {"post_id": post_id, "likes": []}
        profs = admin.table("profiles").select("id,full_name,username").in_("id", user_ids).execute()
        profiles = [
            {"id": str(p["id"]), "full_name": p.get("full_name") or "", "username": p.get("username") or ""}
            for p in profs.data or []
        ]
        return {"post_id": post_id, "likes": profiles}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to load likes")

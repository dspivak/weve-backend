from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
import httpx

from app.supabase_client import get_supabase_admin
from app.routers.auth import get_current_user_and_token
from app.schemas.notifications import (
    NotificationListResponse,
    NotificationResponse,
    NotificationActor,
    NotificationPost
)

router = APIRouter(prefix="/notifications", tags=["notifications"])

def _get_admin():
    return get_supabase_admin()

@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    user_data, access_token = user_and_token
    user_id = str(user_data.get("id", ""))
    
    admin = _get_admin()
    if not admin:
        raise HTTPException(status_code=503, detail="Admin client not configured")

    try:
        # Fetch total count
        total_res = admin.table("notifications")\
            .select("*", count="exact")\
            .eq("user_id", user_id)\
            .limit(0)\
            .execute()
        total_count = total_res.count or 0
        
        # Fetch unread count
        unread_res = admin.table("notifications")\
            .select("*", count="exact")\
            .eq("user_id", user_id)\
            .eq("is_read", False)\
            .limit(0)\
            .execute()
        unread_count = unread_res.count or 0

        # Fetch notifications for current user
        res = admin.table("notifications")\
            .select("*, actor:profiles!notifications_actor_id_fkey(id, full_name, username), post:posts(id, content)")\
            .eq("user_id", user_id)\
            .order("created_at", desc=True)\
            .limit(50)\
            .execute()
        
        data = res.data or []
        notifications = []
        
        for item in data:
            actor_data = item.get("actor") or {}
            post_data = item.get("post") or {}
            
            notifications.append(NotificationResponse(
                id=str(item["id"]),
                user_id=str(item["user_id"]),
                type=item["type"],
                is_read=item["is_read"],
                created_at=item["created_at"],
                actor=NotificationActor(
                    id=str(actor_data.get("id", "")),
                    full_name=actor_data.get("full_name", "Unknown"),
                    username=actor_data.get("username", "unknown")
                ),
                post=NotificationPost(
                    id=str(post_data.get("id", "")),
                    content=post_data.get("content", "")
                ) if post_data else None
            ))
            
        return NotificationListResponse(
            notifications=notifications,
            total=total_count,
            unread_count=unread_count
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/{notification_id}/read")
async def mark_as_read(
    notification_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    user_data, _ = user_and_token
    user_id = str(user_data.get("id", ""))
    
    admin = _get_admin()
    if not admin:
        raise HTTPException(status_code=503, detail="Admin client not configured")
        
    try:
        admin.table("notifications")\
            .update({"is_read": True})\
            .eq("id", notification_id)\
            .eq("user_id", user_id)\
            .execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/read-all")
async def mark_all_as_read(
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    user_data, _ = user_and_token
    user_id = str(user_data.get("id", ""))
    
    admin = _get_admin()
    if not admin:
        raise HTTPException(status_code=503, detail="Admin client not configured")
        
    try:
        admin.table("notifications")\
            .update({"is_read": True})\
            .eq("user_id", user_id)\
            .eq("is_read", False)\
            .execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{notification_id}")
async def delete_notification(
    notification_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    user_data, _ = user_and_token
    user_id = str(user_data.get("id", ""))
    
    admin = _get_admin()
    if not admin:
        raise HTTPException(status_code=503, detail="Admin client not configured")
        
    try:
        admin.table("notifications")\
            .delete()\
            .eq("id", notification_id)\
            .eq("user_id", user_id)\
            .execute()
        return {"status": "success"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

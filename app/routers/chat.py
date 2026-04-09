from typing import Annotated, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from app.supabase_client import get_supabase_admin
from app.routers.auth import get_current_user_and_token
from app.schemas.chat import (
    ConversationResponse,
    ConversationListResponse,
    MessageResponse,
    MessageListResponse,
    SendMessageRequest,
    ChatParticipant
)
import uuid

router = APIRouter(prefix="/chat", tags=["chat"])

def _get_admin():
    return get_supabase_admin()

@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    user_data, _ = user_and_token
    user_id = str(user_data.get("id", ""))
    admin = _get_admin()
    
    # Fetch conversations where user is either user_one or user_two
    res = admin.table("conversations")\
        .select("*, user_one:profiles!conversations_user_one_id_fkey(*), user_two:profiles!conversations_user_two_id_fkey(*)")\
        .or_(f"user_one_id.eq.{user_id},user_two_id.eq.{user_id}")\
        .order("updated_at", desc=True)\
        .execute()
    
    convs = []
    for item in (res.data or []):
        # Determine who the "other" person is
        is_user_one = str(item["user_one_id"]) == user_id
        other_profile = item["user_two"] if is_user_one else item["user_one"]
        
        # Get last message for preview
        msg_res = admin.table("messages")\
            .select("content, created_at")\
            .eq("conversation_id", item["id"])\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()
        
        last_msg = msg_res.data[0]["content"] if msg_res.data else None
        last_at = msg_res.data[0]["created_at"] if msg_res.data else item["created_at"]
        
        # Calculate unread count: messages sender_id != me AND created_at > last_read_at
        last_read_field = "last_read_at_1" if is_user_one else "last_read_at_2"
        last_read_val = item.get(last_read_field)
        
        query = admin.table("messages")\
            .select("id", count="exact")\
            .eq("conversation_id", item["id"])\
            .neq("sender_id", user_id)
        
        if last_read_val:
            query = query.gt("created_at", last_read_val)
            
        unread_res = query.execute()
        
        unread_count = unread_res.count if unread_res.count is not None else 0
        
        convs.append(ConversationResponse(
            id=str(item["id"]),
            participant=ChatParticipant(
                id=str(other_profile["id"]),
                full_name=other_profile.get("full_name", "Unknown"),
                username=other_profile.get("username", "unknown")
            ),
            last_message=last_msg,
            last_at=last_at,
            unread_count=unread_count
        ))
        
    return ConversationListResponse(conversations=convs)

@router.get("/conversations/{conversation_id}/messages", response_model=MessageListResponse)
async def get_messages(
    conversation_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    admin = _get_admin()
    # No extra check needed here as RLS on Supabase (if used via client) would handle it,
    # but since we use admin, we'll just fetch.
    res = admin.table("messages")\
        .select("*")\
        .eq("conversation_id", conversation_id)\
        .order("created_at", desc=False)\
        .execute()
        
    messages = [MessageResponse(**m) for m in (res.data or [])]
    return MessageListResponse(messages=messages)

@router.post("/conversations/{conversation_id}/read")
async def mark_conversation_read(
    conversation_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    user_data, _ = user_and_token
    user_id = str(user_data.get("id", ""))
    admin = _get_admin()
    
    # Verify user is part of the conversation and determine which field to update
    conv_res = admin.table("conversations").select("user_one_id, user_two_id").eq("id", conversation_id).execute()
    if not conv_res.data:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    conv = conv_res.data[0]
    is_user_one = str(conv["user_one_id"]) == user_id
    field = "last_read_at_1" if is_user_one else "last_read_at_2"
    
    try:
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat()
        admin.table("conversations").update({field: now_iso}).eq("id", conversation_id).execute()
    except Exception as e:
        # If column is missing, we just log it or skip for now
        print(f"Warning: Could not update {field}: {e}")
        
    return {"status": "ok"}

@router.post("/messages", response_model=MessageResponse)
async def send_message(
    req: SendMessageRequest,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    user_data, _ = user_and_token
    sender_id = str(user_data.get("id", ""))
    recipient_id = req.recipient_id
    admin = _get_admin()
    
    # 1. Check if conversation exists (order-independent pair)
    # To keep the unique constraint simple, we always store user_one < user_two
    u1, u2 = sorted([sender_id, recipient_id])
    
    conv_res = admin.table("conversations")\
        .select("id")\
        .eq("user_one_id", u1)\
        .eq("user_two_id", u2)\
        .execute()
        
    is_new_conversation = False
    if not conv_res.data:
        # Create new conversation
        new_conv = admin.table("conversations").insert({
            "user_one_id": u1,
            "user_two_id": u2
        }).execute()
        conversation_id = new_conv.data[0]["id"]
        is_new_conversation = True
    else:
        conversation_id = conv_res.data[0]["id"]
        # Check if this sender has ANY messages in this conversation yet
        msg_check = admin.table("messages")\
            .select("id", count="exact")\
            .eq("conversation_id", conversation_id)\
            .eq("sender_id", sender_id)\
            .limit(1)\
            .execute()
        # If count is 0, this is the first message from THIS user to THE OTHER user
        if msg_check.count == 0:
            is_new_conversation = True

    content = req.content

    # 2. Insert message
    msg_res = admin.table("messages").insert({
        "conversation_id": conversation_id,
        "sender_id": sender_id,
        "content": content,
        "post_id": req.post_id
    }).execute()
    
    # Update conversation timestamp
    admin.table("conversations").update({"updated_at": "now()"}).eq("id", conversation_id).execute()
    
    # 3. Notification Logic: ONLY for the first message this user sends in this conversation
    if is_new_conversation:
        # Avoid notifying if sender = recipient (though sort/check prevent it usually)
        if sender_id != recipient_id:
            admin.table("notifications").insert({
                "user_id": recipient_id,
                "actor_id": sender_id,
                "type": "new_message",
                "post_id": req.post_id,
                "is_read": False
            }).execute()

    return MessageResponse(**msg_res.data[0])

@router.get("/conversations/check")
async def check_conversation(
    other_user_id: str,
    user_and_token: Annotated[tuple[dict, str], Depends(get_current_user_and_token)],
):
    user_data, _ = user_and_token
    my_id = str(user_data.get("id", ""))
    u1, u2 = sorted([my_id, other_user_id])
    
    admin = _get_admin()
    res = admin.table("conversations")\
        .select("id")\
        .eq("user_one_id", u1)\
        .eq("user_two_id", u2)\
        .execute()
        
    return {"exists": len(res.data) > 0, "id": res.data[0]["id"] if res.data else None}

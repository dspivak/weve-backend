from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

class NotificationActor(BaseModel):
    id: str
    full_name: str
    username: str

class NotificationPost(BaseModel):
    id: str
    content: str

class NotificationResponse(BaseModel):
    id: str
    user_id: str
    actor: NotificationActor
    post: Optional[NotificationPost] = None
    type: str # post_liked, post_saved
    is_read: bool
    created_at: datetime

class NotificationListResponse(BaseModel):
    notifications: List[NotificationResponse]
    total: int
    unread_count: int

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel

class ChatParticipant(BaseModel):
    id: str
    full_name: str
    username: str

class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    sender_id: str
    content: str
    post_id: Optional[str] = None
    created_at: datetime

class ConversationResponse(BaseModel):
    id: str
    participant: ChatParticipant
    last_message: Optional[str] = None
    last_at: Optional[datetime] = None
    unread_count: int = 0

class ConversationListResponse(BaseModel):
    conversations: List[ConversationResponse]

class MessageListResponse(BaseModel):
    messages: List[MessageResponse]

class SendMessageRequest(BaseModel):
    recipient_id: str
    content: str
    post_id: Optional[str] = None

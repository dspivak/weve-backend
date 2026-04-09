from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, Field


class PostCreate(BaseModel):
    title: str | None = None
    content: str = Field(..., min_length=1)
    status: str = "draft"  # draft | published
    image_url: str | None = None
    collaboration_parent_id: str | None = None
    collaboration_task_id: str | None = None


class PostUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    image_url: str | None = None


class PostAuthor(BaseModel):
    id: str
    full_name: str
    username: str


class PostResponse(BaseModel):
    id: str
    user_id: str
    title: str | None
    content: str
    status: str
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None
    image_url: str | None = None
    collaboration_parent_id: str | None = None
    collaboration_task_id: str | None = None
    # Aggregated interaction data
    like_count: int = 0
    collaboration_count: int = 0
    liked_by_me: bool = False
    saved_by_me: bool = False
    author: PostAuthor | None = None  # populated when listing feed
    parent_post: PostResponse | None = None


class PostListResponse(BaseModel):
    posts: list[PostResponse]
    total: int | None = None

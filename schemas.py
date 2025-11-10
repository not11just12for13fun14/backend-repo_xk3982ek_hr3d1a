"""
Database Schemas for Vibe Ideas (Product Hunt style)

Each Pydantic model corresponds to a MongoDB collection.
Collection name is the lowercase of the class name.
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional

class Post(BaseModel):
    """
    Ideas posted by users
    Collection: "post"
    """
    title: str = Field(..., min_length=3, max_length=140, description="Idea title")
    description: str = Field(..., min_length=3, max_length=1000, description="Short summary of the idea")
    url: Optional[HttpUrl] = Field(None, description="Optional external link")
    votes_count: int = Field(0, ge=0, description="Total votes for this post")
    comments_count: int = Field(0, ge=0, description="Total comments on this post")

class Comment(BaseModel):
    """
    Comments on posts
    Collection: "comment"
    """
    post_id: str = Field(..., description="ID of the post this comment belongs to")
    author: Optional[str] = Field(None, max_length=80, description="Optional display name")
    content: str = Field(..., min_length=1, max_length=1000, description="Comment text")

class Vote(BaseModel):
    """
    A single IP can vote for ONE post only
    Collection: "vote"
    """
    post_id: str = Field(..., description="ID of the post voted for")
    ip: str = Field(..., description="Voter IP address")

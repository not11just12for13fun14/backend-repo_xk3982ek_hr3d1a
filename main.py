import os
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Literal
from bson import ObjectId
from datetime import datetime, timedelta, timezone

from database import db, create_document, get_documents

app = FastAPI(title="Vibe Ideas API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Helpers

def to_object_id(id_str: str) -> ObjectId:
    try:
        return ObjectId(id_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")


def serialize(doc: dict):
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id"))
    # Convert datetimes to isoformat
    for k, v in list(doc.items()):
        if hasattr(v, "isoformat"):
            doc[k] = v.isoformat()
    return doc


# Schemas for requests
class PostCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=140)
    description: str = Field(..., min_length=3, max_length=1000)
    url: Optional[str] = None

class CommentCreate(BaseModel):
    author: Optional[str] = Field(None, max_length=80)
    content: str = Field(..., min_length=1, max_length=1000)


@app.on_event("startup")
def seed_sample_posts():
    # Seed some example ideas if the collection is empty
    try:
        if db is None:
            return
        if db["post"].count_documents({}) == 0:
            now = datetime.now(timezone.utc)
            samples = [
                {
                    "title": "AI Daily Standup Summarizer",
                    "description": "Bot that auto-joins standups, transcribes, and posts bullet summaries to Slack with action items.",
                    "url": "https://example.com/standup-summarizer",
                    "votes_count": 12,
                    "comments_count": 3,
                    "created_at": now - timedelta(days=2, hours=3),
                    "updated_at": now - timedelta(days=1, hours=2),
                },
                {
                    "title": "Vibe UI Presets",
                    "description": "One-click Tailwind themes (gradients, glass, morph) for rapid prototyping. Copy/paste components.",
                    "url": "https://example.com/vibe-ui",
                    "votes_count": 20,
                    "comments_count": 5,
                    "created_at": now - timedelta(days=1, hours=5),
                    "updated_at": now - timedelta(hours=10),
                },
                {
                    "title": "Prompt-to-Plugin",
                    "description": "Describe a plugin in plain English, get a working scaffold with frontend + FastAPI backend in minutes.",
                    "url": None,
                    "votes_count": 8,
                    "comments_count": 1,
                    "created_at": now - timedelta(days=6),
                    "updated_at": now - timedelta(days=5, hours=8),
                },
                {
                    "title": "Open Source Roadmap Radar",
                    "description": "Track trending OSS issues/PRs, cluster by topic, and suggest good-first-issues personalized to you.",
                    "url": "https://example.com/oss-radar",
                    "votes_count": 15,
                    "comments_count": 4,
                    "created_at": now - timedelta(days=3, hours=6),
                    "updated_at": now - timedelta(days=2),
                },
            ]
            db["post"].insert_many(samples)
    except Exception:
        # Best-effort: don't block app startup if seeding fails
        pass


@app.get("/")
def read_root():
    return {"message": "Vibe Ideas API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"

    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os as _os
    response["database_url"] = "✅ Set" if _os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if _os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


# API Endpoints

@app.post("/api/posts")
def create_post(payload: PostCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    data = payload.model_dump()
    now = datetime.now(timezone.utc)
    data.update({
        "votes_count": 0,
        "comments_count": 0,
        "created_at": now,
        "updated_at": now,
    })
    post_id = db["post"].insert_one(data).inserted_id
    doc = db["post"].find_one({"_id": post_id})
    return serialize(doc)


@app.get("/api/posts")
def list_posts(
    time_range: Literal["week", "month", "all"] = Query("week"),
    sort_by: Literal["votes", "comments", "recent"] = Query("votes"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    query = {}
    now = datetime.now(timezone.utc)
    if time_range == "week":
        query["created_at"] = {"$gte": now - timedelta(days=7)}
    elif time_range == "month":
        query["created_at"] = {"$gte": now - timedelta(days=30)}

    sort_field = {
        "votes": ("votes_count", -1),
        "comments": ("comments_count", -1),
        "recent": ("created_at", -1),
    }[sort_by]

    skip = (page - 1) * page_size

    cursor = db["post"].find(query).sort([sort_field]).skip(skip).limit(page_size)
    items = [serialize(d) for d in cursor]
    total = db["post"].count_documents(query)

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/posts/{post_id}")
def get_post(post_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    doc = db["post"].find_one({"_id": to_object_id(post_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Post not found")
    return serialize(doc)


@app.post("/api/posts/{post_id}/vote")
async def vote_post(post_id: str, request: Request):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    ip = request.client.host if request.client else "unknown"

    # Enforce: single IP can vote for one single post only
    existing_vote = db["vote"].find_one({"ip": ip})
    if existing_vote:
        # If the existing vote is for this post, ignore; otherwise block
        if str(existing_vote.get("post_id")) != post_id:
            raise HTTPException(status_code=400, detail="This IP has already voted for another post")
        else:
            return {"status": "ok", "message": "Already voted for this post"}

    # Record vote and increment
    db["vote"].insert_one({"post_id": post_id, "ip": ip, "created_at": datetime.now(timezone.utc)})
    db["post"].update_one({"_id": to_object_id(post_id)}, {"$inc": {"votes_count": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}})

    doc = db["post"].find_one({"_id": to_object_id(post_id)})
    return serialize(doc)


@app.post("/api/posts/{post_id}/comments")
def add_comment(post_id: str, payload: CommentCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    comment = {
        "post_id": post_id,
        "author": payload.author,
        "content": payload.content,
        "created_at": datetime.now(timezone.utc),
    }
    db["comment"].insert_one(comment)
    db["post"].update_one({"_id": to_object_id(post_id)}, {"$inc": {"comments_count": 1}, "$set": {"updated_at": datetime.now(timezone.utc)}})

    return {"status": "ok"}


@app.get("/api/posts/{post_id}/comments")
def list_comments(post_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    cursor = db["comment"].find({"post_id": post_id}).sort([("created_at", -1)])
    return [serialize(d) for d in cursor]


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

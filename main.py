import os
from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import Optional, Literal, List, Dict
from bson import ObjectId
from datetime import datetime, timedelta, timezone

from database import db

app = FastAPI(title="VibeHunt API")

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
    parent_id: Optional[str] = Field(None, description="Optional parent comment id for threading")


@app.on_event("startup")
def seed_sample_posts_on_empty():
    # Best-effort initial seed if empty
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
                    "comments_count": 0,
                    "created_at": now - timedelta(days=2, hours=3),
                    "updated_at": now - timedelta(days=1, hours=2),
                },
                {
                    "title": "Vibe UI Presets",
                    "description": "One-click Tailwind themes (gradients, glass, morph) for rapid prototyping. Copy/paste components.",
                    "url": "https://example.com/vibe-ui",
                    "votes_count": 20,
                    "comments_count": 0,
                    "created_at": now - timedelta(days=1, hours=5),
                    "updated_at": now - timedelta(hours=10),
                },
            ]
            db["post"].insert_many(samples)
    except Exception:
        pass


@app.get("/")
def read_root():
    return {"message": "VibeHunt API running"}


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
    request: Request,
    time_range: Literal["week", "month", "all"] = Query("week"),
    sort_by: Literal["votes", "comments", "recent"] = Query("votes"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=50),
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    query: Dict = {}
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

    # annotate with whether this IP has voted each item
    ip = request.client.host if request.client else "unknown"
    voted_map = {
        v.get("post_id"): True for v in db["vote"].find({"ip": ip, "post_id": {"$in": [i["id"] for i in items]}})
    }
    for i in items:
        i["voted"] = bool(voted_map.get(i["id"]))

    # Live compute comments_count from comment collection
    if items:
        ids = [i["id"] for i in items]
        pipeline = [
            {"$match": {"post_id": {"$in": ids}}},
            {"$group": {"_id": "$post_id", "count": {"$sum": 1}}}
        ]
        counts = {d["_id"]: d["count"] for d in db["comment"].aggregate(pipeline)}
        for i in items:
            i["comments_count"] = int(counts.get(i["id"], 0))

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.get("/api/posts/{post_id}")
def get_post(post_id: str, request: Request):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    doc = db["post"].find_one({"_id": to_object_id(post_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Post not found")
    item = serialize(doc)
    ip = request.client.host if request.client else "unknown"
    item["voted"] = db["vote"].find_one({"ip": ip, "post_id": post_id}) is not None
    # Live comments_count
    item["comments_count"] = db["comment"].count_documents({"post_id": post_id})
    return item


@app.post("/api/posts/{post_id}/vote")
async def vote_post(post_id: str, request: Request):
    """
    Toggle vote for this post by client IP. One vote per IP per post.
    Returns the updated post and current voted state.
    """
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    ip = request.client.host if request.client else "unknown"

    existing_vote = db["vote"].find_one({"ip": ip, "post_id": post_id})
    now = datetime.now(timezone.utc)

    if existing_vote:
        # Unvote
        db["vote"].delete_one({"_id": existing_vote["_id"]})
        db["post"].update_one({"_id": to_object_id(post_id)}, {"$inc": {"votes_count": -1}, "$set": {"updated_at": now}})
        status = "unvoted"
        voted = False
    else:
        # Cast vote
        db["vote"].insert_one({"post_id": post_id, "ip": ip, "created_at": now})
        db["post"].update_one({"_id": to_object_id(post_id)}, {"$inc": {"votes_count": 1}, "$set": {"updated_at": now}})
        status = "voted"
        voted = True

    doc = db["post"].find_one({"_id": to_object_id(post_id)})
    item = serialize(doc)
    item["voted"] = voted
    item["status"] = status
    # Live comments_count
    item["comments_count"] = db["comment"].count_documents({"post_id": post_id})
    return item


@app.post("/api/posts/{post_id}/comments")
def add_comment(post_id: str, payload: CommentCreate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    parent_id = payload.parent_id
    if parent_id:
        # Validate parent exists and belongs to same post
        parent = db["comment"].find_one({"_id": to_object_id(parent_id)})
        if not parent or parent.get("post_id") != post_id:
            raise HTTPException(status_code=400, detail="Invalid parent comment")

    comment = {
        "post_id": post_id,
        "author": payload.author,
        "content": payload.content,
        "parent_id": parent_id,
        "created_at": datetime.now(timezone.utc),
    }
    db["comment"].insert_one(comment)
    # we no longer rely on stored comments_count for accuracy
    db["post"].update_one({"_id": to_object_id(post_id)}, {"$set": {"updated_at": datetime.now(timezone.utc)}})

    return {"status": "ok"}


@app.get("/api/posts/{post_id}/comments")
def list_comments(post_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    cursor = db["comment"].find({"post_id": post_id}).sort([( "created_at", -1)])
    return [serialize(d) for d in cursor]


@app.post("/seed")
def reseed():
    """
    Reseed the database content without dropping collections/schemas.
    - Clears documents in post, comment, vote collections
    - Inserts money-making vibe-coding ideas
    - Adds threaded comments under the first two posts
    """
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    # Delete only documents, not collections or schema
    for col in ("post", "comment", "vote"):
        db[col].delete_many({})

    now = datetime.now(timezone.utc)

    posts = [
        {
            "title": "Micro-SaaS: Notion-to-SOP Generator",
            "description": "Turn messy Notion pages into step-by-step SOPs with AI. Export to PDF, share links, and track views. $19/mo per workspace.",
            "url": "https://vibehunt.dev/notion-sop",
            "votes_count": 0,
            "comments_count": 0,
            "created_at": now - timedelta(days=1, hours=2),
            "updated_at": now - timedelta(days=1, hours=2),
        },
        {
            "title": "Cold DM Personalizer for X/LinkedIn",
            "description": "Paste a lead list, get hyper-personalized DMs with tone presets. Auto A/B test openers. Pay per credit.",
            "url": "https://vibehunt.dev/dm-personalizer",
            "votes_count": 0,
            "comments_count": 0,
            "created_at": now - timedelta(hours=10),
            "updated_at": now - timedelta(hours=10),
        },
        {
            "title": "Churn Radar for Stripe",
            "description": "Daily digest of at-risk users with suggested saves. One-click Playbooks via email and in-app. $49/mo.",
            "url": "https://vibehunt.dev/churn-radar",
            "votes_count": 0,
            "comments_count": 0,
            "created_at": now - timedelta(days=2),
            "updated_at": now - timedelta(days=2),
        },
        {
            "title": "Figma-to-React Glass UI Kit",
            "description": "Import a Figma link and get production React components with Tailwind glassmorphism. $99 one-time, updates included.",
            "url": "https://vibehunt.dev/glass-kit",
            "votes_count": 0,
            "comments_count": 0,
            "created_at": now - timedelta(days=3),
            "updated_at": now - timedelta(days=3),
        },
        {
            "title": "Podcast to Blog Auto-Repurposer",
            "description": "Upload audio → chapters, quotes, SEO blog, and newsletter draft. Integrations for Substack and Ghost. $29/mo.",
            "url": "https://vibehunt.dev/pod-repurpose",
            "votes_count": 0,
            "comments_count": 0,
            "created_at": now - timedelta(days=1, hours=8),
            "updated_at": now - timedelta(days=1, hours=8),
        },
        {
            "title": "Tweet-to-Carousel Maker",
            "description": "Turn top tweets into swipeable LinkedIn/IG carousels with on-brand templates. Credit-based pricing.",
            "url": "https://vibehunt.dev/carousel-maker",
            "votes_count": 0,
            "comments_count": 0,
            "created_at": now - timedelta(hours=20),
            "updated_at": now - timedelta(hours=20),
        },
        {
            "title": "Affiliate Finder for Creators",
            "description": "Paste your product URL, get a ranked list of creators likely to convert + outreach scripts. $39/mo.",
            "url": "https://vibehunt.dev/affiliate-finder",
            "votes_count": 0,
            "comments_count": 0,
            "created_at": now - timedelta(days=4),
            "updated_at": now - timedelta(days=4),
        },
        {
            "title": "Launch Page Optimizer",
            "description": "Upload your landing page, get heatmap predictions and headline variants to boost CVR. $19/mo starter.",
            "url": "https://vibehunt.dev/launch-optimizer",
            "votes_count": 0,
            "comments_count": 0,
            "created_at": now - timedelta(days=2, hours=12),
            "updated_at": now - timedelta(days=2, hours=12),
        },
    ]

    result = db["post"].insert_many(posts)
    post_ids = result.inserted_ids
    p1, p2 = str(post_ids[0]), str(post_ids[1])

    # Threaded comments: root and replies
    c1_id = db["comment"].insert_one({
        "post_id": p1,
        "author": "Maya",
        "content": "This scratches a real itch. Consultants will pay. Bundle with templates.",
        "parent_id": None,
        "created_at": now - timedelta(hours=8),
    }).inserted_id

    db["comment"].insert_many([
        {
            "post_id": p1,
            "author": "Leo",
            "content": "+1. Add Chrome capture to auto-grab screenshots into steps.",
            "parent_id": str(c1_id),
            "created_at": now - timedelta(hours=7, minutes=20),
        },
        {
            "post_id": p1,
            "author": "Ava",
            "content": "Pricing idea: $19 solo / $49 team. Bundle export branding.",
            "parent_id": str(c1_id),
            "created_at": now - timedelta(hours=6, minutes=45),
        },
    ])

    c2_id = db["comment"].insert_one({
        "post_id": p2,
        "author": "Noah",
        "content": "Cold DMs work when ultra-personalized. Needs live social proof + rotate angles.",
        "parent_id": None,
        "created_at": now - timedelta(hours=5),
    }).inserted_id

    db["comment"].insert_many([
        {
            "post_id": p2,
            "author": "Zoe",
            "content": "Let users import a CSV and detect company tech stack for better hooks.",
            "parent_id": str(c2_id),
            "created_at": now - timedelta(hours=4, minutes=30),
        },
        {
            "post_id": p2,
            "author": "Kai",
            "content": "Offer a \"done-for-you\" upsell: $299 set up with copy review.",
            "parent_id": str(c2_id),
            "created_at": now - timedelta(hours=3, minutes=15),
        },
    ])

    # Seed some votes to make the list interesting
    votes = []
    for ip_last in range(1, 8):
        votes.append({"post_id": p1, "ip": f"10.0.0.{ip_last}", "created_at": now - timedelta(hours=ip_last)})
    for ip_last in range(1, 5):
        votes.append({"post_id": p2, "ip": f"10.0.1.{ip_last}", "created_at": now - timedelta(hours=ip_last)})
    for ip_last in range(1, 3):
        votes.append({"post_id": str(post_ids[2]), "ip": f"10.0.2.{ip_last}", "created_at": now - timedelta(hours=ip_last)})

    if votes:
        db["vote"].insert_many(votes)

    # Update votes_count to match current vote docs
    vote_counts = {}
    for v in db["vote"].aggregate([
        {"$group": {"_id": "$post_id", "count": {"$sum": 1}}}
    ]):
        vote_counts[v["_id"]] = v["count"]
    for pid in [str(_id) for _id in post_ids]:
        db["post"].update_one({"_id": to_object_id(pid)}, {"$set": {"votes_count": int(vote_counts.get(pid, 0)), "updated_at": datetime.now(timezone.utc)}})

    return {"status": "ok", "posts": len(post_ids)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)

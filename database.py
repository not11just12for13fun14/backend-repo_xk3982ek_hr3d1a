"""
Database Helper Functions

MongoDB helper functions with graceful fallback.
- Primary: real MongoDB via DATABASE_URL + DATABASE_NAME
- Fallback: Mongita (embedded MongoDB-compatible) so the app fully works without external DB
"""

from datetime import datetime, timezone
from typing import Union
import os

from pydantic import BaseModel
from dotenv import load_dotenv

# Load environment variables from .env file (noop if not present)
load_dotenv()

# Try real MongoDB first
_db = None
_client = None

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except Exception:  # pragma: no cover
    MongoClient = None  # type: ignore
    PyMongoError = Exception  # type: ignore

database_url = os.getenv("DATABASE_URL")
database_name = os.getenv("DATABASE_NAME")

if database_url and database_name and MongoClient is not None:
    try:
        _client = MongoClient(database_url, serverSelectionTimeoutMS=2000)
        _client.admin.command("ping")  # ensure reachable now
        _db = _client[database_name]
    except PyMongoError:
        _client = None
        _db = None

# Fallback to Mongita (embedded) if real DB isn't configured/reachable
if _db is None:
    try:
        from mongita import MongitaClientDisk  # type: ignore
        _client = MongitaClientDisk()
        _db = _client[database_name or "vibehunt_local"]
    except Exception:
        _client = None
        _db = None

# Export name expected by application

db = _db


def create_document(collection_name: str, data: Union[BaseModel, dict]):
    """Insert a single document with timestamps"""
    if db is None:
        raise Exception("Database not available. Check DATABASE_URL and DATABASE_NAME environment variables.")

    if isinstance(data, BaseModel):
        data_dict = data.model_dump()
    else:
        data_dict = dict(data)

    now = datetime.now(timezone.utc)
    data_dict['created_at'] = now
    data_dict['updated_at'] = now

    result = db[collection_name].insert_one(data_dict)
    return str(result.inserted_id)


def get_documents(collection_name: str, filter_dict: dict | None = None, limit: int | None = None):
    """Get documents from a collection"""
    if db is None:
        raise Exception("Database not available. Check DATABASE_URL and DATABASE_NAME environment variables.")

    cursor = db[collection_name].find(filter_dict or {})
    if limit:
        cursor = cursor.limit(limit)
    return list(cursor)

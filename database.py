"""All MongoDB work: cache, search_history and commission_log collections."""
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient, DESCENDING
from pymongo.errors import PyMongoError

import config


class DatabaseError(Exception):
    """Raised when MongoDB cannot be reached or a query fails."""


_client = None


def get_db():
    global _client
    if _client is None:
        _client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=3000)
    return _client[config.DB_NAME]


def _now():
    return datetime.now(timezone.utc)


def _as_utc(value):
    """Mongo returns naive datetimes; treat them as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def normalise(term):
    return " ".join(term.lower().split())


def get_cached(term, ignore_age=False):
    """Return the cached product list if it is still fresh, otherwise None."""
    try:
        doc = get_db().search_cache.find_one({"search_term": normalise(term)})
    except PyMongoError as exc:
        raise DatabaseError(f"MongoDB error: {exc}") from exc
    if not doc or not doc.get("exchange_rate"):
        return None
    if doc.get("sample") and not ignore_age:
        return None                       # sample data is never reused as a cached result
    if ignore_age:
        return doc["products"]
    age = _now() - _as_utc(doc["cached_at"])
    if age > timedelta(minutes=config.CACHE_MINUTES):
        return None
    return doc["products"]


def save_cache(term, products, rate=None, sample=False):
    try:
        get_db().search_cache.update_one(
            {"search_term": normalise(term)},
            {"$set": {"search_term": normalise(term),
                      "cached_at": _now(),
                      "exchange_rate": rate,
                      "sample": sample,
                      "products": products}},
            upsert=True,
        )
    except PyMongoError as exc:
        raise DatabaseError(f"MongoDB error: {exc}") from exc


def log_search(term, result_count, source):
    """Every search (cached or live) goes into search_history."""
    try:
        get_db().search_history.insert_one({
            "search_term": term,
            "timestamp": _now(),
            "number_of_results": result_count,
            "source": source,                 # "cache" or "live"
        })
    except PyMongoError as exc:
        raise DatabaseError(f"MongoDB error: {exc}") from exc


def log_commission(term, products, rate):
    """Save the generated product list with commission and totals (RWF + USD)."""
    try:
        get_db().commission_log.insert_one({
            "search_term": term,
            "timestamp": _now(),
            "exchange_rate": rate,
            "number_of_products": len(products),
            "total_commission_rwf": round(sum(p["commission_fee"] for p in products), 2),
            "total_commission_usd": round(sum(p["commission_usd"] for p in products), 2),
            "products": [{
                "product_name": p["product_name"],
                "product_price": p["product_price"],
                "commission_fee": p["commission_fee"],
                "total_price": p["total_price"],
                "currency": "RWF",
            } for p in products],
        })
    except PyMongoError as exc:
        raise DatabaseError(f"MongoDB error: {exc}") from exc


def recent_searches(limit=8):
    try:
        rows = list(get_db().search_history.find(
            {}, {"_id": 0}).sort("timestamp", DESCENDING).limit(limit))
    except PyMongoError:
        return []
    for row in rows:                                   # show Rwanda time (CAT = UTC+2)
        row["time_local"] = (_as_utc(row["timestamp"]) + timedelta(hours=2)
                             ).strftime("%d %b %Y, %H:%M")
    return rows


def get_stats():
    """Numbers for the dashboard: commission earned, searches, cache hits, top searches."""
    stats = {"total_commission_rwf": 0.0, "searches": 0, "cache_hits": 0, "top": []}
    try:
        db = get_db()
        agg = list(db.commission_log.aggregate(
            [{"$group": {"_id": None, "t": {"$sum": "$total_commission_rwf"}}}]))
        stats["total_commission_rwf"] = round(agg[0]["t"], 2) if agg else 0.0
        stats["searches"] = db.search_history.count_documents({})
        stats["cache_hits"] = db.search_history.count_documents({"source": "cache"})
        top = db.search_history.aggregate([
            {"$group": {"_id": {"$toLower": "$search_term"}, "n": {"$sum": 1}}},
            {"$sort": {"n": -1}}, {"$limit": 5}])
        stats["top"] = [{"term": t["_id"], "count": t["n"]} for t in top]
    except PyMongoError:
        pass
    return stats
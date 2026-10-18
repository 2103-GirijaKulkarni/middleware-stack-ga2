from collections import defaultdict, deque
import base64
import json
import time
import uuid

from fastapi import FastAPI, Request, Query, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import Any, Dict, Optional

YOUR_EMAIL = "23f3000791@ds.study.iitm.ac.in"

ALLOWED_ORIGINS = [
    "https://app-w33sty.example.com",
    "https://exam.sanand.workers.dev",
]

# Change this only if your assignment gives a different total.
TOTAL_ORDERS = 43

RATE_LIMIT = 9
WINDOW_SECONDS = 10

app = FastAPI()

request_counts = defaultdict(deque)
idempotency_store = {}


# -------------------------
# Rate limiting
# -------------------------

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id")

    if client_id is None:
        return await call_next(request)

    now = time.time()
    bucket = request_counts[client_id]

    while bucket and now - bucket[0] >= WINDOW_SECONDS:
        bucket.popleft()

    if len(bucket) >= RATE_LIMIT:
        oldest = bucket[0]
        retry_after = max(1, int(WINDOW_SECONDS - (now - oldest)) + 1)

        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit exceeded"},
            headers={"Retry-After": str(retry_after)},
        )

    bucket.append(now)
    return await call_next(request)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


# -------------------------
# CORS: must be added last
# -------------------------

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "Idempotency-Key",
        "X-Request-ID",
        "X-Client-Id",
    ],
    expose_headers=["Retry-After", "X-Request-ID"],
)


# -------------------------
# Ping endpoint
# -------------------------

@app.get("/ping")
async def ping(request: Request):
    return {
        "email": YOUR_EMAIL,
        "request_id": request.state.request_id,
    }


# -------------------------
# 1. Idempotent POST /orders
# -------------------------

@app.post("/orders", status_code=201)
async def create_order(
    request: Request,
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    if not idempotency_key:
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key header is required",
        )

    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    try:
        body: Dict[str, Any] = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        body = {}

    order_id = str(uuid.uuid4())

    response = {
        "id": order_id,
        "item": body.get("item", "default-item"),
        "quantity": body.get("quantity", 1),
        "status": "created",
    }

    idempotency_store[idempotency_key] = response
    return response


# -------------------------
# 2. Cursor pagination
# -------------------------

def encode_cursor(last_id: int) -> str:
    raw = json.dumps({"last_id": last_id}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def decode_cursor(cursor: str) -> int:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode())
        data = json.loads(raw)
        return int(data["last_id"])
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid cursor")


@app.get("/orders")
async def list_orders(
    request: Request,
    limit: int = Query(default=10, ge=1, le=100),
    cursor: Optional[str] = Query(default=None),
):
    start = 1

    if cursor:
        start = decode_cursor(cursor) + 1

    end = min(start + limit - 1, TOTAL_ORDERS)

    items = [
        {
            "id": order_id,
            "item": f"Order {order_id}",
            "status": "available",
        }
        for order_id in range(start, end + 1)
    ]

    next_cursor = None
    if end < TOTAL_ORDERS:
        next_cursor = encode_cursor(end)

    return {
        "items": items,
        "next_cursor": next_cursor,
    }

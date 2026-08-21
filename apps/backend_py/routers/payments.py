"""
Payments router — Razorpay integration.

Routes (prefixed /api/v1 in main.py):
  POST /payments/create-order  → create Razorpay order, persist Payment record
  POST /payments/verify        → verify HMAC signature, credit user account
"""
import time

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from config import get_settings
from deps import DB, CurrentUser
from models import Payment, User
from services.razorpay import create_order as razorpay_create_order, verify_signature

router = APIRouter(tags=["payments"])
settings = get_settings()

PRICING_PLANS: list[dict] = [
    {"id": "free",      "name": "Free",         "credits": 50,    "price": 0,    "popular": False},
    {"id": "starter",   "name": "Starter",       "credits": 500,   "price": 499,  "popular": True},
    {"id": "pro",       "name": "Professional",  "credits": 2000,  "price": 1499, "popular": False},
    {"id": "unlimited", "name": "Unlimited",     "credits": -1,    "price": 2999, "popular": False},
]


def _find_plan(tier: str) -> dict | None:
    return next((p for p in PRICING_PLANS if p["id"] == tier), None)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/payments/create-order")
async def create_order(request: Request, user: CurrentUser, db: DB):
    body = await request.json()
    tier = body.get("tier", "")
    plan = _find_plan(tier)

    if (
        not plan
        or plan["price"] == 0
        or not settings.razorpay_key_id
        or not settings.razorpay_key_secret
    ):
        raise HTTPException(
            status_code=400, detail="Invalid plan or payments not configured"
        )

    try:
        order = await razorpay_create_order(
            amount_paise=plan["price"] * 100,
            receipt=f"{user.id}_{int(time.time() * 1000)}",
            notes={"userId": user.id, "tier": plan["id"], "credits": plan["credits"]},
        )
    except RuntimeError:
        raise HTTPException(status_code=502, detail="Failed to create Razorpay order")

    payment = Payment(
        user_id=user.id,
        razorpay_id=order["id"],
        amount=plan["price"],
        credits=plan["credits"],
        tier=plan["id"],
    )
    db.add(payment)
    await db.commit()

    return {
        "orderId": order["id"],
        "amount": plan["price"] * 100,
        "key": settings.razorpay_key_id,
    }


@router.post("/payments/verify")
async def verify_payment(request: Request, user: CurrentUser, db: DB):
    body = await request.json()
    order_id = body.get("razorpay_order_id")
    payment_id = body.get("razorpay_payment_id")
    signature = body.get("razorpay_signature")

    if not order_id or not payment_id or not signature:
        raise HTTPException(
            status_code=400, detail="Missing payment verification fields"
        )

    result = await db.execute(select(Payment).where(Payment.razorpay_id == order_id))
    payment = result.scalar_one_or_none()
    if not payment or payment.user_id != user.id:
        raise HTTPException(status_code=404, detail="Payment not found")

    if not verify_signature(order_id, payment_id, signature):
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Mark payment captured
    payment.status = "captured"
    await db.flush()

    # Update user credits / unlimited flag
    user_result = await db.execute(select(User).where(User.id == user.id))
    db_user = user_result.scalar_one()

    plan = _find_plan(payment.tier)
    if plan and plan["id"] == "unlimited":
        db_user.is_unlimited = True
    elif plan and plan["credits"] > 0:
        db_user.credits += plan["credits"]

    await db.commit()

    return {
        "ok": True,
        "credits": plan["credits"] if plan else 0,
        "isUnlimited": (plan["id"] == "unlimited") if plan else False,
    }

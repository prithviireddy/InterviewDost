"""
Razorpay service — create orders and verify payment signatures.
"""
import base64
import hashlib
import hmac

import httpx

from config import get_settings

settings = get_settings()


async def create_order(amount_paise: int, receipt: str, notes: dict) -> dict:
    """
    Create a Razorpay order.  Returns the parsed order JSON.
    Raises RuntimeError if the Razorpay API call fails.
    """
    auth = base64.b64encode(
        f"{settings.razorpay_key_id}:{settings.razorpay_key_secret}".encode()
    ).decode()

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            "https://api.razorpay.com/v1/orders",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Basic {auth}",
            },
            json={
                "amount": amount_paise,
                "currency": "INR",
                "receipt": receipt,
                "notes": notes,
            },
        )

    if not resp.is_success:
        raise RuntimeError(f"Razorpay API error ({resp.status_code}): {resp.text[:200]}")

    return resp.json()


def verify_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """
    Verify a Razorpay payment signature using HMAC-SHA256.
    Returns True if the signature matches; False otherwise.
    """
    expected = hmac.new(
        settings.razorpay_key_secret.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)

"""
Parallel idempotency check against the running Docker stack.

Sends 20 requests at once with the same Idempotency-Key through nginx
(2 api replicas behind it). Duplicates that arrive while the first request is
still running get 409 + Retry-After, so each request retries like a real client.
Money must move exactly once.

Requires: `docker compose up -d --build` (api x2, nginx, postgres all running).
Run:      python check_concurrent.py
"""
import asyncio
import uuid
from collections import Counter

import httpx

BASE = "http://localhost:8000"   # nginx, not an api container directly
REQUESTS = 20
MAX_ATTEMPTS = 5


async def send_with_retry(c, body, key):
    """Return (status of the first attempt, final response)."""
    first_status = None
    for _ in range(MAX_ATTEMPTS):
        r = await c.post("/transfers", json=body, headers={"Idempotency-Key": key})
        if first_status is None:
            first_status = r.status_code
        if r.status_code != 409:
            return first_status, r
        await asyncio.sleep(float(r.headers.get("Retry-After", "1")))
    return first_status, r


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        a = (await c.post("/accounts", json={"name": "RaceA"})).json()["id"]
        b = (await c.post("/accounts", json={"name": "RaceB"})).json()["id"]
        await c.post(f"/accounts/{a}/deposit", json={"amount": 1000})

        key = str(uuid.uuid4())
        body = {"from_account_id": a, "to_account_id": b, "amount": 100}

        results = await asyncio.gather(*[
            send_with_retry(c, body, key) for _ in range(REQUESTS)
        ])

        first = Counter(s for s, _ in results)
        final = Counter(r.status_code for _, r in results)
        ids = {r.json().get("id") for _, r in results}
        bal_a = (await c.get(f"/accounts/{a}")).json()["balance"]
        bal_b = (await c.get(f"/accounts/{b}")).json()["balance"]

        print("first attempt statuses:", dict(first))
        print("final statuses:        ", dict(final))
        print("transfer ids:", ids)
        print("A balance:", bal_a, "| B balance:", bal_b)

        assert set(first) <= {200, 201, 409}, "unexpected status on the first attempt"
        assert first[201] == 1, "exactly one request may create the transfer"
        assert final == {201: 1, 200: REQUESTS - 1}, "after retries: one 201, the rest 200"
        assert len(ids) == 1, "every response must point to the same transfer id"
        assert bal_a == 900 and bal_b == 100, "money must move exactly once"
        print(f"PASS: {REQUESTS} parallel requests, 2 replicas, money moved once")


asyncio.run(main())
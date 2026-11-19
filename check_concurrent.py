"""
Parallel idempotency check against the running Docker stack.

Sends 20 requests at once with the same Idempotency-Key through nginx
(2 api replicas behind it) and checks that money moved exactly once.

Requires: `docker compose up -d` (api x2, nginx, postgres all running).
Run:      python check_concurrent.py
"""
import asyncio
import uuid
from collections import Counter

import httpx

BASE = "http://localhost:8000"   # nginx, not an api container directly
REQUESTS = 20


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as c:
        # setup: two fresh accounts, sender gets 1000
        a = (await c.post("/accounts", json={"name": "RaceA"})).json()["id"]
        b = (await c.post("/accounts", json={"name": "RaceB"})).json()["id"]
        await c.post(f"/accounts/{a}/deposit", json={"amount": 1000})

        # one intent = one key, reused by every "retry"
        key = str(uuid.uuid4())
        body = {"from_account_id": a, "to_account_id": b, "amount": 100}

        # fire all requests at the same time
        responses = await asyncio.gather(*[
            c.post("/transfers", json=body, headers={"Idempotency-Key": key})
            for _ in range(REQUESTS)
        ])

        statuses = Counter(r.status_code for r in responses)
        ids = {r.json().get("id") for r in responses}
        bal_a = (await c.get(f"/accounts/{a}")).json()["balance"]
        bal_b = (await c.get(f"/accounts/{b}")).json()["balance"]

        print("status codes:", dict(statuses))
        print("transfer ids:", ids)
        print("A balance:", bal_a, "| B balance:", bal_b)

        assert statuses == {201: 1, 200: REQUESTS - 1}, \
            "expected exactly one 201 (created) and the rest 200 (duplicate)"
        assert len(ids) == 1, "every response must point to the same transfer id"
        assert bal_a == 900 and bal_b == 100, "money must move exactly once"
        print(f"PASS: {REQUESTS} parallel requests, 2 replicas, money moved once")


asyncio.run(main())
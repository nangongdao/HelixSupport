"""End-to-end SDK example: conversation -> turn -> feedback -> knowledge gap."""

from __future__ import annotations

import os
import sys

from helix_client import HelixClient


def main() -> None:
    base_url = os.getenv("HELIX_BASE_URL", "http://localhost:8000")
    api_key = os.getenv("HELIX_API_KEY")
    if not api_key:
        print("set HELIX_API_KEY to run the example", file=sys.stderr)
        return
    tenant = os.getenv("HELIX_TENANT_ID")

    with HelixClient(base_url=base_url, api_key=api_key, tenant_id=tenant) as client:
        me = client.me()
        print(f"authenticated as {me['actor_id']} ({me['role']})")

        conv = client.create_conversation(
            customer_name="Example Customer", customer_ref="CUST-EX-1"
        )
        print(f"conversation {conv['id']} -> {conv['status']}")

        turn = client.send_message(conv["id"], "ORD-10482 到哪了？", idempotency_key="example-1")
        print(f"assistant: {turn['assistant_message']['content']}")

        assistant_id = turn["assistant_message"]["id"]
        feedback = client.submit_feedback(conv["id"], assistant_id, -1, reason="unclear")
        print(f"feedback {feedback['id']} rating={feedback['rating']}")


if __name__ == "__main__":
    main()

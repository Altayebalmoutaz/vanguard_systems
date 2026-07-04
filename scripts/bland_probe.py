"""One-off probe to inspect the Bland.ai account: pathways + recent calls."""

from __future__ import annotations

import json

import httpx

from app.eligibility.config import get_settings


def main() -> None:
    settings = get_settings()
    key = settings.bland_api_key.strip()
    headers = {"authorization": key}
    base = settings.bland_base_url.rstrip("/")

    pid = settings.bland_pathway_id.strip()
    endpoints = [
        f"{base}/v1/pathway/{pid}",
        f"{base}/v1/convo_pathway/{pid}",
    ]
    with httpx.Client(timeout=30.0) as client:
        for url in endpoints:
            print("\n==", url, "==")
            try:
                resp = client.get(url, headers=headers)
                print("status", resp.status_code)
                text = resp.text
                try:
                    print(json.dumps(resp.json(), indent=2)[:12000])
                except Exception:
                    print(text[:4000])
            except Exception as exc:  # noqa: BLE001
                print("ERR", type(exc).__name__, exc)


if __name__ == "__main__":
    main()

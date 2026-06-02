"""Verify Supabase env vars (root .env + dashboard .env.local). No secrets printed."""
from __future__ import annotations

import base64
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_TABLE = "eligibility_checks"
PROBE_SELECT = "id"


def parse_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def jwt_ref(k: str) -> str | None:
    try:
        payload = k.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data.get("ref") or data.get("project_ref")
    except Exception:
        return None


def ping_table(url: str, key: str) -> tuple[bool, str]:
    if not url or not key:
        return False, "missing url or key"
    req = urllib.request.Request(
        f"{url.rstrip('/')}/rest/v1/{PROBE_TABLE}?select={PROBE_SELECT}&limit=1",
        headers={
            "apikey": key,
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200, f"HTTP {r.status} on {PROBE_TABLE}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason} on {PROBE_TABLE}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def check_block(
    name: str,
    url: str | None,
    anon: str | None,
    service: str | None,
) -> list[str]:
    errors: list[str] = []
    host_ref = None
    if url:
        m = re.search(r"https://([a-z0-9]+)\.supabase\.co", url)
        host_ref = m.group(1) if m else None
        if not host_ref:
            errors.append(f"{name}: URL format invalid")
    else:
        errors.append(f"{name}: URL missing")

    for label, key in [("anon", anon), ("service_role", service)]:
        if not key:
            if label == "service_role":
                continue
            errors.append(f"{name}: {label} key missing")
            continue
        ref = jwt_ref(key)
        if host_ref and ref and ref != host_ref:
            errors.append(f"{name}: {label} JWT ref ({ref}) != URL ref ({host_ref})")
        ok, msg = ping_table(url or "", key)
        print(f"  {name} {label}: {msg} {'OK' if ok else 'FAIL'}")
        if not ok:
            errors.append(f"{name}: {label} failed ({msg})")
    return errors


def main() -> int:
    root = parse_env(ROOT / ".env")
    dash = parse_env(ROOT / "eligibility_dashboard" / ".env.local")

    print("=== Root .env ===")
    root_errors = check_block(
        "root",
        root.get("SUPABASE_URL"),
        root.get("SUPABASE_ANON_KEY"),
        root.get("SUPABASE_SERVICE_ROLE_KEY")
        or root.get("SUPABASE_KEY")
        or root.get("SUPABASE_SERVICE_KEY"),
    )

    print("\n=== eligibility_dashboard/.env.local ===")
    dash_errors = check_block(
        "dashboard",
        dash.get("NEXT_PUBLIC_SUPABASE_URL"),
        dash.get("NEXT_PUBLIC_SUPABASE_ANON_KEY"),
        None,
    )

    if root.get("SUPABASE_URL") and dash.get("NEXT_PUBLIC_SUPABASE_URL"):
        if root["SUPABASE_URL"].rstrip("/") != dash["NEXT_PUBLIC_SUPABASE_URL"].rstrip("/"):
            dash_errors.append("dashboard URL != root SUPABASE_URL")

    all_errors = root_errors + dash_errors
    print()
    if all_errors:
        print("FAILED:")
        for e in all_errors:
            print(" -", e)
        return 1
    print("All Supabase env checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

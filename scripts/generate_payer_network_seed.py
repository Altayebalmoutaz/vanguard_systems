"""Generate supabase migration SQL from Stedi payer CSV (dental-capable rows only)."""
from __future__ import annotations

import csv
import sys
from collections import defaultdict
from pathlib import Path


def esc_sql(s: str) -> str:
    return s.replace("'", "''")


def main() -> int:
    if len(sys.argv) < 3:
        print("Usage: generate_payer_network_seed.py <input.csv> <output.sql>", file=sys.stderr)
        return 2
    csv_path = Path(sys.argv[1])
    out_path = Path(sys.argv[2])

    # primary_payer_id -> display_name (first dental row wins)
    primary_display: dict[str, str] = {}
    # alias_token -> set of primary payer ids that list this token in Aliases
    alias_owners: dict[str, set[str]] = defaultdict(set)

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ct = (row.get("CoverageTypes") or "").lower()
            if "dental" not in ct:
                continue
            pid = (row.get("PrimaryPayerId") or "").strip()
            if not pid:
                continue
            disp = (row.get("DisplayName") or "").strip() or pid
            if pid not in primary_display:
                primary_display[pid] = disp
            aliases_raw = row.get("Aliases") or ""
            for part in aliases_raw.split("|"):
                t = part.strip()
                if t:
                    alias_owners[t].add(pid)

    primary_ids = set(primary_display.keys())

    # Unambiguous alias-only rows: token maps to exactly one primary, and token is not
    # already a different primary id (would collide on payer_id / tpsid semantics).
    alias_rows: list[tuple[str, str]] = []
    for token, owners in alias_owners.items():
        if len(owners) != 1:
            continue
        only_p = next(iter(owners))
        if token == only_p:
            continue
        if token in primary_ids and token != only_p:
            # Token is itself a primary id for another payer; do not add duplicate alias row.
            continue
        if token in primary_ids:
            continue
        alias_rows.append((token, primary_display[only_p]))

    # Stable order: primaries sorted, then aliases sorted
    primary_pairs = sorted(primary_display.items(), key=lambda x: x[0])
    alias_pairs = sorted(alias_rows, key=lambda x: x[0])

    all_rows: list[tuple[str, str]] = primary_pairs + alias_pairs

    lines = [
        "-- Seed payer_network from Stedi payer directory export (dental-capable payers only).",
        "-- Primary rows: CoverageTypes contains 'dental', keyed by PrimaryPayerId.",
        "-- Extra rows: unambiguous Aliases (single owner) where alias != PrimaryPayerId,",
        "-- so Layer 1 accepts alternate Stedi IDs (e.g. 84103 -> Anthem BCBS CA primary 040).",
        "",
        "insert into public.payer_network (payer_id, trading_partner_service_id, display_name, coverage_type)",
        "values",
    ]
    vals = []
    for pid, disp in all_rows:
        e_pid = esc_sql(pid)
        e_disp = esc_sql(disp)
        vals.append(f"  ('{e_pid}', '{e_pid}', '{e_disp}', 'dental')")
    lines.append(",\n".join(vals))
    lines.append("on conflict (payer_id) do update set")
    lines.append("  trading_partner_service_id = excluded.trading_partner_service_id,")
    lines.append("  display_name = excluded.display_name,")
    lines.append("  coverage_type = excluded.coverage_type;")
    lines.append("")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(
        f"wrote {len(primary_pairs)} primary + {len(alias_pairs)} alias = {len(all_rows)} rows -> {out_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

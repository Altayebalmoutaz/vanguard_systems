"""One-time (re-runnable) import of OpenDental procedure catalog TreatArea.

Pulls ``GET /procedurecodes`` from the OpenDental Local API, stores it in
``analytics.od_procedurecode_catalog`` (a code dictionary, no PHI), then sets
``requires_tooth`` / ``requires_surfaces`` on ``analytics.cdt_codes`` from the
authoritative TreatArea (falling back to CDT code-range rules for codes missing
from the OD catalog) and syncs the flags to ``public.cdt_codes``.

``requires_radiograph`` is left to the code-range hint from migration 060 — it is
a payer documentation policy, not intrinsic to the code.

Usage:
    python -m scripts.import_od_treatarea            # apply
    python -m scripts.import_od_treatarea --dry-run  # report only
"""

from __future__ import annotations

import argparse

from app.coding.cdt_requirements import code_range_requirements, treat_area_to_flags
from app.config import get_settings as get_app_settings
from app.db.connection import database_connection
from app.eligibility.config import get_settings as get_elig_settings
from app.integrations.opendental import OpenDentalClient

_SPOT_CHECKS: dict[str, tuple[bool, bool]] = {
    # code: (requires_tooth, requires_surfaces)
    "D2740": (True, False),  # crown -> tooth only
    "D2392": (True, True),  # 2-surface posterior composite
    "D0120": (False, False),  # periodic exam
    "D4341": (True, False),  # SRP (per plan spot-check)
}


def _fetch_catalog() -> dict[str, str | int | None]:
    """proc_code -> TreatArea from the OD Local API; {} if OD is unavailable."""
    try:
        client = OpenDentalClient.from_settings(get_elig_settings())
    except Exception as exc:  # missing keys / config
        print(
            f"OpenDental client unavailable ({type(exc).__name__}: {exc}); "
            "falling back to CDT code-range rules only."
        )
        return {}
    rows = client.get_procedure_catalog()
    catalog: dict[str, str | int | None] = {}
    for row in rows:
        code = (row.ProcCode or "").upper().strip()
        if code:
            catalog[code] = row.TreatArea
    print(f"OpenDental catalog: {len(catalog)} procedure codes.")
    return catalog


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    catalog = _fetch_catalog()
    settings = get_app_settings()

    updated = 0
    from_od = 0
    with database_connection(settings, bypass_rls=True) as conn, conn.cursor() as cur:
        # Persist the catalog for provenance/audit.
        for code, treat_area in catalog.items():
            cur.execute(
                """
                    insert into analytics.od_procedurecode_catalog
                        (proc_code, treat_area, imported_at)
                    values (%s, %s, now())
                    on conflict (proc_code) do update
                        set treat_area = excluded.treat_area,
                            imported_at = now()
                    """,
                (code, None if treat_area is None else str(treat_area)),
            )

        cur.execute("select code from analytics.cdt_codes")
        codes = [str(r[0]).upper().strip() for r in cur.fetchall()]

        for code in codes:
            treat_area = catalog.get(code)
            flags = treat_area_to_flags(treat_area)
            if flags is not None:
                requires_tooth, requires_surfaces = flags
                from_od += 1
            else:
                req = code_range_requirements(code)
                requires_tooth, requires_surfaces = (
                    req.requires_tooth,
                    req.requires_surfaces,
                )
            cur.execute(
                """
                    update analytics.cdt_codes
                    set requires_tooth = %s, requires_surfaces = %s
                    where code = %s
                    """,
                (requires_tooth, requires_surfaces, code),
            )
            updated += cur.rowcount or 0

        # Sync flags analytics -> public (mirror; preserves vector index).
        cur.execute(
            """
                update public.cdt_codes p
                set requires_tooth = a.requires_tooth,
                    requires_surfaces = a.requires_surfaces
                from analytics.cdt_codes a
                where a.code = p.code
                """
        )

        # Validate spot-checks before committing.
        failures: list[str] = []
        for code, (exp_t, exp_s) in _SPOT_CHECKS.items():
            cur.execute(
                "select requires_tooth, requires_surfaces from analytics.cdt_codes where code = %s",
                (code,),
            )
            row = cur.fetchone()
            if row is None:
                continue  # code not in this reference set
            if (bool(row[0]), bool(row[1])) != (exp_t, exp_s):
                failures.append(
                    f"{code}: expected tooth={exp_t}, surf={exp_s}; got "
                    f"tooth={row[0]}, surf={row[1]}"
                )

        if failures:
            conn.rollback()
            print("SPOT-CHECK FAILURES (rolled back):", *failures, sep="\n  ")
            return 1

        if args.dry_run:
            conn.rollback()
            print(
                f"[dry-run] would update {updated} rows ({from_od} from OD TreatArea); rolled back."
            )
            return 0

        conn.commit()

    print(
        f"Updated {updated} cdt_codes rows ({from_od} from OD TreatArea, "
        f"rest via code-range) and synced analytics -> public."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

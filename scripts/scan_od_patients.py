"""Scan OD demo DB for a patient with a usable ElectID payer mapping."""

import httpx

H = {"Authorization": "ODFHIR 0bTpTOz4EhcUUqBq/dmaGpgJjyrLNlq0S"}
B = "http://localhost:30222/api/v1"

client = httpx.Client(timeout=15)
patients = client.get(f"{B}/patients", headers=H).json()
print(f"Total patients returned: {len(patients)}")

carrier_cache: dict[int, dict] = {}
with_insurance: list[dict] = []
with_electid: list[dict] = []

for p in patients:
    pat_num = p["PatNum"]
    ins = client.get(f"{B}/familymodules/{pat_num}/Insurance", headers=H).json()
    if not ins:
        continue
    rows_info = []
    has_electid = False
    for r in ins:
        carrier_num = r.get("CarrierNum")
        if carrier_num and carrier_num not in carrier_cache:
            carrier_cache[carrier_num] = client.get(f"{B}/carriers/{carrier_num}", headers=H).json()
        carrier = carrier_cache.get(carrier_num, {})
        electid = (carrier.get("ElectID") or "").strip()
        if electid:
            has_electid = True
        rows_info.append(
            {
                "carrier_num": carrier_num,
                "carrier_name": carrier.get("CarrierName"),
                "elect_id": electid,
                "subscriber_id": r.get("SubscriberID"),
                "ordinal": r.get("Ordinal"),
                "pat_plan_num": r.get("PatPlanNum"),
            }
        )
    entry = {
        "pat_num": pat_num,
        "fname": p["FName"],
        "lname": p["LName"],
        "birthdate": p["Birthdate"],
        "rows": rows_info,
    }
    with_insurance.append(entry)
    if has_electid:
        with_electid.append(entry)

print(f"\nPatients with >=1 insurance row: {len(with_insurance)}")
print(f"Patients with at least one ElectID populated: {len(with_electid)}")

print("\n=== All patients with insurance ===")
for e in with_insurance:
    print(f"\nPatNum {e['pat_num']:>3} {e['fname']} {e['lname']} (DOB {e['birthdate']})")
    for r in e["rows"]:
        print(
            f"  Carrier={r['carrier_num']:<4} "
            f"Name={r['carrier_name']!r:<40} "
            f"ElectID={r['elect_id']!r:<8} "
            f"SubscriberID={r['subscriber_id']!r:<20} "
            f"Ordinal={r['ordinal']} "
            f"PatPlanNum={r['pat_plan_num']}"
        )

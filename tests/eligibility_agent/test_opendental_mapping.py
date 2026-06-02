from __future__ import annotations

import unittest

from app.eligibility.models import TriggerEvent
from app.integrations.opendental.errors import OpenDentalMappingError
from app.integrations.opendental.mapping import od_to_eligibility_request
from app.integrations.opendental.models import ODCarrier, ODInsuranceRow, ODPatient


class TestOpenDentalMapping(unittest.TestCase):
    def test_maps_primary_and_secondary(self) -> None:
        patient = ODPatient(
            PatNum=1, FName="Aardvark", LName="Dent", Birthdate="1970-12-12", SSN="111-22-3333"
        )
        rows = [
            ODInsuranceRow(
                PatPlanNum=101,
                InsSubNum=201,
                PlanNum=301,
                CarrierNum=401,
                SubscriberID="SUB-1",
                Ordinal=1,
            ),
            ODInsuranceRow(
                PatPlanNum=102,
                InsSubNum=202,
                PlanNum=302,
                CarrierNum=402,
                SubscriberID="SUB-2",
                Ordinal=2,
            ),
        ]
        carriers = {
            401: ODCarrier(CarrierNum=401, ElectID="84103"),
            402: ODCarrier(CarrierNum=402, ElectID="52133"),
        }
        mapped = od_to_eligibility_request(
            patient,
            rows,
            carriers,
            trigger_event=TriggerEvent.PRE_APPOINTMENT,
            cdt_codes=["D1110"],
            practice_id=None,
            rendering_provider_npi=None,
        )
        req = mapped.request
        self.assertEqual(mapped.primary_pat_plan_num, 101)
        self.assertEqual(mapped.primary_plan_num, 301)
        self.assertEqual(mapped.primary_ins_sub_num, 201)
        self.assertEqual(req.primary_payer_id, "84103")
        self.assertEqual(req.secondary_payer_id, "52133")
        self.assertEqual(req.subscriber_id, "SUB-1")
        self.assertEqual(req.first_name, "Aardvark")
        self.assertFalse(hasattr(req, "SSN"))

    def test_missing_primary_subscriber_id_raises(self) -> None:
        patient = ODPatient(PatNum=1, FName="Aardvark", LName="Dent", Birthdate="1970-12-12")
        rows = [
            ODInsuranceRow(
                PatPlanNum=101,
                InsSubNum=201,
                PlanNum=301,
                CarrierNum=401,
                SubscriberID="",
                Ordinal=1,
            )
        ]
        carriers = {401: ODCarrier(CarrierNum=401, ElectID="84103")}
        with self.assertRaises(OpenDentalMappingError):
            od_to_eligibility_request(
                patient,
                rows,
                carriers,
                trigger_event=TriggerEvent.PRE_APPOINTMENT,
                cdt_codes=None,
                practice_id=None,
                rendering_provider_npi=None,
            )

    def test_missing_carrier_electid_raises(self) -> None:
        patient = ODPatient(PatNum=1, FName="Aardvark", LName="Dent", Birthdate="1970-12-12")
        rows = [
            ODInsuranceRow(
                PatPlanNum=101,
                InsSubNum=201,
                PlanNum=301,
                CarrierNum=401,
                SubscriberID="SUB-1",
                Ordinal=1,
            )
        ]
        carriers = {401: ODCarrier(CarrierNum=401, ElectID=None)}
        with self.assertRaises(OpenDentalMappingError):
            od_to_eligibility_request(
                patient,
                rows,
                carriers,
                trigger_event=TriggerEvent.PRE_APPOINTMENT,
                cdt_codes=None,
                practice_id=None,
                rendering_provider_npi=None,
            )

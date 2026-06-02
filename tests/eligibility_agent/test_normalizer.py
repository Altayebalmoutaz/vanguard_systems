"""Unit tests for Layer 3 (normalizer) — isolated from HTTP/DB."""

from datetime import UTC, date

import pytest

from app.eligibility.normalizer import normalize


def _base_raw() -> dict:
    return {
        "payer": {"payorIdentification": "TESTPAYER"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "planDetails": "in network preferred"}],
        "benefitsInformation": [
            {
                "code": "1",
                "name": "Active Coverage",
                "benefitAmount": None,
                "procedureCode": "D0120",
            },
            {
                "code": "C",
                "name": "Deductible",
                "benefitAmount": 1500.0,
            },
            {
                "code": "C",
                "name": "Deductible Remaining",
                "benefitAmount": 400.0,
            },
            {
                "code": "F",
                "name": "Annual Maximum",
                "benefitAmount": 2000.0,
            },
            {
                "code": "F",
                "name": "Remaining",
                "benefitAmount": 1200.0,
            },
            {
                "code": "A",
                "name": "Coinsurance",
                "benefitPercent": 0.2,
            },
        ],
    }


def test_normalize_dental_benefit_breakdown_category_coinsurance_and_notes() -> None:
    """Dental RCM-style extras: per-STC coinsurance, ortho max, limitation notes (Telegram mock style)."""
    raw = {
        "payer": {"payorIdentification": "DELTA"},
        "subscriber": {"subscriberStatus": "Active", "firstName": "BOB", "lastName": "SMITH"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {
                "code": "C",
                "name": "Deductible",
                "benefitAmount": "50",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "F",
                "name": "Annual Maximum",
                "benefitAmount": "1500",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "A",
                "name": "Co-Insurance",
                "benefitPercent": "1.0",
                "serviceTypeCodes": ["23"],
                "inPlanNetworkIndicatorCode": "Y",
                "additionalInformation": [{"description": "Frequency: 2 per 12 months"}],
            },
            {
                "code": "A",
                "name": "Co-Insurance",
                "benefitPercent": "0.8",
                "serviceTypeCodes": ["25"],
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "A",
                "name": "Co-Insurance",
                "benefitPercent": "0.5",
                "serviceTypeCodes": ["36"],
                "inPlanNetworkIndicatorCode": "Y",
                "additionalInformation": [{"description": "6 month waiting period applies"}],
            },
            {
                "code": "F",
                "name": "Lifetime Maximum",
                "benefitAmount": "1000",
                "serviceTypeCodes": ["38"],
                "inPlanNetworkIndicatorCode": "Y",
            },
        ],
        "_request_procedure_codes": ["D0120"],
        "_trading_partner_service_id": "DELTA",
    }
    c = normalize(raw, "primary")
    br = c.get("dental_benefit_breakdown") or {}
    by_stc = br.get("coinsurance_patient_pct_by_stc") or {}
    assert by_stc.get("23") == pytest.approx(100.0)
    assert by_stc.get("25") == pytest.approx(80.0)
    assert by_stc.get("36") == pytest.approx(50.0)
    assert br.get("ortho_lifetime_max") == pytest.approx(1000.0)
    notes = br.get("limitation_notes") or []
    assert any("Frequency" in n for n in notes)
    assert any("waiting period" in n.lower() for n in notes)


def test_normalize_stc35_annual_max_not_poisoned_by_other_service_f_rows() -> None:
    """Earlier EB*F rows for non-dental STCs must not set annual_max_total to 0 before STC 35 rows."""
    raw = {
        "payer": {"payorIdentification": "84103"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            # Ortho / other: contract 0 — would poison generic _collect_financials
            {
                "code": "F",
                "name": "Limitations",
                "serviceTypeCodes": ["38"],
                "serviceTypes": ["Orthodontics"],
                "timeQualifierCode": "25",
                "timeQualifier": "Contract",
                "benefitAmount": "0",
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "F",
                "name": "Limitations",
                "serviceTypeCodes": ["35"],
                "serviceTypes": ["Dental Care"],
                "planCoverage": "PRIME35",
                "timeQualifierCode": "25",
                "timeQualifier": "Contract",
                "benefitAmount": "1500",
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "F",
                "name": "Limitations",
                "serviceTypeCodes": ["35"],
                "serviceTypes": ["Dental Care"],
                "planCoverage": "PRIME35",
                "timeQualifierCode": "29",
                "timeQualifier": "Remaining",
                "benefitAmount": "1356",
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "A",
                "name": "Co-Insurance",
                "serviceTypeCodes": ["41"],
                "benefitPercent": "0",
            },
        ],
        "_request_procedure_codes": ["D1110"],
        "_trading_partner_service_id": "84103",
    }
    c = normalize(raw, "primary")
    assert c["annual_max_total"] == pytest.approx(1500.0)
    assert c["annual_max_remaining"] == pytest.approx(1356.0)
    assert "layer3_clamp:annual_max_remaining_capped_to_annual_max_total" not in (
        c.get("normalization_warnings") or []
    )


def test_normalize_happy_path_all_critical_present() -> None:
    raw = _base_raw()
    raw["_request_procedure_codes"] = ["D0120"]
    raw["_has_secondary"] = False
    raw["_secondary_payer_id"] = None
    raw["_trading_partner_service_id"] = "TESTPAYER"
    c = normalize(raw, "primary")
    assert c["payer_id"] == "TESTPAYER"
    assert c["is_active"] is True
    assert c["in_network"] is True
    assert c["deductible_remaining"] is not None
    assert c["annual_max_remaining"] is not None
    assert c["coverage_percent"] == pytest.approx(80.0)
    assert len(c["procedure_details"]) == 1
    assert c["procedure_details"][0]["cdt_code"] == "D0120"


def test_normalize_missing_is_active_inferred_from_plan() -> None:
    raw = _base_raw()
    raw["subscriber"] = {}
    raw["planStatus"] = []
    raw["_request_procedure_codes"] = ["D0120"]
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert c.get("is_active") is None


def test_normalize_conflicting_deductible_sets_warning() -> None:
    raw = _base_raw()
    raw["benefitsInformation"] = [
        {"code": "C", "name": "Deductible", "benefitAmount": 1000.0},
        {"code": "C", "name": "Deductible Met", "benefitAmount": 200.0},
        {"code": "C", "name": "Deductible Remaining", "benefitAmount": 500.0},
        {"code": "F", "name": "Annual Maximum", "benefitAmount": 2000.0},
        {"code": "F", "name": "Remaining", "benefitAmount": 1200.0},
        {"code": "A", "name": "Coinsurance", "benefitPercent": 0.2},
    ]
    raw["_request_procedure_codes"] = ["D0120"]
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert c["deductible_remaining"] == pytest.approx(800.0)
    assert any(
        "deductible_remaining conflict" in w for w in (c.get("normalization_warnings") or [])
    )


def test_waiting_period_category_mapping() -> None:
    raw = _base_raw()
    raw["_request_procedure_codes"] = ["D1110", "D8080", "D4910", "D2750"]
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    by = {p["cdt_code"]: p["waiting_period_category"] for p in c["procedure_details"]}
    assert by["D1110"] == "basic"
    assert by["D8080"] == "ortho"
    assert by["D4910"] == "perio"
    assert by["D2750"] == "major"


def test_checked_at_is_utc() -> None:
    raw = _base_raw()
    raw["_request_procedure_codes"] = []
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert c["checked_at"].tzinfo == UTC


def test_normalize_extracts_payer_aaa_errors_dedupes_payer_and_subscriber() -> None:
    raw = _base_raw()
    dup = {"field": "AAA", "code": "72", "description": "Invalid/Missing Subscriber/Insured ID"}
    raw["errors"] = [dup]
    raw["subscriber"] = {"aaaErrors": [dup]}
    raw["_request_procedure_codes"] = []
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert len(c["payer_aaa_errors"]) == 1
    assert c["payer_aaa_errors"][0]["code"] == "72"
    assert c["payer_aaa_errors"][0]["source"] == "payer"


def test_normalize_zero_deductible_row_sets_remaining_zero() -> None:
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage"}],
        "benefitsInformation": [
            {"code": "C", "name": "Deductible", "benefitAmount": "0"},
            {"code": "A", "name": "Coinsurance", "benefitPercent": 0.2},
        ],
    }
    raw["_request_procedure_codes"] = []
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert c["deductible_total"] == pytest.approx(0.0)
    assert c["deductible_remaining"] == pytest.approx(0.0)


def test_normalize_deductible_remaining_from_unmet_phrase_in_notes() -> None:
    """EB*C rows may carry remaining only in additionalInformation (not payer-specific)."""
    raw = {
        "payer": {"payorIdentification": "PXY"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {"code": "C", "name": "Deductible", "benefitAmount": "50", "serviceTypeCodes": ["35"]},
            {
                "code": "C",
                "name": "Deductible",
                "benefitAmount": "35",
                "serviceTypeCodes": ["35"],
                "additionalInformation": [{"description": "Individual unmet dental deductible"}],
            },
        ],
        "_request_procedure_codes": ["D0120"],
        "_trading_partner_service_id": "PXY",
    }
    c = normalize(raw, "primary")
    assert c["deductible_total"] == pytest.approx(50.0)
    assert c["deductible_remaining"] == pytest.approx(35.0)


def test_normalize_plan_coverage_met_literal_not_deductible_met_amount() -> None:
    """planCoverage ``MET`` is commonly a plan label — do not classify as deductible_met."""
    raw = {
        "payer": {"payorIdentification": "METLIFE_STYLE"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage"}],
        "benefitsInformation": [
            {
                "code": "C",
                "name": "Deductible",
                "benefitAmount": "225",
                "serviceTypeCodes": ["24"],
                "serviceTypes": ["Periodontics"],
                "planCoverage": "MET",
            },
        ],
        "_request_procedure_codes": ["D1110"],
        "_trading_partner_service_id": "METLIFE_STYLE",
    }
    c = normalize(raw, "primary")
    assert c["deductible_total"] == pytest.approx(225.0)
    assert c.get("deductible_met") is None


def test_normalize_infers_procedure_covered_from_plan_stc35() -> None:
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [
            {"status": "Active Coverage", "serviceTypeCodes": ["35"], "planDetails": "dental"}
        ],
        "benefitsInformation": [
            {"code": "1", "name": "Active Coverage", "serviceTypeCodes": ["35"]},
            {"code": "C", "name": "Deductible", "benefitAmount": 0.0},
        ],
    }
    raw["_request_procedure_codes"] = ["D0120"]
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert c["procedure_details"][0]["procedure_covered"] is True
    assert c["is_covered"] is True


def test_normalize_matches_procedure_via_composite_medical_procedure_identifier() -> None:
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "planDetails": "in network"}],
        "benefitsInformation": [
            {
                "code": "N",
                "name": "Not covered",
                "serviceTypeCodes": ["35"],
                "compositeMedicalProcedureIdentifier": {
                    "productOrServiceIDQualifier": "AD",
                    "procedureCode": "D2740",
                },
            },
            {"code": "C", "name": "Deductible", "benefitAmount": 100.0},
        ],
    }
    raw["_request_procedure_codes"] = ["D2740"]
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert c["procedure_details"][0]["procedure_covered"] is False
    assert c["is_covered"] is False


def test_normalize_happy_path_empty_payer_aaa_errors() -> None:
    raw = _base_raw()
    raw["_request_procedure_codes"] = ["D0120"]
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert c["payer_aaa_errors"] == []


def test_dental_calculator_ready_groups_financials_by_network_and_coverage_level() -> None:
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {
                "code": "C",
                "name": "Deductible",
                "benefitAmount": "1000",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "coverageLevelCode": "IND",
                "timeQualifierCode": "23",
            },
            {
                "code": "C",
                "name": "Deductible Remaining",
                "benefitAmount": "250",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "coverageLevelCode": "IND",
                "timeQualifierCode": "29",
            },
            {
                "code": "A",
                "name": "Co-Insurance",
                "benefitPercent": "0.2",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "coverageLevelCode": "IND",
            },
            {
                "code": "B",
                "name": "Co-Payment",
                "benefitAmount": "30",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "N",
                "coverageLevelCode": "FAM",
            },
            {
                "code": "F",
                "name": "Annual Maximum Remaining",
                "benefitAmount": "1200",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "W",
                "coverageLevelCode": "FAM",
                "timeQualifierCode": "29",
            },
        ],
        "_request_procedure_codes": ["D0120"],
        "_trading_partner_service_id": "P",
    }
    c = normalize(raw, "primary")
    buckets = c["dental_calculator_ready"]["network_status"]
    inn = buckets["in_network"]
    assert inn["deductible_total"] == pytest.approx(1000.0)
    assert inn["remaining_deductible"] == pytest.approx(250.0)
    assert inn["coinsurance_percent"] == pytest.approx(20.0)
    assert inn["coverage_levels"] == ["IND"]
    assert "calendar_year" in inn["time_periods"]
    assert "remaining" in inn["time_periods"]
    assert buckets["out_of_network"]["copay_amount"] == pytest.approx(30.0)
    assert buckets["out_of_network"]["coverage_levels"] == ["FAM"]
    assert buckets["both"]["annual_max_remaining"] == pytest.approx(1200.0)


def test_dental_calculator_ready_free_text_overrides_structured_network_and_prior_auth() -> None:
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {
                "code": "B",
                "name": "Co-Payment",
                "benefitAmount": "45",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "N",
                "priorAuthorizationRequired": False,
                "additionalInformation": [
                    {
                        "description": "This benefit is in network. Prior authorization required before service."
                    }
                ],
            }
        ],
        "_request_procedure_codes": ["D2750"],
        "_trading_partner_service_id": "P",
    }
    c = normalize(raw, "primary")
    calc = c["dental_calculator_ready"]
    inn = calc["network_status"]["in_network"]
    assert inn["copay_amount"] == pytest.approx(45.0)
    assert inn["prior_auth_required"] is True
    assert calc["network_status"]["out_of_network"]["copay_amount"] is None
    assert any(o["field"] == "network_status" for o in calc["free_text_overrides"])
    assert any(o["field"] == "prior_auth_required" for o in calc["free_text_overrides"])
    warnings = c.get("normalization_warnings") or []
    assert any("free_text_network_override" in w for w in warnings)
    assert any("free_text_prior_auth_override" in w for w in warnings)


def test_dental_calculator_ready_frequency_latest_visit_carve_out_and_aaa_actions() -> None:
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {
            "subscriberStatus": "Active",
            "aaaErrors": [{"code": "75", "description": "Subscriber Not Found"}],
        },
        "errors": [{"code": "42", "description": "Unable to respond at current time"}],
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {
                "code": "F",
                "name": "Limitations",
                "benefitAmount": "1500",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "benefitsServiceDelivery": [
                    {"quantity": "1", "unit": "visit", "period": "6 months"}
                ],
                "benefitsDateInformation": {"latestVisitOrConsultation": "20240401"},
                "benefitsRelatedEntities": [
                    {
                        "entityIdentifier": "Third-Party Administrator",
                        "entityIdentificationValue": "TPA-MEMBER-123",
                    }
                ],
            }
        ],
        "_request_procedure_codes": ["D0120"],
        "_trading_partner_service_id": "P",
    }
    c = normalize(raw, "primary")
    calc = c["dental_calculator_ready"]
    assert calc["frequency_rules"][0]["description"] == "1 visit 6 months"
    assert calc["latest_visit_or_consultation"][0]["latest_visit_or_consultation"] == date(
        2024, 4, 1
    )
    assert calc["carve_outs"][0]["follow_up_required"] is True
    assert calc["carve_outs"][0]["entity_identification_value"] == "TPA-MEMBER-123"
    actions = {a["code"]: a["action"] for a in calc["aaa_actions"]}
    assert actions["75"] == "verify_subscriber"
    assert actions["42"] == "retry_connectivity"


def test_normalize_preserves_stedi_warnings_and_structured_aaa_actions() -> None:
    raw = _base_raw()
    raw["warnings"] = [
        {
            "code": "request::270::member_id_required",
            "description": "This payer requires the patient's member ID.",
        }
    ]
    raw["provider"] = {
        "aaaErrors": [{"code": "41", "description": "Authorization/Access Restrictions"}]
    }
    raw["_request_procedure_codes"] = []
    raw["_trading_partner_service_id"] = "P"
    c = normalize(raw, "primary")
    assert c["stedi_warnings"][0]["code"] == "request::270::member_id_required"
    actions = {a["code"]: a["action"] for a in c["stedi_aaa_actions"]}
    assert actions["41"] == "enrollment_or_portal_credentials"


def test_auth_or_cert_indicator_y_n_u_maps_prior_auth() -> None:
    """Stedi documents ``authOrCertIndicator`` on benefitsInformation rows."""
    base = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {
                "code": "B",
                "name": "Co-Payment",
                "benefitAmount": "25",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "authOrCertIndicator": "Y",
            }
        ],
        "_request_procedure_codes": ["D0120"],
        "_trading_partner_service_id": "P",
    }
    c_y = normalize(dict(base), "primary")
    inn_y = c_y["dental_calculator_ready"]["network_status"]["in_network"]
    assert inn_y["prior_auth_required"] is True

    base_n = dict(base)
    base_n["benefitsInformation"] = [
        {**base["benefitsInformation"][0], "authOrCertIndicator": "N"},
    ]
    c_n = normalize(base_n, "primary")
    assert (
        c_n["dental_calculator_ready"]["network_status"]["in_network"]["prior_auth_required"]
        is False
    )

    base_u = dict(base)
    base_u["benefitsInformation"] = [
        {**base["benefitsInformation"][0], "authOrCertIndicator": "U"},
    ]
    c_u = normalize(base_u, "primary")
    assert (
        c_u["dental_calculator_ready"]["network_status"]["in_network"]["prior_auth_required"]
        is None
    )


def test_benefit_quantity_and_qualifier_surfaces_as_frequency_rule_without_service_delivery() -> (
    None
):
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {
                "code": "F",
                "name": "Limitations",
                "benefitAmount": "1500",
                "serviceTypeCodes": ["35"],
                "inPlanNetworkIndicatorCode": "Y",
                "benefitQuantity": "2",
                "quantityQualifierCode": "VS",
                "quantityQualifier": "Visits",
            }
        ],
        "_request_procedure_codes": ["D0120"],
        "_trading_partner_service_id": "P",
    }
    c = normalize(raw, "primary")
    rules = c["dental_calculator_ready"]["frequency_rules"]
    assert len(rules) == 1
    assert rules[0]["description"] == "2 Visits (benefit quantity)"
    assert rules[0]["raw"]["benefitQuantity"] == "2"


def test_normalize_splits_eb_f_limitation_from_eb_g_oop_stop_loss() -> None:
    """EB*F (limitation) vs EB*G (out-of-pocket max) must not share annual_max_*."""
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {
                "code": "F",
                "name": "Benefit Limitations",
                "benefitAmount": "1500",
                "serviceTypeCodes": ["35"],
                "timeQualifierCode": "25",
                "timeQualifier": "Contract",
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "F",
                "name": "Benefit Limitations",
                "benefitAmount": "900",
                "serviceTypeCodes": ["35"],
                "timeQualifierCode": "29",
                "timeQualifier": "Remaining",
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "G",
                "name": "Out of Pocket",
                "benefitAmount": "7000",
                "serviceTypeCodes": ["35"],
                "timeQualifierCode": "25",
                "timeQualifier": "Contract",
                "inPlanNetworkIndicatorCode": "Y",
            },
            {
                "code": "G",
                "name": "Out of Pocket",
                "benefitAmount": "6200",
                "serviceTypeCodes": ["35"],
                "timeQualifierCode": "29",
                "timeQualifier": "Remaining",
                "inPlanNetworkIndicatorCode": "Y",
            },
        ],
        "_request_procedure_codes": ["D0120"],
        "_trading_partner_service_id": "P",
    }
    c = normalize(raw, "primary")
    assert c["annual_max_total"] == pytest.approx(1500.0)
    assert c["annual_max_remaining"] == pytest.approx(900.0)
    assert c["out_of_pocket_max_total"] == pytest.approx(7000.0)
    assert c["out_of_pocket_max_remaining"] == pytest.approx(6200.0)


def test_normalize_eb_y_spend_down_and_eb_j_cost_containment() -> None:
    raw = {
        "payer": {"payorIdentification": "P"},
        "subscriber": {"subscriberStatus": "Active"},
        "planStatus": [{"status": "Active Coverage", "serviceTypeCodes": ["35"]}],
        "benefitsInformation": [
            {
                "code": "Y",
                "name": "Spend Down",
                "benefitAmount": "500",
                "serviceTypeCodes": ["35"],
                "timeQualifierCode": "25",
            },
            {
                "code": "Y",
                "name": "Spend Down Remaining",
                "benefitAmount": "120",
                "serviceTypeCodes": ["35"],
                "timeQualifierCode": "29",
            },
            {
                "code": "J",
                "name": "Cost Containment",
                "benefitAmount": "200",
                "serviceTypeCodes": ["35"],
                "timeQualifierCode": "25",
            },
            {
                "code": "J",
                "name": "Cost Containment Remaining",
                "benefitAmount": "45",
                "serviceTypeCodes": ["35"],
                "timeQualifierCode": "29",
            },
        ],
        "_request_procedure_codes": ["D0120"],
        "_trading_partner_service_id": "P",
    }
    c = normalize(raw, "primary")
    assert c["spend_down_total"] == pytest.approx(500.0)
    assert c["spend_down_remaining"] == pytest.approx(120.0)
    assert c["cost_containment_total"] == pytest.approx(200.0)
    assert c["cost_containment_remaining"] == pytest.approx(45.0)
    inn = c["dental_calculator_ready"]["network_status"]["unknown"]
    assert inn["spend_down_total"] == pytest.approx(500.0)
    assert inn["spend_down_remaining"] == pytest.approx(120.0)
    assert inn["cost_containment_total"] == pytest.approx(200.0)
    assert inn["cost_containment_remaining"] == pytest.approx(45.0)


def test_stedi_x12_transaction_kind_271_vs_999() -> None:
    raw = _base_raw()
    raw["_request_procedure_codes"] = []
    raw["_trading_partner_service_id"] = "P"
    raw["x12"] = "~ST*271*0001*005010X279A1~"
    c271 = normalize(dict(raw), "primary")
    assert c271["stedi_x12_transaction_kind"] == "271"
    assert not any(
        "implementation_ack_999" in w for w in (c271.get("normalization_warnings") or [])
    )

    raw999 = dict(raw)
    raw999["x12"] = "~ST*999*0001*005010X231A1~"
    c999 = normalize(raw999, "primary")
    assert c999["stedi_x12_transaction_kind"] == "999"
    assert any(
        "stedi_x12_payload:implementation_ack_999" in w
        for w in (c999.get("normalization_warnings") or [])
    )

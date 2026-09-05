from app.integrations.opendental.onboarding_errors import friendly_opendental_test_error


def test_friendly_econnector_error() -> None:
    out = friendly_opendental_test_error(
        '"The office\'s eConnector is not running. Please contact the office."'
    )
    assert out["code"] == "econnector_down"
    assert out["recovery_step"] == "econnector"


def test_friendly_auth_error() -> None:
    out = friendly_opendental_test_error("401 Not Authorized")
    assert out["code"] == "auth"
    assert out["recovery_step"] == "paste_key"


def test_friendly_timeout_error() -> None:
    out = friendly_opendental_test_error("ConnectError: timed out")
    assert out["code"] == "network"


def test_friendly_gateway_timeout() -> None:
    out = friendly_opendental_test_error(
        "504 Gateway Time-out: The server didn't respond in time."
    )
    assert out["code"] == "econnector_timeout"
    assert out["recovery_step"] == "econnector"


def test_friendly_disabled_by_customer() -> None:
    out = friendly_opendental_test_error('"API key has been disabled by customer."')
    assert out["code"] == "auth"
    assert "disabled" in out["message"].lower()

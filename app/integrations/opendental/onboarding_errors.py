"""Map OpenDental connection-test errors to partner-facing copy."""

from __future__ import annotations


def friendly_opendental_test_error(raw: str | None) -> dict[str, str]:
    """Return ``{code, title, message, recovery_step}`` for the Connect wizard."""
    text = (raw or "").strip().strip('"')
    low = text.lower()

    if "econnector" in low or "e connector" in low:
        return {
            "code": "econnector_down",
            "title": "OpenDental’s bridge isn’t running",
            "message": "The eConnector service on the OpenDental server must be Working (eServices → eConnector Service) before we can connect.",
            "recovery_step": "econnector",
        }
    # HQ accepted the key but the office never answered (504 from api.opendental.com,
    # or our client timed out waiting). Common with multi-database installs where
    # eConnector is still pointed at a different MySQL database.
    if (
        "504" in low
        or "gateway time-out" in low
        or "gateway timeout" in low
        or "readtimeout" in low
        or "the server didn't respond in time" in low
    ):
        return {
            "code": "econnector_timeout",
            "title": "OpenDental’s bridge isn’t answering",
            "message": (
                "Open Dental’s cloud reached your clinic key, but the eConnector "
                "did not respond in time. On a multi-database server, install a "
                "separate eConnector pointed at this SmileSuites database "
                "(Service Manager → database name in OpenDentalWebConfig.xml), "
                "keep that PC awake, then retry."
            ),
            "recovery_step": "econnector",
        }
    if "disabled by customer" in low:
        return {
            "code": "auth",
            "title": "Key is disabled in OpenDental",
            "message": "In Setup → Advanced Setup → API, enable this Customer Key (not Disabled), then retry.",
            "recovery_step": "paste_key",
        }
    if (
        "401" in low
        or "not authorized" in low
        or "unauthorized" in low
        or ("invalid" in low and "key" in low)
    ):
        return {
            "code": "auth",
            "title": "Key not accepted",
            "message": "OpenDental did not accept the Customer Key. Paste it again under Add Key, then retry.",
            "recovery_step": "paste_key",
        }
    if (
        "timeout" in low
        or "timed out" in low
        or "connecterror" in low
        or "connection refused" in low
        or "name or service not known" in low
    ):
        return {
            "code": "network",
            "title": "Can’t reach Open Dental’s cloud",
            "message": "Check the clinic internet connection and keep that computer awake, then try again.",
            "recovery_step": "test",
        }
    if "customer key" in low or "not configured" in low or "missing" in low:
        return {
            "code": "key_missing",
            "title": "Clinic key isn’t ready yet",
            "message": "Your setup contact still needs to finish provisioning. Refresh this page in a few minutes.",
            "recovery_step": "paste_key",
        }
    return {
        "code": "unknown",
        "title": "Connection test failed",
        "message": text[:280] if text else "Something went wrong while testing the connection.",
        "recovery_step": "test",
    }

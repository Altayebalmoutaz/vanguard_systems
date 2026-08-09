"""Safety tests for the OpenDental TreatArea reference import."""

from __future__ import annotations

from unittest.mock import patch

from scripts.import_od_treatarea import main


def test_empty_catalog_aborts_before_database_mutation(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr("sys.argv", ["import_od_treatarea"])

    with (
        patch("scripts.import_od_treatarea._fetch_catalog", return_value={}),
        patch("scripts.import_od_treatarea.get_app_settings") as mock_settings,
        patch("scripts.import_od_treatarea.database_connection") as mock_connection,
    ):
        result = main()

    assert result == 1
    assert "aborting without changing CDT flags" in capsys.readouterr().out
    mock_settings.assert_not_called()
    mock_connection.assert_not_called()

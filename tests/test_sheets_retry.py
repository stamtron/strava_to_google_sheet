"""
Unit tests for Google Sheets API transient retry helper.
"""

from unittest.mock import MagicMock
import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from src.integrations.sheets import execute_with_retry


def test_execute_with_retry_succeeds_first_attempt():
    mock_req = MagicMock()
    mock_req.execute.return_value = {"values": [["test"]]}

    res = execute_with_retry(mock_req, retries=3, delay_sec=0.01)
    assert res == {"values": [["test"]]}
    assert mock_req.execute.call_count == 1


def test_execute_with_retry_recovers_from_503():
    mock_req = MagicMock()
    resp_503 = Response({"status": 503, "reason": "Service Unavailable"})
    err_503 = HttpError(resp_503, b"Service Unavailable")

    success_data = {"values": [["recovered"]]}
    mock_req.execute.side_effect = [err_503, success_data]

    res = execute_with_retry(mock_req, retries=3, delay_sec=0.01)
    assert res == success_data
    assert mock_req.execute.call_count == 2


def test_execute_with_retry_raises_non_transient_error():
    mock_req = MagicMock()
    resp_404 = Response({"status": 404, "reason": "Not Found"})
    err_404 = HttpError(resp_404, b"Not Found")
    mock_req.execute.side_effect = err_404

    with pytest.raises(HttpError):
        execute_with_retry(mock_req, retries=3, delay_sec=0.01)
    assert mock_req.execute.call_count == 1

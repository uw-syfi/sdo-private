"""Tests for the SREGym-local HTTP retry utility."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from benchmarks.sregym.protocol._http import request_with_retry


def _mock_response(status_code: int) -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.HTTPError(response=resp)
    return resp


class TestRequestWithRetry:
    @patch("benchmarks.sregym.protocol._http.time.sleep")
    @patch("benchmarks.sregym.protocol._http.requests.request")
    def test_success_no_retry(self, mock_request, mock_sleep):
        mock_request.return_value = _mock_response(200)
        resp = request_with_retry("GET", "http://example.com/api")
        assert resp.status_code == 200
        mock_sleep.assert_not_called()

    @patch("benchmarks.sregym.protocol._http.time.sleep")
    @patch("benchmarks.sregym.protocol._http.requests.request")
    def test_retries_on_429(self, mock_request, mock_sleep):
        mock_request.side_effect = [
            _mock_response(429),
            _mock_response(429),
            _mock_response(200),
        ]
        resp = request_with_retry("GET", "http://example.com/api", jitter=False)
        assert resp.status_code == 200
        assert mock_sleep.call_count == 2

    @patch("benchmarks.sregym.protocol._http.time.sleep")
    @patch("benchmarks.sregym.protocol._http.requests.request")
    def test_retries_on_500(self, mock_request, mock_sleep):
        mock_request.side_effect = [
            _mock_response(500),
            _mock_response(200),
        ]
        resp = request_with_retry("GET", "http://example.com/api", jitter=False)
        assert resp.status_code == 200
        assert mock_sleep.call_count == 1

    @patch("benchmarks.sregym.protocol._http.time.sleep")
    @patch("benchmarks.sregym.protocol._http.requests.request")
    def test_retries_on_connection_error(self, mock_request, mock_sleep):
        mock_request.side_effect = [
            requests.ConnectionError("connection refused"),
            _mock_response(200),
        ]
        resp = request_with_retry("GET", "http://example.com/api", jitter=False)
        assert resp.status_code == 200
        assert mock_sleep.call_count == 1

    @patch("benchmarks.sregym.protocol._http.time.sleep")
    @patch("benchmarks.sregym.protocol._http.requests.request")
    def test_exhausted_retries_raises(self, mock_request, mock_sleep):
        mock_request.return_value = _mock_response(429)
        with pytest.raises(requests.HTTPError):
            request_with_retry(
                "GET",
                "http://example.com/api",
                max_retries=2,
                jitter=False,
            )
        assert mock_sleep.call_count == 2

    @patch("benchmarks.sregym.protocol._http.time.sleep")
    @patch("benchmarks.sregym.protocol._http.requests.request")
    def test_non_retryable_error_raises_immediately(self, mock_request, mock_sleep):
        mock_request.return_value = _mock_response(404)
        with pytest.raises(requests.HTTPError):
            request_with_retry("GET", "http://example.com/api")
        mock_sleep.assert_not_called()

    @patch("benchmarks.sregym.protocol._http.time.sleep")
    @patch("benchmarks.sregym.protocol._http.requests.request")
    def test_exponential_backoff(self, mock_request, mock_sleep):
        mock_request.side_effect = [
            _mock_response(429),
            _mock_response(429),
            _mock_response(429),
            _mock_response(200),
        ]
        request_with_retry(
            "GET",
            "http://example.com/api",
            initial_delay=1.0,
            backoff_factor=2.0,
            jitter=False,
        )
        delays = [call.args[0] for call in mock_sleep.call_args_list]
        assert delays == [1.0, 2.0, 4.0]

    @patch("benchmarks.sregym.protocol._http.time.sleep")
    @patch("benchmarks.sregym.protocol._http.requests.request")
    def test_connection_error_exhausted_raises(self, mock_request, mock_sleep):
        mock_request.side_effect = requests.ConnectionError("refused")
        with pytest.raises(requests.ConnectionError):
            request_with_retry(
                "GET",
                "http://example.com/api",
                max_retries=2,
                jitter=False,
            )
        assert mock_sleep.call_count == 2

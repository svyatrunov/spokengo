import json

import pytest

from spokengo.errors import (AuthError, BadRequestError, FileTooLargeError,
                             NetworkError, ProviderError, RateLimitError)
from spokengo.transcribe.groq_provider import GroqProvider, KNOWN_MODELS


class Resp:
    def __init__(self, status, body):
        self.status = status
        self.body = body if isinstance(body, bytes) else body.encode()


def make_http(sequence):
    calls = {"n": 0, "headers": None}
    def http(url, headers, fields, file_field, file_path, timeout):
        calls["headers"] = headers
        i = min(calls["n"], len(sequence) - 1)
        calls["n"] += 1
        return sequence[i]
    http.calls = calls
    return http


def provider(http, **kw):
    return GroqProvider("sk-test", http_post=http, sleep=lambda s: None, **kw)


def test_success_returns_text(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    http = make_http([Resp(200, json.dumps({"text": " привет мир "}))])
    tr = provider(http).transcribe(str(f), model="whisper-large-v3-turbo")
    assert tr.text == "привет мир"


def test_known_models_listed():
    assert "whisper-large-v3-turbo" in KNOWN_MODELS
    assert "whisper-large-v3" in KNOWN_MODELS


def test_bad_model_is_permanent(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    body = json.dumps({"error": {"message": "model `nope` not found"}})
    for status in (400, 404, 422):
        http = make_http([Resp(status, body)])
        with pytest.raises(BadRequestError) as ei:
            provider(http).transcribe(str(f), model="nope")
        assert ei.value.transient is False
        assert "nope" in str(ei.value) or "модель" in str(ei.value).lower()


def test_auth_error_permanent(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    http = make_http([Resp(401, "no")])
    with pytest.raises(AuthError) as ei:
        provider(http).transcribe(str(f), model="m")
    assert ei.value.transient is False


def test_file_too_large_permanent(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    http = make_http([Resp(413, "too big")])
    with pytest.raises(FileTooLargeError) as ei:
        provider(http).transcribe(str(f), model="m")
    assert ei.value.transient is False


def test_retry_then_success(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    http = make_http([Resp(429, "slow"), Resp(500, "err"),
                      Resp(200, json.dumps({"text": "ok"}))])
    tr = provider(http, max_retries=3).transcribe(str(f), model="m")
    assert tr.text == "ok"
    assert http.calls["n"] == 3


def test_rate_limit_exhausted_is_transient(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    http = make_http([Resp(429, "no")])
    with pytest.raises(RateLimitError) as ei:
        provider(http, max_retries=2).transcribe(str(f), model="m")
    assert ei.value.transient is True


def test_network_unreachable_is_transient(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    http = make_http([Resp(0, "connection refused")])  # 0 = unreachable sentinel
    with pytest.raises(NetworkError) as ei:
        provider(http, max_retries=2).transcribe(str(f), model="m")
    assert ei.value.transient is True


def test_missing_key_raises():
    with pytest.raises(AuthError):
        GroqProvider("")


def test_sends_custom_user_agent(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    http = make_http([Resp(200, json.dumps({"text": "ok"}))])
    provider(http).transcribe(str(f), model="whisper-large-v3-turbo")
    ua = http.calls["headers"].get("User-Agent", "")
    assert ua.startswith("SpokenGo/")        # NOT the blocked Python-urllib UA
    assert "Authorization" in http.calls["headers"]


def test_403_is_permanent_with_hint(tmp_path):
    f = tmp_path / "a.wav"; f.write_bytes(b"x")
    http = make_http([Resp(403, "error code: 1010")])
    with pytest.raises(ProviderError) as ei:
        provider(http).transcribe(str(f), model="m")
    assert ei.value.transient is False
    assert "403" in str(ei.value)


def test_fail_fast_defaults():
    from spokengo.transcribe.groq_provider import GroqProvider
    p = GroqProvider("sk")
    assert p.timeout <= 20            # short timeout, no minutes-long hang
    assert p.max_retries <= 1         # at most one retry -> errors quickly


# ---------------------------------------------------------------- upload timeout
def _capture_timeout():
    seen = {}

    def http(url, headers, fields, file_field, file_path, timeout):
        seen.setdefault("timeouts", []).append(timeout)
        return Resp(200, json.dumps({"text": "ok"}))
    return http, seen


def test_short_clip_keeps_fail_fast_timeout(tmp_path):
    from spokengo.transcribe.groq_provider import BASE_TIMEOUT
    f = tmp_path / "short.wav"; f.write_bytes(b"\0" * 200_000)       # ~6 s of 16 kHz mono
    http, seen = _capture_timeout()
    provider(http).transcribe(str(f), model="whisper-large-v3-turbo")
    assert BASE_TIMEOUT <= seen["timeouts"][0] <= 20


def test_long_recording_gets_time_to_upload(tmp_path):
    """Regression: an ~18 MB WAV failed with 'The write operation timed out'
    because the whole upload had to fit in a flat 15 s."""
    f = tmp_path / "long.wav"
    with open(f, "wb") as fh:
        fh.truncate(18 * 1024 * 1024)          # sparse file, no 18 MB write
    http, seen = _capture_timeout()
    provider(http).transcribe(str(f), model="whisper-large-v3-turbo")
    t = seen["timeouts"][0]
    # Room for ~1 Mbit/s uplink: 18 MB needs ~150 s of pure transfer.
    assert t >= 150


def test_upload_timeout_scales_and_is_capped():
    from spokengo.transcribe.groq_provider import MAX_TIMEOUT, upload_timeout
    small, mid, huge = (upload_timeout(n) for n in (0, 5_000_000, 500_000_000))
    assert small < mid < huge == MAX_TIMEOUT
    assert upload_timeout(0, base=40.0) == 40.0          # explicit base is respected
    assert upload_timeout(10**9, base=900.0) == 900.0    # never below the base


def test_retry_uses_same_scaled_timeout(tmp_path):
    f = tmp_path / "a.wav"
    with open(f, "wb") as fh:
        fh.truncate(10_000_000)
    timeouts = []

    def http(url, headers, fields, file_field, file_path, timeout):
        timeouts.append(timeout)
        return Resp(0, b"The write operation timed out") if len(timeouts) == 1 \
            else Resp(200, json.dumps({"text": "ok"}))
    assert provider(http).transcribe(str(f), model="m").text == "ok"
    assert len(timeouts) == 2 and timeouts[0] == timeouts[1] > 100

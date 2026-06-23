"""Groq Whisper provider. HTTP via stdlib urllib so there is no hard runtime
dependency and a single function can be monkeypatched in tests.

Error policy (the important part):
  * 200            -> Transcript
  * 400/404/422    -> BadRequestError  (permanent — e.g. unknown model id)
  * 401            -> AuthError         (permanent)
  * 413            -> FileTooLargeError (permanent)
  * 429 / 5xx / 0  -> retry w/ backoff, then RateLimitError / NetworkError
                      (transient — pipeline may queue these)
Status 0 is our sentinel for "could not reach the server" (DNS/timeout/conn).
"""
from __future__ import annotations

import json
import time
from typing import Callable, Optional

from .. import __version__
from ..errors import (AuthError, BadRequestError, FileTooLargeError,
                      NetworkError, ProviderError, RateLimitError)
from .base import Transcript
from .registry import register_provider

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
# A non-default User-Agent is REQUIRED: Groq sits behind Cloudflare, which
# blocks the stdlib default "Python-urllib/x.y" with 403 "error code: 1010".
USER_AGENT = f"SpokenGo/{__version__} (+https://github.com/spokengo)"
# Valid Groq speech-to-text models (docs/speech-to-text). Used to warn early.
KNOWN_MODELS = ("whisper-large-v3-turbo", "whisper-large-v3")


class _Resp:
    def __init__(self, status: int, body: bytes):
        self.status = status
        self.body = body


def _http_post_multipart(url, headers, fields, file_field, file_path,
                         timeout) -> _Resp:  # pragma: no cover - network
    import mimetypes
    import os
    import urllib.error
    import urllib.request

    boundary = "----spokengo" + str(int(time.time() * 1000))
    crlf = b"\r\n"
    body = bytearray()
    for k, v in fields.items():
        body += b"--" + boundary.encode() + crlf
        body += f'Content-Disposition: form-data; name="{k}"'.encode() + crlf + crlf
        body += str(v).encode() + crlf
    fname = os.path.basename(file_path)
    ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    body += b"--" + boundary.encode() + crlf
    body += (f'Content-Disposition: form-data; name="{file_field}"; '
             f'filename="{fname}"').encode() + crlf
    body += f"Content-Type: {ctype}".encode() + crlf + crlf
    with open(file_path, "rb") as f:
        body += f.read()
    body += crlf + b"--" + boundary.encode() + b"--" + crlf

    req = urllib.request.Request(url, data=bytes(body), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return _Resp(r.status, r.read())
    except urllib.error.HTTPError as e:
        return _Resp(e.code, e.read())
    except urllib.error.URLError as e:
        return _Resp(0, str(e.reason).encode())  # unreachable -> transient
    except TimeoutError:
        return _Resp(0, b"timeout")


def _error_message(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8"))
        err = data.get("error") or {}
        if isinstance(err, dict):
            return err.get("message") or str(data)
        return str(err)
    except Exception:
        return body[:200].decode("utf-8", "replace")


@register_provider("groq")
class GroqProvider:
    name = "groq"

    def __init__(self, api_key, *, timeout=120.0, max_retries=3, base_url=GROQ_URL,
                 http_post=_http_post_multipart, sleep: Callable[[float], None] = time.sleep):
        if not api_key:
            raise AuthError("Groq API key is not set")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_url = base_url
        self._http_post = http_post
        self._sleep = sleep

    def transcribe(self, audio_path, *, model="whisper-large-v3-turbo",
                   language: Optional[str] = None) -> Transcript:
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "User-Agent": USER_AGENT,
                   "Accept": "application/json"}
        fields = {"model": model, "response_format": "json", "temperature": "0"}
        if language:
            fields["language"] = language

        attempt = 0
        while True:
            resp = self._http_post(
                self.base_url, headers, fields, "file", audio_path, self.timeout)
            s = resp.status
            if s == 200:
                data = json.loads(resp.body.decode("utf-8"))
                return Transcript(text=data.get("text", "").strip(),
                                  language=data.get("language"), raw=data)
            if s in (400, 404, 422):
                msg = _error_message(resp.body)
                raise BadRequestError(
                    f"Groq отклонил запрос ({s}): {msg}. Проверьте модель "
                    f"(допустимы: {', '.join(KNOWN_MODELS)}).")
            if s == 401:
                raise AuthError("Groq отклонил ключ API (401)")
            if s == 413:
                raise FileTooLargeError("Аудио превышает лимит Groq (413, >25 МБ)")
            if s == 403:
                raise ProviderError(
                    "Запрос заблокирован (403) — вероятно Cloudflare/сеть "
                    f"(прокси, VPN, фильтр). Ответ: {_error_message(resp.body)}")
            if s == 429 or s >= 500 or s == 0:
                attempt += 1
                if attempt > self.max_retries:
                    if s == 429:
                        raise RateLimitError("Groq: лимит запросов (429), попробуйте позже")
                    raise NetworkError(
                        f"Groq недоступен (status {s}): {_error_message(resp.body)}")
                self._sleep(min(2 ** attempt * 0.5, 8.0))
                continue
            raise ProviderError(f"Groq error {s}: {_error_message(resp.body)}")

from __future__ import annotations

import json
import mimetypes
import urllib.parse
import urllib.request
import uuid
from urllib.error import HTTPError
from typing import Any

import httpx


class MaxClient:
    base_url = "https://platform-api.max.ru"

    def __init__(self, token: str, timeout: float = 60.0) -> None:
        self.token = token
        self.timeout = timeout

    def close(self) -> None:
        return None

    def __enter__(self) -> "MaxClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def send_message(
        self,
        chat_id: str,
        text: str,
        attachments: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str | None]:
        payload: dict[str, Any] = {"text": text, "format": "html"}
        if attachments:
            payload["attachments"] = attachments

        data = self._request_json(
            "POST",
            f"/messages?chat_id={urllib.parse.quote(chat_id, safe='')}",
            payload,
        )
        message = data.get("message") or {}
        body = data.get("body") or message.get("body") or {}
        message_id = str(body.get("mid") or "")
        if not message_id:
            raise RuntimeError(f"MAX send_message response has no message id: {data}")
        return message_id, None

    def upload_attachment(self, media_url: str, attachment_type: str) -> str:
        upload_type = "video" if attachment_type == "video" else "image"
        upload_data = self._request_json("POST", f"/uploads?type={upload_type}", {})
        upload_url = str(upload_data.get("url") or "")
        if not upload_url:
            raise RuntimeError(f"MAX uploads response has no upload_url: {upload_data}")

        with httpx.stream("GET", media_url, follow_redirects=True, timeout=60.0) as media_response:
            media_response.raise_for_status()
            media_bytes = media_response.read()
            content_type = media_response.headers.get("content-type", "application/octet-stream")

        file_name = f"upload{_guess_extension(content_type)}"

        if upload_type == "video":
            token = str(upload_data.get("token") or "")
            if not token:
                raise RuntimeError(f"MAX upload init returned no token: {upload_data}")
            self._upload_multipart(upload_url, file_name, media_bytes, content_type)
            return token

        upload_result = self._upload_multipart(upload_url, file_name, media_bytes, content_type)
        image_token = _extract_image_token(upload_result)
        if not image_token:
            raise RuntimeError(f"MAX image upload returned no token: {upload_result}")
        return image_token

    def get_message_url(self, message_id: str) -> str | None:
        data = self._request_json("GET", f"/messages/{urllib.parse.quote(message_id, safe='')}", None)
        url = data.get("url")
        return str(url) if url else None

    def _request_json(self, method: str, path: str, payload: dict[str, Any] | None) -> dict[str, Any]:
        data = None if payload is None and method == "GET" else json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Authorization": self.token,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace").strip()
            detail = f": {body[:1000]}" if body else ""
            raise RuntimeError(f"MAX API {exc.code} for {path}{detail}") from exc
        if not raw.strip():
            return {}
        return json.loads(raw)

    def _upload_multipart(
        self,
        url: str,
        file_name: str,
        data: bytes,
        content_type: str,
    ) -> Any:
        boundary = f"----Codex{uuid.uuid4().hex}"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="data"; filename="{file_name}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("utf-8") + data + f"\r\n--{boundary}--\r\n".encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=300) as response:
            raw = response.read().decode("utf-8", errors="replace").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw


def _guess_extension(content_type: str) -> str:
    extension = mimetypes.guess_extension(content_type.split(";")[0].strip()) or ""
    return extension


def _extract_image_token(payload: Any) -> str | None:
    if isinstance(payload, dict):
        direct_token = payload.get("token")
        if direct_token:
            return str(direct_token)
        photos = payload.get("photos")
        if isinstance(photos, dict):
            for variants in photos.values():
                token = _extract_image_token(variants)
                if token:
                    return token
        for value in payload.values():
            token = _extract_image_token(value)
            if token:
                return token
    elif isinstance(payload, list):
        for item in payload:
            token = _extract_image_token(item)
            if token:
                return token
    return None

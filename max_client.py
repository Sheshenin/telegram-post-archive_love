from __future__ import annotations

import json
from typing import Any

import httpx


class MaxClient:
    def __init__(self, token: str, timeout: float = 30.0) -> None:
        self.client = httpx.Client(
            base_url="https://platform-api.max.ru",
            timeout=timeout,
            headers={"Authorization": token},
        )

    def close(self) -> None:
        self.client.close()

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

        response = self.client.post("/messages", params={"chat_id": chat_id}, json=payload)
        response.raise_for_status()
        data = response.json()
        message = data.get("message", data)

        message_id = None
        for key in ("message_id", "id", "mid"):
            value = message.get(key) if isinstance(message, dict) else None
            if value:
                message_id = str(value)
                break
            value = data.get(key)
            if value:
                message_id = str(value)
                break

        if not message_id and isinstance(message, dict):
            body = message.get("body")
            if isinstance(body, dict) and body.get("mid"):
                message_id = str(body["mid"])

        if not message_id:
            raise RuntimeError(f"MAX send_message response has no message id: {message}")

        return message_id, None

    def upload_attachment(self, media_url: str, attachment_type: str) -> str:
        upload_info = self.client.post("/uploads", params={"type": attachment_type})
        upload_info.raise_for_status()
        upload_data = upload_info.json()

        upload_url = upload_data.get("upload_url") or upload_data.get("url")
        if not upload_url:
            raise RuntimeError(f"MAX uploads response has no upload_url: {upload_data}")
        upload_token = upload_data.get("token")

        with httpx.stream("GET", media_url, follow_redirects=True, timeout=60.0) as media_response:
            media_response.raise_for_status()
            media_bytes = media_response.read()
            content_type = media_response.headers.get("content-type", "application/octet-stream")

        uploaded = httpx.post(
            upload_url,
            files={"data": ("upload", media_bytes, content_type)},
            headers={"Authorization": self.client.headers["Authorization"]},
            timeout=120.0,
        )
        uploaded.raise_for_status()

        if attachment_type in {"video", "audio"} and upload_token:
            return str(upload_token)

        upload_result = self._parse_upload_result(uploaded)
        direct_token = upload_result.get("attachment_token") or upload_result.get("token")
        if direct_token:
            return str(direct_token)

        photos = upload_result.get("photos")
        if isinstance(photos, dict):
            for photo_data in photos.values():
                if isinstance(photo_data, dict) and photo_data.get("token"):
                    return str(photo_data["token"])

        raise RuntimeError(f"MAX upload result has no attachment token: {upload_result}")

    @staticmethod
    def _parse_upload_result(response: httpx.Response) -> dict[str, Any]:
        text = response.text.strip()
        if not text:
            return {}

        try:
            return response.json()
        except json.JSONDecodeError:
            return {"raw": text}

    def get_message_url(self, message_id: str) -> str | None:
        response = self.client.get(f"/messages/{message_id}")
        response.raise_for_status()
        data = response.json()
        url = data.get("url")
        return str(url) if url else None

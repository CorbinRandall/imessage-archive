"""Proxy Immich media so the browser never sees the API key."""
from __future__ import annotations

import logging
from typing import AsyncIterator

import httpx
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from app.config import IMMICH_API_KEY, IMMICH_URL

logger = logging.getLogger(__name__)

_THUMB_SIZES = {"thumbnail", "preview", "fullsize", "original"}


def immich_enabled() -> bool:
    return bool(IMMICH_URL and IMMICH_API_KEY)


def _headers() -> dict[str, str]:
    return {"x-api-key": IMMICH_API_KEY, "Accept": "*/*"}


async def _stream(url: str) -> StreamingResponse:
    client = httpx.AsyncClient(timeout=120.0)
    try:
        req = client.build_request("GET", url, headers=_headers())
        resp = await client.send(req, stream=True)
        if resp.status_code >= 400:
            body = await resp.aread()
            await resp.aclose()
            await client.aclose()
            raise HTTPException(resp.status_code, body.decode()[:200] or "Immich error")

        async def body() -> AsyncIterator[bytes]:
            try:
                async for chunk in resp.aiter_bytes():
                    yield chunk
            finally:
                await resp.aclose()
                await client.aclose()

        media_type = resp.headers.get("content-type", "application/octet-stream")
        return StreamingResponse(body(), media_type=media_type)
    except HTTPException:
        await client.aclose()
        raise
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("Immich proxy error: %s", exc)
        raise HTTPException(502, f"Immich unreachable: {exc}") from exc


async def proxy_thumbnail(asset_id: str, size: str = "thumbnail") -> StreamingResponse:
    if not immich_enabled():
        raise HTTPException(503, "Immich not configured")
    if size not in _THUMB_SIZES:
        size = "thumbnail"
    url = f"{IMMICH_URL}/api/assets/{asset_id}/thumbnail?size={size}"
    return await _stream(url)


async def proxy_original(asset_id: str) -> StreamingResponse:
    if not immich_enabled():
        raise HTTPException(503, "Immich not configured")
    return await _stream(f"{IMMICH_URL}/api/assets/{asset_id}/original")


async def proxy_playback(asset_id: str) -> StreamingResponse:
    if not immich_enabled():
        raise HTTPException(503, "Immich not configured")
    return await _stream(f"{IMMICH_URL}/api/assets/{asset_id}/video/playback")

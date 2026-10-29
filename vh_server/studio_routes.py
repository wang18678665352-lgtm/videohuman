"""工作室商用 HTTP 路由。鉴权沿用应用中间件,这里不再校验 token。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from vh_core.config import Settings
from vh_core.studio import build_delivery_zip, readiness, usage_summary

_CONSENT_NAME = "consent.jsonl"
_CONSENT_TYPE = "application/x-ndjson"


def mount_studio(app: FastAPI, settings: Settings, queue: Any) -> None:
    """注册就绪、用量、授权日志和交付包路由。queue 由 create_app 持有,此处不使用。"""

    @app.get("/api/readiness")
    def studio_readiness() -> dict[str, Any]:
        return readiness(settings)

    @app.get("/api/usage")
    def studio_usage() -> dict[str, Any]:
        return usage_summary(settings)

    @app.get("/api/consent")
    def studio_consent() -> Response:
        path = settings.assets / ".consent.jsonl"
        if path.is_file():
            return FileResponse(path, media_type=_CONSENT_TYPE, filename=_CONSENT_NAME)
        return Response(
            content=b"",
            media_type=_CONSENT_TYPE,
            headers={"Content-Disposition": f'attachment; filename="{_CONSENT_NAME}"'},
        )

    @app.get("/api/jobs/{job_id}/delivery")
    def studio_delivery(job_id: str) -> Response:
        try:
            payload = build_delivery_zip(settings, job_id)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        filename = f"{job_id}-delivery.zip"
        return Response(
            content=payload,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

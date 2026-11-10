"""Footage / music / SFX rights register."""

from __future__ import annotations

import re
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...enums import AssetKind, Platform
from ...models import MediaAsset, Render
from ...video import footage
from .. import serializers as ser
from ..deps import admin, db, log_action, viewer

router = APIRouter(prefix="/api/assets")
MAX_UPLOAD = 2 * 1024**3


class AssetIn(BaseModel):
    path: str
    kind: AssetKind = AssetKind.FOOTAGE
    original_source: str = Field(min_length=2)
    owner: str = Field(min_length=2)
    licence: str = Field(min_length=2)
    usage_permissions: str = ""
    attribution_required: str = ""
    allowed_platforms: list[Platform] = Field(default_factory=list)
    monetization_allowed: bool = False
    is_official_material: bool = False
    expires_at: datetime | None = None
    restrictions: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""


class AssetPatch(BaseModel):
    original_source: str | None = None
    owner: str | None = None
    licence: str | None = None
    usage_permissions: str | None = None
    attribution_required: str | None = None
    allowed_platforms: list[Platform] | None = None
    monetization_allowed: bool | None = None
    is_official_material: bool | None = None
    expires_at: datetime | None = None
    restrictions: str | None = None
    tags: list[str] | None = None
    description: str | None = None


RIGHTS_FIELDS = {"original_source", "owner", "licence", "usage_permissions", "allowed_platforms",
                 "monetization_allowed", "is_official_material", "expires_at", "restrictions"}


@router.get("")
def list_assets(kind: str | None = None, s: Session = Depends(db), _: str = Depends(viewer)):
    q = select(MediaAsset).order_by(MediaAsset.id.desc())
    if kind:
        q = q.where(MediaAsset.kind == kind)
    return [ser.asset(a) for a in s.scalars(q)]


@router.post("")
def register(body: AssetIn, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    try:
        a = footage.register_asset(s, **{**body.model_dump(), "kind": body.kind.value,
                                         "allowed_platforms": [p.value for p in body.allowed_platforms]})
    except footage.RightsError as exc:
        raise HTTPException(422, str(exc)) from exc
    log_action(s, request, "asset.register", f"asset:{a.id}", owner=a.owner, licence=a.licence)
    return ser.asset(a)


@router.post("/upload")
async def upload(request: Request, file: UploadFile = File(...), kind: str = Form("footage"),
                 s: Session = Depends(db), _: str = Depends(admin)):
    """Store an administrator-supplied file in the footage root. Rights are registered separately."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", file.filename or "upload")[:120]
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    allowed = footage.VIDEO_EXTS | footage.AUDIO_EXTS | footage.IMAGE_EXTS
    if ext not in allowed:
        raise HTTPException(415, f"unsupported file type {ext}")
    dest_dir = get_settings().data_dir / "footage" / kind
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / name
    if dest.exists():
        raise HTTPException(409, "a file with this name already exists")
    written = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1 << 20):
            written += len(chunk)
            if written > MAX_UPLOAD:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "file too large")
            out.write(chunk)
    log_action(s, request, "asset.upload", str(dest), bytes=written)
    return {"path": str(dest), "bytes": written}


@router.post("/{asset_id}/verify")
def verify(asset_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    a = s.get(MediaAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    try:
        footage.verify_rights(a, actor=f"admin@{request.client.host if request.client else 'unknown'}")
    except footage.RightsError as exc:
        raise HTTPException(422, str(exc)) from exc
    log_action(s, request, "asset.verify_rights", f"asset:{asset_id}")
    return ser.asset(a)


@router.post("/{asset_id}/analyse")
def analyse(asset_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    a = s.get(MediaAsset, asset_id)
    if not a or a.kind != "footage":
        raise HTTPException(404, "footage asset not found")
    footage.analyse_asset(a)
    log_action(s, request, "asset.analyse", f"asset:{asset_id}")
    return ser.asset(a)


@router.patch("/{asset_id}")
def patch(asset_id: int, body: AssetPatch, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    a = s.get(MediaAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        if k == "allowed_platforms":
            v = sorted({p.value if hasattr(p, "value") else p for p in v})
        setattr(a, k, v)
    if RIGHTS_FIELDS & changes.keys() and a.rights_verified:
        a.rights_verified, a.verified_by = False, ""  # any rights change needs re-verification
    log_action(s, request, "asset.update", f"asset:{asset_id}", fields=sorted(changes))
    return ser.asset(a)


@router.delete("/{asset_id}")
def remove(asset_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    a = s.get(MediaAsset, asset_id)
    if not a:
        raise HTTPException(404, "asset not found")
    used = [r.id for r in s.scalars(select(Render)) if asset_id in (r.asset_ids or [])]
    if used:
        raise HTTPException(409, f"asset used in renders {used[:5]}; revoke rights instead of deleting")
    s.delete(a)
    log_action(s, request, "asset.delete", f"asset:{asset_id}")
    return {"deleted": asset_id}


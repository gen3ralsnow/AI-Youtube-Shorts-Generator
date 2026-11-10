"""Topics, research, jobs, scripts, voiceovers, renders, calendar and media files."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, HttpUrl
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ...agents.base import AgentContext
from ...agents.research import ResearchAgent
from ...agents.strategist import build_calendar
from ...config import get_settings
from ...db import session_scope
from ...enums import Category, Classification, JobStatus, Stage, TopicStatus
from ...models import (
    ContentJob,
    MetadataPackage,
    Publication,
    Render,
    Script,
    SourceItem,
    StageRun,
    Thumbnail,
    Topic,
    Voiceover,
)
from ...pipeline import orchestrator
from ...publishing import manager
from ...research import classify
from ...research.connectors import RawItem, connector_status
from ...research.text import fingerprint, keyset
from .. import background
from .. import serializers as ser
from ..deps import admin, db, log_action, viewer

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ research & topics


@router.get("/research/sources")
def research_sources(_: str = Depends(viewer)):
    return connector_status()


@router.post("/research/run")
def research_run(request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    log_action(s, request, "research.run")
    return {"status": background.run_tick("research")}


@router.get("/topics")
def topics(status: str | None = None, min_score: float = 0, limit: int = Query(100, le=500),
           s: Session = Depends(db), _: str = Depends(viewer)):
    q = select(Topic).where(Topic.score >= min_score).order_by(Topic.created_at.desc()).limit(limit)
    if status:
        q = q.where(Topic.status == status)
    return [ser.topic(t) for t in s.scalars(q)]


@router.get("/topics/{topic_id}")
def topic_detail(topic_id: int, s: Session = Depends(db), _: str = Depends(viewer)):
    t = s.get(Topic, topic_id)
    if not t:
        raise HTTPException(404, "topic not found")
    return ser.topic(t, with_sources=True)


class ManualSource(BaseModel):
    url: HttpUrl
    title: str = Field(min_length=3, max_length=300)
    summary: str = Field(default="", max_length=3000)


class ManualTopic(BaseModel):
    title: str = Field(min_length=5, max_length=200)
    explanation: str = Field(default="", max_length=2000)
    category: Category
    sources: list[ManualSource] = Field(min_length=1, max_length=10)


@router.post("/topics")
def create_topic(body: ManualTopic, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    """Admin-submitted topic. Accuracy classification is still derived from the sources."""
    items = []
    for src in body.sources:
        raw = RawItem(connector="manual", external_id=str(src.url), url=str(src.url), title=src.title,
                      summary=src.summary)
        ResearchAgent._store(AgentContext(s), raw)  # reuse dedupe + credibility rating
        items.append(s.scalar(select(SourceItem).where(SourceItem.connector == "manual",
                                                       SourceItem.external_id == str(src.url))))
    texts = [f"{i.title} {i.summary}" for i in items]
    classification = classify.classify(texts, [i.credibility for i in items])
    category = classify.compatible(body.category, classification)
    t = Topic(title=body.title, explanation=body.explanation, category=category.value,
              classification=classification.value, score=0.0,
              score_breakdown={"note": "manual topic; not scored from audience signals"},
              status=TopicStatus.NEW.value, fingerprint=fingerprint(body.title), keywords=sorted(keyset(body.title)))
    t.sources = items
    s.add(t)
    s.flush()
    log_action(s, request, "topic.create", f"topic:{t.id}", classification=classification.value)
    return ser.topic(t, with_sources=True)


@router.post("/topics/{topic_id}/reject")
def reject_topic(topic_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    t = s.get(Topic, topic_id) or _404("topic")
    t.status = TopicStatus.REJECTED.value
    log_action(s, request, "topic.reject", f"topic:{topic_id}")
    return ser.topic(t)


@router.post("/topics/{topic_id}/produce")
def produce_topic(topic_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    t = s.get(Topic, topic_id) or _404("topic")
    if t.status in (TopicStatus.REJECTED.value, TopicStatus.DUPLICATE.value):
        raise HTTPException(409, f"topic is {t.status}")
    if not t.sources:
        raise HTTPException(409, "topic has no sources")
    job = ContentJob(topic_id=t.id, category=t.category, status=JobStatus.QUEUED.value,
                     duration_bucket=t.recommended_length)
    s.add(job)
    t.status = TopicStatus.SELECTED.value
    s.flush()
    log_action(s, request, "job.create", f"job:{job.id}", topic=t.id)
    return ser.job(job)


@router.post("/strategy/plan")
def plan(request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    log_action(s, request, "strategy.plan")
    return {"status": background.run_tick("plan")}


@router.get("/calendar")
def calendar(days: int = Query(7, ge=1, le=31), s: Session = Depends(db), _: str = Depends(viewer)):
    return build_calendar(s, days)


# ------------------------------------------------------------------ jobs


@router.get("/jobs")
def jobs(status: str | None = None, limit: int = Query(100, le=500), s: Session = Depends(db),
         _: str = Depends(viewer)):
    q = select(ContentJob).order_by(ContentJob.id.desc()).limit(limit)
    if status:
        q = q.where(ContentJob.status == status)
    return [ser.job(j) for j in s.scalars(q)]


@router.get("/jobs/{job_id}")
def job_detail(job_id: int, s: Session = Depends(db), _: str = Depends(viewer)):
    j = s.get(ContentJob, job_id) or _404("job")
    script = s.scalar(select(Script).where(Script.job_id == job_id).order_by(Script.version.desc()))
    voice = s.scalar(select(Voiceover).where(Voiceover.job_id == job_id).order_by(Voiceover.id.desc()))
    return {
        **ser.job(j),
        "next_stage": (st.value if (st := orchestrator.next_stage(s, j)) else None),
        "stages": [ser.stage(r) for r in s.scalars(select(StageRun).where(StageRun.job_id == job_id)
                                                   .order_by(StageRun.id))],
        "script": ser.script(script) if script else None,
        "voiceover": ser.voiceover(voice) if voice else None,
        "renders": [ser.render(r) for r in s.scalars(select(Render).where(Render.job_id == job_id))],
        "thumbnails": [ser.thumbnail(t) for t in s.scalars(select(Thumbnail).where(Thumbnail.job_id == job_id))],
        "metadata": [ser.metadata(m) for m in s.scalars(select(MetadataPackage).where(MetadataPackage.job_id == job_id))],
        "publications": [ser.publication(p) for p in s.scalars(select(Publication).where(Publication.job_id == job_id))],
        "topic": ser.topic(j.topic, with_sources=True) if j.topic else None,
    }


@router.post("/jobs/{job_id}/run")
def run_job(job_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    j = s.get(ContentJob, job_id) or _404("job")
    if j.status not in (JobStatus.QUEUED.value,):
        raise HTTPException(409, f"job is {j.status}; retry or regenerate it first")
    log_action(s, request, "job.run", f"job:{job_id}")
    s.commit()
    result = background.run_job(job_id)
    if result.startswith("not started"):
        raise HTTPException(409, result)
    return {"status": result}


@router.post("/jobs/{job_id}/retry")
def retry_job(job_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    j = s.get(ContentJob, job_id) or _404("job")
    if j.status not in (JobStatus.FAILED.value, JobStatus.BLOCKED.value, JobStatus.QUEUED.value):
        raise HTTPException(409, f"job is {j.status}")
    orchestrator.retry(s, j)
    log_action(s, request, "job.retry", f"job:{job_id}")
    s.commit()
    return {**ser.job(j), "run": background.run_job(job_id)}


class Regenerate(BaseModel):
    target: str = Field(pattern="^(script|voiceover|video|metadata)$")


REGEN_STAGE = {"script": Stage.SCRIPT, "voiceover": Stage.VOICEOVER, "video": Stage.FOOTAGE,
               "metadata": Stage.METADATA}


@router.post("/jobs/{job_id}/regenerate")
def regenerate(job_id: int, body: Regenerate, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    j = s.get(ContentJob, job_id) or _404("job")
    try:
        orchestrator.invalidate(s, j, REGEN_STAGE[body.target])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    log_action(s, request, "job.regenerate", f"job:{job_id}", target=body.target)
    s.commit()
    return {**ser.job(j), "run": background.run_job(job_id)}


class JobEdit(BaseModel):
    title_override: str | None = Field(default=None, max_length=100)
    captions: list[str] | None = None


@router.patch("/jobs/{job_id}")
def edit_job(job_id: int, body: JobEdit, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    j = s.get(ContentJob, job_id) or _404("job")
    changed = []
    if body.title_override is not None:
        j.title_override = body.title_override.strip() or None
        changed.append("title")
    if body.captions is not None:
        script = s.scalar(select(Script).where(Script.job_id == job_id).order_by(Script.version.desc())) or _404("script")
        if len(body.captions) != len(script.scenes):
            raise HTTPException(422, f"expected {len(script.scenes)} captions")
        scenes = [dict(sc) for sc in script.scenes]
        for sc, cap in zip(scenes, body.captions):
            sc["caption"] = cap.strip()[:60]
        script.scenes, script.captions = scenes, [c.strip()[:60] for c in body.captions]
        changed.append("captions")
    # Captions are burned into the video; titles live in metadata. Re-run what depends on them.
    try:
        if "captions" in changed:
            orchestrator.invalidate(s, j, Stage.FOOTAGE)
        elif "title" in changed and orchestrator.next_stage(s, j) is None:
            orchestrator.invalidate(s, j, Stage.METADATA)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    log_action(s, request, "job.edit", f"job:{job_id}", fields=changed)
    return ser.job(j)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    j = s.get(ContentJob, job_id) or _404("job")
    j.status = JobStatus.CANCELLED.value
    for p in s.scalars(select(Publication).where(Publication.job_id == job_id, Publication.status.in_(
            ["queued", "dry_run", "blocked"]))):
        s.delete(p)
    log_action(s, request, "job.cancel", f"job:{job_id}")
    return ser.job(j)


@router.delete("/jobs/{job_id}")
def delete_job(job_id: int, request: Request, s: Session = Depends(db), _: str = Depends(admin)):
    j = s.get(ContentJob, job_id) or _404("job")
    sent = s.scalar(select(Publication).where(Publication.job_id == job_id, Publication.status.in_(manager.ACTIVE)))
    if sent:
        raise HTTPException(409, "job has been sent to a platform; it is kept for the record")
    if j.status == JobStatus.RUNNING.value:
        raise HTTPException(409, "job is running")
    files = [r.path for r in s.scalars(select(Render).where(Render.job_id == job_id))]
    files += [t.path for t in s.scalars(select(Thumbnail).where(Thumbnail.job_id == job_id))]
    for v in s.scalars(select(Voiceover).where(Voiceover.job_id == job_id)):
        files += [v.raw_path, v.processed_path]
    s.execute(delete(Publication).where(Publication.job_id == job_id))
    if j.topic:
        j.topic.status = TopicStatus.NEW.value
    s.delete(j)
    root = get_settings().data_dir.resolve()
    for f in files:
        p = Path(f).resolve()
        if root in p.parents and p.is_file():
            p.unlink()
    log_action(s, request, "job.delete", f"job:{job_id}", files=len(files))
    return {"deleted": job_id}


# ------------------------------------------------------------------ libraries


@router.get("/scripts")
def scripts(limit: int = Query(100, le=500), s: Session = Depends(db), _: str = Depends(viewer)):
    rows = s.scalars(select(Script).order_by(Script.id.desc()).limit(limit))
    return [{k: v for k, v in ser.script(x).items() if k not in ("scenes", "sections")} for x in rows]


@router.get("/voiceovers")
def voiceovers(limit: int = Query(100, le=500), s: Session = Depends(db), _: str = Depends(viewer)):
    return [ser.voiceover(v) for v in s.scalars(select(Voiceover).order_by(Voiceover.id.desc()).limit(limit))]


@router.get("/renders")
def renders(limit: int = Query(100, le=500), s: Session = Depends(db), _: str = Depends(viewer)):
    return [ser.render(r) for r in s.scalars(select(Render).order_by(Render.id.desc()).limit(limit))]


@router.get("/reference/enums")
def enums(_: str = Depends(viewer)):
    return {"categories": [c.value for c in Category], "classifications": [c.value for c in Classification],
            "stages": [st.value for st in Stage]}


# ------------------------------------------------------------------ files (paths come from the DB only)


def _serve(path: str, media: str) -> FileResponse:
    p = Path(path).resolve()
    root = get_settings().data_dir.resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(404, "file not available")
    return FileResponse(p, media_type=media)


# The DB session is closed *before* streaming starts: a browser can keep a video stream open
# for minutes, and holding a pooled connection for that long exhausts the pool.


def _lookup(model, obj_id: int, attr) -> str:
    with session_scope() as s:
        obj = s.get(model, obj_id)
        if obj is None:
            raise HTTPException(404, "file not found")
        return attr(obj)


@router.get("/files/render/{render_id}")
def render_file(render_id: int, _: str = Depends(viewer)):
    return _serve(_lookup(Render, render_id, lambda r: r.path), "video/mp4")


@router.get("/files/thumbnail/{thumb_id}")
def thumb_file(thumb_id: int, _: str = Depends(viewer)):
    return _serve(_lookup(Thumbnail, thumb_id, lambda t: t.path), "image/jpeg")


@router.get("/files/voiceover/{voice_id}")
def voice_file(voice_id: int, kind: str = Query("processed", pattern="^(raw|processed)$"), _: str = Depends(viewer)):
    path = _lookup(Voiceover, voice_id, lambda v: v.raw_path if kind == "raw" else v.processed_path)
    return _serve(path, "audio/mpeg" if path.endswith(".mp3") else "audio/wav")


def _404(what: str):
    raise HTTPException(404, f"{what} not found")

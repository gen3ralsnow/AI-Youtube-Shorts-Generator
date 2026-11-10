"""Agent 7: Video Production - footage selection, editing/rendering, platform exports, thumbnails."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import delete, select

from .. import costs, settings_store
from ..config import get_settings
from ..enums import Category
from ..models import ContentJob, MediaAsset, Render, Thumbnail
from ..storage import LocalStorage, get_storage
from ..video import assemble, ffmpeg, footage, subtitles, thumbnails
from ..video.profiles import PLATFORM_PROFILE, PROFILES
from .base import Agent, AgentBlocked, AgentContext
from .scriptwriter import latest_script
from .voiceover import latest_voiceover

SFX_TAGS = ("impact", "riser", "swipe")


def persist_outputs(job_id: int, paths: list[Path]) -> dict[str, str]:
    """Copy final artefacts to the configured object storage (no-op copy for local storage).
    Local files stay as the working copies FFmpeg and the uploaders read."""
    storage = get_storage()
    if isinstance(storage, LocalStorage):
        return {str(p): f"file://{p}" for p in paths}
    out = {}
    for p in paths:
        if p.is_file():
            out[str(p)] = storage.persist(p, f"jobs/{job_id}/{p.parent.name}/{p.name}")
    return out


def target_platforms(session) -> list[str]:
    plats = settings_store.get(session, "schedule")["platforms"]
    return [p for p, cfg in plats.items() if cfg.get("enabled")]


class ProductionInput(BaseModel):
    job_id: int
    step: str = Field(pattern="^(footage|edit|thumbnails)$")


class ProductionOutput(BaseModel):
    step: str
    detail: dict = Field(default_factory=dict)


class VideoProductionAgent(Agent[ProductionInput, ProductionOutput]):
    name = "video_production"
    responsibility = "Select rights-cleared footage, assemble and render the edit, export per platform."
    input_model, output_model = ProductionInput, ProductionOutput
    max_attempts = 2

    def execute(self, ctx: AgentContext, payload: ProductionInput) -> ProductionOutput:
        s = ctx.session
        job = s.get(ContentJob, payload.job_id)
        script = latest_script(s, payload.job_id)
        voice = latest_voiceover(s, payload.job_id)
        if job is None or script is None or voice is None:
            raise AgentBlocked("job needs a script and a voiceover first")
        if payload.step == "footage":
            return self._footage(ctx, job, script, voice)
        if payload.step == "edit":
            return self._edit(ctx, job, script, voice)
        return self._thumbnails(ctx, job, script)

    # ---------------------------------------------------------------- footage
    def _footage(self, ctx, job, script, voice) -> ProductionOutput:
        s = ctx.session
        platforms = target_platforms(s)
        total = round(voice.duration + 0.5, 3)  # short tail after the last word
        scenes = assemble.retime_scenes(script.scenes, voice.word_timings, total)
        durations = [sc["end"] - sc["start"] for sc in scenes]
        assets = footage.usable_assets(s, "footage", platforms)
        try:
            picks = footage.select_segments(scenes, durations, assets,
                                            prefer_official=job.category == Category.NEWS.value)
        except footage.RightsError as exc:
            raise AgentBlocked(str(exc)) from exc
        edl = {
            "scenes": scenes,
            "picks": [p.__dict__ for p in picks],
            "platforms": platforms,
            "total": total,
            "fps": assemble.choose_fps([a.fps for a in assets if a.id in {p.asset_id for p in picks}]),
        }
        return ProductionOutput(step="footage", detail=edl)

    # ---------------------------------------------------------------- edit
    def _edit(self, ctx, job, script, voice) -> ProductionOutput:
        s = ctx.session
        from ..models import StageRun

        footage_run = s.scalar(
            select(StageRun).where(StageRun.job_id == job.id, StageRun.stage == "footage_selection",
                                   StageRun.status == "succeeded").order_by(StageRun.id.desc())
        )
        if footage_run is None or "picks" not in footage_run.output:
            raise AgentBlocked("no footage selection recorded")
        edl = footage_run.output
        platforms = edl["platforms"]
        picks = [footage.Pick(**p) for p in edl["picks"]]
        # Re-check rights at render time: a licence may have expired or been revoked.
        for p in picks:
            asset = s.get(MediaAsset, p.asset_id)
            ok, why = footage.usable(asset, platforms) if asset else (False, "deleted")
            if not ok:
                raise AgentBlocked(f"asset {p.asset_id} no longer usable: {why}")

        settings = get_settings()
        brand = settings_store.get(s, "brand")
        out_dir = settings.data_dir / "renders" / f"job{job.id}"
        ass_path = settings.data_dir / "tmp" / f"job{job.id}" / "subs.ass"
        cues = subtitles.build_ass([dict(w) for w in voice.word_timings], edl["scenes"], ass_path,
                                   brand=brand, duration=float(edl["total"]))

        music = self._least_used(s, "music", platforms)
        sfx = {}
        for tag in SFX_TAGS:
            hit = next((a for a in footage.usable_assets(s, "sfx", platforms) if tag in (a.tags or [])), None)
            if hit:
                sfx[tag] = Path(hit.path)
        total = float(edl["total"])
        plan = assemble.RenderPlan(picks=picks, scenes=edl["scenes"], narration=Path(voice.processed_path),
                                   subtitles=ass_path, total=total, fps=int(edl["fps"]),
                                   music=Path(music.path) if music else None, sfx=sfx)
        est = costs.estimate_render(total * (1 + len(platforms)))
        costs.check_budget(s, est, job.id)
        master = assemble.render(plan, out_dir / "master.mp4")
        costs.record(s, category="video_processing", provider="ffmpeg-local", units=total, unit="seconds",
                     estimated_usd=est, actual_usd=est, job_id=job.id)

        s.execute(delete(Render).where(Render.job_id == job.id))
        used_ids = sorted({p.asset_id for p in picks} | ({music.id} if music else set()))
        rows = []
        for platform in platforms:
            profile = PROFILES[PLATFORM_PROFILE[platform]]
            path = assemble.export_profile(master, profile, out_dir / f"{profile.name}.mp4")
            info = ffmpeg.probe(path)
            row = Render(job_id=job.id, profile=profile.name, path=str(path),
                         edit_decision_list={"picks": edl["picks"], "music_asset": music.id if music else None,
                                             "sfx": {k: str(v) for k, v in sfx.items()}, "cues": cues},
                         width=info["width"], height=info["height"], fps=info["fps"],
                         duration=info["duration"], size_bytes=info["size"], asset_ids=used_ids)
            s.add(row)
            rows.append(row)
        for aid in used_ids:
            a = s.get(MediaAsset, aid)
            if a:
                a.times_used += 1
        stored = persist_outputs(job.id, [Path(r.path) for r in rows] +
                                 [Path(voice.raw_path), Path(voice.processed_path)])
        for r in rows:
            r.edit_decision_list = {**r.edit_decision_list, "storage_uri": stored.get(r.path)}
        s.flush()
        return ProductionOutput(step="edit", detail={
            "master": str(master), "renders": [{"id": r.id, "profile": r.profile, "path": r.path} for r in rows],
            "music": music.path if music else None, "sfx": list(sfx), "cues": len(cues),
        })

    @staticmethod
    def _least_used(s, kind, platforms):
        assets = footage.usable_assets(s, kind, platforms)
        return min(assets, key=lambda a: a.times_used) if assets else None

    # ---------------------------------------------------------------- thumbnails
    def _thumbnails(self, ctx, job, script) -> ProductionOutput:
        s = ctx.session
        renders = list(s.scalars(select(Render).where(Render.job_id == job.id)))
        if not renders:
            raise AgentBlocked("no render to take a frame from")
        settings = get_settings()
        brand = settings_store.get(s, "brand")
        master = Path(renders[0].path).parent / "master.mp4"
        hook_scene = script.scenes[0] if script.scenes else {"start": 0, "end": 1}
        frame_at = (hook_scene.get("start", 0) + max(hook_scene.get("end", 1), 0.5)) / 2
        frame = ffmpeg.extract_frame(master, frame_at, settings.data_dir / "tmp" / f"job{job.id}" / "frame.jpg")
        classification = job.topic.classification if job.topic else "community_theory"
        concept = thumbnails.concept(script.title, classification, brand)
        text = concept["supporting_text"]
        s.execute(delete(Thumbnail).where(Thumbnail.job_id == job.id))
        out_dir = settings.data_dir / "thumbnails" / f"job{job.id}"
        made = []
        for platform, size in (("youtube", (1280, 720)), ("instagram", (1080, 1920))):
            for variant in ("A", "B", "C"):
                path = thumbnails.compose(frame, text, out_dir / f"{platform}_{variant}.jpg", size, variant, brand)
                row = Thumbnail(job_id=job.id, variant=variant, platform=platform, path=str(path),
                                concept=concept, selected=variant == "A")
                s.add(row)
                made.append(str(path))
        persist_outputs(job.id, [Path(m) for m in made])
        s.flush()
        return ProductionOutput(step="thumbnails", detail={"files": made, "concept": concept,
                                                           "tiktok_cover_timestamp_ms": int(frame_at * 1000)})

"""Agent 11: Quality Control - technical video QC plus content/publishing readiness."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy import select

from ..models import ContentJob, MediaAsset, Render
from ..video import footage, qc
from ..video.profiles import PROFILES
from .base import Agent, AgentBlocked, AgentContext
from .scriptwriter import latest_script
from .voiceover import latest_voiceover


class QCInput(BaseModel):
    job_id: int


class QCOutput(BaseModel):
    technical_passed: bool
    publishable: bool
    renders: list[dict] = Field(default_factory=list)
    content_checks: list[dict] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)


class QualityControlAgent(Agent[QCInput, QCOutput]):
    name = "quality_control"
    responsibility = "Reject defective exports and anything not ready to publish (facts, rights, voice)."
    input_model, output_model = QCInput, QCOutput
    max_attempts = 1

    def execute(self, ctx: AgentContext, payload: QCInput) -> QCOutput:
        s = ctx.session
        job = s.get(ContentJob, payload.job_id)
        script = latest_script(s, payload.job_id)
        voice = latest_voiceover(s, payload.job_id)
        renders = list(s.scalars(select(Render).where(Render.job_id == payload.job_id)))
        if not (job and script and voice and renders):
            raise AgentBlocked("nothing rendered to inspect")

        content = []
        validation = script.validation or {}
        content.append({"name": "script_validation", "passed": bool(validation.get("passed")),
                        "detail": "; ".join(validation.get("errors", [])) or "passed"})
        fact_ok = bool(script.fact_check) and script.confidence >= 0  # value checked by stage gate
        content.append({"name": "fact_check_recorded", "passed": fact_ok,
                        "detail": f"confidence {script.confidence:.2f}"})
        content.append({"name": "sources_attached", "passed": bool(script.sources),
                        "detail": f"{len(script.sources)} source(s)"})
        rights_problems = []
        for r in renders:
            platform = PROFILES[r.profile].platform
            for aid in r.asset_ids:
                asset = s.get(MediaAsset, aid)
                ok, why = footage.usable(asset, [platform]) if asset else (False, "asset deleted")
                if not ok:
                    rights_problems.append(f"asset {aid} on {platform}: {why}")
        content.append({"name": "footage_rights", "passed": not rights_problems,
                        "detail": "; ".join(rights_problems) or "all assets verified"})

        out_renders, technical, publishable = [], True, True
        for r in renders:
            report = qc.inspect(Path(r.path), PROFILES[r.profile], expected_duration=voice.duration + 0.5,
                                cues=r.edit_decision_list.get("cues"), placeholder_voice=voice.is_placeholder)
            r.qc = report
            tech = qc.technical_pass(report)
            r.qc_passed = tech
            technical &= tech
            publishable &= report["passed"]
            out_renders.append({"render_id": r.id, "profile": r.profile, "technical_passed": tech,
                                "publishable": report["passed"],
                                "failed": [c["name"] for c in report["checks"] if not c["passed"]]})

        test_only = sorted({aid for r in renders for aid in r.asset_ids
                            if (a := s.get(MediaAsset, aid)) is not None and footage.is_test_only(a)})
        blockers = [c["name"] + ": " + c["detail"] for c in content if not c["passed"]]
        if test_only:
            blockers.append(f"uses test-only assets {test_only}; not publishable")
        if voice.is_placeholder:
            blockers.append("placeholder voiceover (no TTS provider configured)")
        publishable = publishable and not blockers
        for r in renders:  # the publishing manager reads render.qc["passed"]
            r.qc = {**r.qc, "passed": bool(r.qc.get("passed")) and not blockers, "content_blockers": blockers}
        return QCOutput(technical_passed=technical and all(c["passed"] for c in content),
                        publishable=publishable, renders=out_renders, content_checks=content,
                        blockers=blockers)

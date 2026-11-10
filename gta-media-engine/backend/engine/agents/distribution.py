"""Agent 9 (Publishing) and Agent 10 (Analytics)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from ..analytics import analyst, collector
from ..publishing import manager
from .base import Agent, AgentContext


class PublishInput(BaseModel):
    process_due: bool = True


class PublishOutput(BaseModel):
    sent: int = 0
    blocked_or_waiting: int = 0
    dry_run: int = 0


class PublishingAgent(Agent[PublishInput, PublishOutput]):
    name = "publishing"
    responsibility = "Distribute approved content within mode, pause, eligibility and rate-limit controls."
    input_model, output_model = PublishInput, PublishOutput
    max_attempts = 1  # the manager retries per publication with backoff

    def __init__(self, http=None):
        super().__init__()
        self.http = http

    def execute(self, ctx: AgentContext, payload: PublishInput) -> PublishOutput:
        return PublishOutput(**manager.process_due(ctx.session, http=self.http))


class AnalyticsInput(BaseModel):
    collect: bool = True
    weekly_report: bool = False


class AnalyticsOutput(BaseModel):
    snapshots: int = 0
    errors: dict = Field(default_factory=dict)
    report_id: int | None = None
    next_focus: str = ""


class AnalyticsAgent(Agent[AnalyticsInput, AnalyticsOutput]):
    name = "analytics"
    responsibility = "Collect real metrics, compare videos with sound statistics, recommend what to make next."
    input_model, output_model = AnalyticsInput, AnalyticsOutput
    max_attempts = 1

    def __init__(self, http=None):
        super().__init__()
        self.http = http

    def execute(self, ctx: AgentContext, payload: AnalyticsInput) -> AnalyticsOutput:
        out = AnalyticsOutput()
        if payload.collect:
            res = collector.collect(ctx.session, http=self.http)
            out.snapshots, out.errors = res["snapshots"], res["errors"]
        if payload.weekly_report:
            report = analyst.weekly_report(ctx.session)
            out.report_id = report.id
            out.next_focus = report.body["all_time"]["answers"]["next_focus"]
        else:
            out.next_focus = analyst.analyse(ctx.session)["answers"]["next_focus"]
        return out

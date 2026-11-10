"""Agent framework.

Every agent declares a responsibility, a pydantic input and output model, and a
retry limit. ``Agent.run`` validates input, retries transient failures within the
limit, validates output, and records an ``AgentRun`` row. Safety gates
(``AgentBlocked``), missing credentials and budget exhaustion are never retried.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from ..costs import BudgetExceeded
from ..logs import get_logger
from ..models import AgentRun
from ..providers.http import NotConfigured, ProviderError

I = TypeVar("I", bound=BaseModel)
O = TypeVar("O", bound=BaseModel)


class AgentBlocked(RuntimeError):
    """A safety gate refused to proceed (facts, rights, policy). Not retried."""


class AgentFailed(RuntimeError):
    pass


@dataclass
class AgentContext:
    session: Session
    job_id: int | None = None


class Agent(Generic[I, O]):
    name: str = "agent"
    responsibility: str = ""
    input_model: type[BaseModel] = BaseModel
    output_model: type[BaseModel] = BaseModel
    max_attempts: int = 2

    def __init__(self) -> None:
        self.log = get_logger(f"agent.{self.name}")

    def execute(self, ctx: AgentContext, payload: I) -> O:  # pragma: no cover - abstract
        raise NotImplementedError

    def run(self, ctx: AgentContext, payload: I | dict) -> O:
        if isinstance(payload, dict):
            payload = self.input_model.model_validate(payload)  # type: ignore[assignment]
        started = time.monotonic()
        attempt = 0
        last_error: Exception | None = None
        while attempt < self.max_attempts:
            attempt += 1
            try:
                result = self.execute(ctx, payload)  # type: ignore[arg-type]
                if not isinstance(result, self.output_model):
                    result = self.output_model.model_validate(result)
                self._record(ctx, "succeeded", attempt, started, None, result)
                return result  # type: ignore[return-value]
            except (AgentBlocked, BudgetExceeded, NotConfigured) as exc:
                self._record(ctx, "blocked", attempt, started, exc, None)
                raise
            except ValidationError as exc:
                last_error = exc
                self.log.warning("output validation failed (attempt %s): %s", attempt, exc)
            except ProviderError as exc:
                last_error = exc
                if not exc.transient:
                    break
                self.log.warning("transient provider error (attempt %s): %s", attempt, exc)
                time.sleep(min(2 ** attempt, 10))
            except Exception as exc:  # noqa: BLE001 - recorded and re-raised as AgentFailed
                last_error = exc
                self.log.exception("agent error (attempt %s)", attempt)
        self._record(ctx, "failed", attempt, started, last_error, None)
        raise AgentFailed(f"{self.name} failed after {attempt} attempt(s): {last_error}") from last_error

    def _record(self, ctx: AgentContext, status: str, attempts: int, started: float,
                error: Exception | None, result: BaseModel | None) -> None:
        summary: dict = {}
        if result is not None:
            dumped = result.model_dump(mode="json")
            summary = {k: v for k, v in dumped.items() if isinstance(v, (int, float, str, bool))}
        ctx.session.add(
            AgentRun(
                agent=self.name,
                job_id=ctx.job_id,
                status=status,
                attempts=attempts,
                duration_ms=int((time.monotonic() - started) * 1000),
                error=None if error is None else f"{type(error).__name__}: {error}"[:2000],
                summary=summary,
            )
        )
        ctx.session.flush()

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from uuid import uuid4

from pydantic import BaseModel
from smart_slugify import slugify

from talkingdb.models.job.error import JobErrorCode
from talkingdb.models.job.stage import JobStage
from talkingdb.models.job.state import JobState
from talkingdb.models.job.type import JobType


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobModel(BaseModel):
    """Asynchronous document-ingestion job model.

    This model is intentionally persistence-agnostic. Repository/storage
    concerns live outside the model so queue/storage implementations remain
    swappable.

    Field ownership boundaries:
      - status_message:
            Short human-readable UI status text.
      - progress_details:
            Ephemeral runtime details. Not part of the stable API contract.
      - result_summary:
            Immutable terminal outcome summary.
    """

    job_id: str
    job_type: JobType

    state: JobState = JobState.QUEUED
    stage: Optional[JobStage] = None

    total_units: int = 0
    done_units: int = 0
    cancel_requested: bool = False

    result_graph_id: Optional[str] = None
    result_summary: Optional[Dict[str, Any]] = None
    progress_details: Optional[Dict[str, Any]] = None
    status_message: Optional[str] = None

    error_code: Optional[JobErrorCode] = None
    error_message: Optional[str] = None

    filename: Optional[str] = None
    file_size_bytes: Optional[int] = None
    temp_path: Optional[str] = None

    heartbeat_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    created_at: str = ""
    updated_at: str = ""

    # ------------------------------------------------------------------ ids
    @staticmethod
    def make_id(job_id: Optional[str] = None) -> str:
        """Return a stable, prefixed job id (generating one if absent)."""
        if not job_id:
            return f"job::{slugify(uuid4().hex)}"
        if job_id.startswith("job::"):
            return job_id
        return f"job::{slugify(job_id)}"

    @classmethod
    def new(
        cls,
        *,
        job_type: JobType,
        filename: Optional[str] = None,
    ) -> "JobModel":
        """Create a new queued job for the given job_type."""
        now = _now_iso()
        return cls(
            job_id=cls.make_id(),
            job_type=job_type,
            state=JobState.QUEUED,
            filename=filename,
            created_at=now,
            updated_at=now,
        )

    # -------------------------------------------------------------- helpers
    def is_terminal(self) -> bool:
        """Return whether the job is in a terminal state."""
        return self.state.is_terminal()

    def percent(self) -> Optional[int]:
        """Return completion percentage for UI display.

        Returns:
            None:
                Progress is indeterminate.
            int:
                Rounded completion percentage in range [0, 100].
        """
        if self.total_units <= 0:
            return None
        ratio = self.done_units / self.total_units
        return max(0, min(100, round(ratio * 100)))

    def to_status_payload(self) -> Dict[str, Any]:
        """The STABLE API contract surface.

        Consumers may couple only to these fields. ``progress_details`` and
        internal columns are deliberately excluded. ``stage`` is ``None`` on
        any terminal state.
        """
        return {
            "job_id": self.job_id,
            "job_type": self.job_type.value,
            "state": self.state.value,
            "stage": None if self.is_terminal() else (
                self.stage.value if self.stage else None
            ),
            "percent": self.percent(),
            "status_message": self.status_message,
            "result_graph_id": self.result_graph_id,
            "result_summary": self.result_summary,
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": self.error_message,
        }

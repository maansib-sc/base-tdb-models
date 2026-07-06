from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4
import random

from pydantic import BaseModel
from smart_slugify import slugify

from talkingdb.models.job.error import JobErrorCode
from talkingdb.models.job.stage import JobStage
from talkingdb.models.job.state import JobState
from talkingdb.models.job.type import JobType


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------- progress
# Each stage owns a slice of the 0-100 range, sized to its typical share of
# total job wall-clock time. This turns progress into a staircase that moves
# forward on every stage transition, instead of sitting at 0 until the one
# stage that happens to report unit-level progress (INDEXING) takes over.
#
# These are heuristic defaults. Revisit once real stage-duration data is
# collected from job_observability logs (grouped by file size/type) - don't
# let these numbers go stale forever.

validating = random.randint(0, 3)
parsing = random.randint(5, 9)
element_extraction = random.randint(1, 3)
tree_generation = random.randint(3, 7)
persisting = random.randint(3, 7)

indexing = 100 - (
    validating
    + parsing
    + element_extraction
    + tree_generation
    + persisting
)
_STAGE_WEIGHTS: Dict[JobStage, int] = {
    JobStage.VALIDATING: validating,
    JobStage.PARSING: parsing,
    JobStage.ELEMENT_EXTRACTION: element_extraction,
    JobStage.TREE_GENERATION: tree_generation,
    JobStage.INDEXING: indexing,
    JobStage.PERSISTING: persisting,
}


def _build_stage_progress(
    weights: Dict[JobStage, int],
) -> Dict[JobStage, Tuple[int, int]]:
    """Precompute each stage's (starting_floor, weight).

    Relies on ``JobStage``'s declaration order matching pipeline execution
    order (VALIDATING -> PARSING -> ELEMENT_EXTRACTION -> TREE_GENERATION ->
    INDEXING -> PERSISTING), so the floors accumulate correctly.
    """
    progress: Dict[JobStage, Tuple[int, int]] = {}
    running = 0
    for stage in JobStage:
        weight = weights.get(stage, 0)
        progress[stage] = (running, weight)
        running += weight
    return progress


_STAGE_PROGRESS = _build_stage_progress(_STAGE_WEIGHTS)


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

    session_id: Optional[str] = None

    namespace: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    suggested_queries: Optional[List[str]] = None

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
        session_id: Optional[str] = None,
        namespace: Optional[str] = None,
        title: Optional[str] = None,
        description: Optional[str] = None,
        suggested_queries: Optional[List[str]] = None,
    ) -> "JobModel":
        """Create a new queued job for the given job_type."""
        now = _now_iso()
        return cls(
            job_id=cls.make_id(),
            job_type=job_type,
            session_id=session_id,
            namespace=namespace,
            title=title,
            description=description,
            suggested_queries=suggested_queries,
            filename=filename,
            state=JobState.QUEUED,
            created_at=now,
            updated_at=now,
        )

    # -------------------------------------------------------------- helpers
    def is_terminal(self) -> bool:
        """Return whether the job is in a terminal state."""
        return self.state.is_terminal()

    def percent(self) -> int:
        """Return completion percentage for UI display.

        Progress is stage-weighted: each ``JobStage`` owns a fixed slice of
        the 0-100 range (see ``_STAGE_WEIGHTS``). Within the current stage,
        ``done_units``/``total_units`` (when known) advance the value
        smoothly across that stage's slice. Stages that don't report units
        (e.g. PARSING today) simply sit at their starting floor until the
        job moves to the next stage - still a forward step on every
        transition, rather than a flat 0 for the entire stage.

        Returns:
            int:
                0 when the job hasn't started or has no stage yet.
                Monotonically increasing through the stage floors while
                ONGOING.
                100 when job is COMPLETED.
                0 when CANCELLING/CANCELLED.
        """
        if self.state == JobState.COMPLETED:
            return 100
        if self.state in (JobState.CANCELLING, JobState.CANCELLED):
            return 0
        if self.stage is None:
            return 0

        floor, weight = _STAGE_PROGRESS.get(self.stage, (0, 0))

        local_ratio = 0.0
        if self.total_units > 0:
            local_ratio = max(0.0, min(1.0, self.done_units / self.total_units))

        return max(0, min(100, round(floor + weight * local_ratio)))

    def to_status_payload(self) -> Dict[str, Any]:
        """The STABLE API contract surface.

        Consumers may couple only to these fields. ``progress_details`` and
        internal columns are deliberately excluded. ``stage`` is ``None`` on
        any terminal state.
        """
        return {
            "job_id": self.job_id,
            "job_type": self.job_type.value,
            "session_id": self.session_id,
            "state": self.state.value,
            "stage": self.stage.value if self.stage else None,
            "progress": self.percent(),
            "status_message": self.status_message,
            "result_graph_id": self.result_graph_id,
            "file_name": self.filename,
            "file_size": self.file_size_bytes,
            "result_summary": self.result_summary,
            "error_code": self.error_code.value if self.error_code else None,
            "error_message": self.error_message,
        }

    def to_document_payload(self) -> Dict[str, Any]:
        return {
            "id": self.job_id,
            "namespace": self.namespace,
            "title": self.title or self.filename,
            "description": self.description,
            "suggested_queries": self.suggested_queries or [],
            "result_graph_id": self.result_graph_id,
            "state": self.state.value,
        }
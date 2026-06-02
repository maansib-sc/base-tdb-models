from enum import Enum


class JobType(str, Enum):
    """Kind of background operation a job represents."""

    DOCUMENT = "document"

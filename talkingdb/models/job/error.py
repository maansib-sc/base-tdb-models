from enum import Enum


class JobErrorCode(str, Enum):
    """Structured failure taxonomy.

    Stored on a failed job alongside a free-text ``error_message``. The code
    is the stable, machine-readable contract; the message is for humans and
    may change.
    """

    VALIDATION_ERROR = "VALIDATION_ERROR"   # bad type / too large / bad input
    PARSE_ERROR = "PARSE_ERROR"             # content-elementizer / docx failure
    INDEX_ERROR = "INDEX_ERROR"             # tokenizer / symbol-gen / graph build
    PERSIST_ERROR = "PERSIST_ERROR"         # SQLite write failure
    TIMEOUT = "TIMEOUT"                     # exceeded MAX_JOB_DURATION
    STUCK = "STUCK"                         # heartbeat alive, no progress for too long
    INTERNAL_ERROR = "INTERNAL_ERROR"       # unclassified / orphaned

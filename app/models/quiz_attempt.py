from datetime import datetime

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, IndexModel

from app.core.clock import utcnow
from app.models.user_curriculum import Learner


class QuizAttempt(Document):
    """One answer to one quiz question (collection quiz_attempts). An audit trail for
    analytics; the course document holds the running totals used for progress."""

    owner_id: PydanticObjectId
    learner: Learner
    user_curriculum_id: PydanticObjectId
    curriculum_id: PydanticObjectId
    module_number: int
    lesson_number: int
    quiz_index: int  # 0-based within the lesson
    selected_index: int
    is_correct: bool
    attempt_number: int  # 1-based, per quiz question
    created_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "quiz_attempts"
        indexes = [
            IndexModel(
                [
                    ("user_curriculum_id", ASCENDING),
                    ("module_number", ASCENDING),
                    ("lesson_number", ASCENDING),
                    ("quiz_index", ASCENDING),
                    ("attempt_number", ASCENDING),
                ],
                unique=True,
                name="one_row_per_attempt",
            ),
            IndexModel([("curriculum_id", ASCENDING)], name="curriculum"),
        ]

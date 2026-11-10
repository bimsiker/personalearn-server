from datetime import datetime
from typing import Literal

from beanie import Document, PydanticObjectId
from pydantic import BaseModel, Field
from pymongo import ASCENDING, DESCENDING, IndexModel

from app.core.clock import utcnow


class StoredFile(BaseModel):
    backend: str  # "local" | "cloudinary"
    key: str  # random storage key; never a user-supplied name
    size_bytes: int
    file_type: Literal["pdf", "docx", "doc"] = "pdf"  # detected from content, not the name
    content_type: str = "application/pdf"
    original_filename: str  # sanitized; for display only
    sha256: str


class CurriculumMetadata(BaseModel):
    institution_name: str | None = None
    tutor_name: str | None = None
    date_created: str | None = None
    professor_contact: str | None = None
    course_requirements: str | None = None
    technology_requirements: str | None = None
    preliminary_info: str | None = None


class Assignment(BaseModel):
    """Who this material is assigned to (teacher -> student, parent -> child). Milestone 3."""

    learner_type: Literal["user", "child"]
    learner_id: PydanticObjectId


class Curriculum(Document):
    title: str
    description: str = ""
    file: StoredFile
    extracted_text: str = ""
    text_truncated: bool = False
    page_count: int | None = None  # None when the document doesn't record pages
    metadata: CurriculumMetadata = Field(default_factory=CurriculumMetadata)
    created_by: PydanticObjectId
    assigned_to: list[Assignment] = Field(default_factory=list)
    assigned_by: PydanticObjectId | None = None
    is_published: bool = True
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    class Settings:
        name = "curricula"
        indexes = [
            IndexModel(
                [("created_by", ASCENDING), ("created_at", DESCENDING)], name="owner_newest"
            ),
            IndexModel([("assigned_to.learner_id", ASCENDING)], name="assigned_learner"),
        ]

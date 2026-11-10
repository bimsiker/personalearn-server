from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models.curriculum import Curriculum
from app.schemas.common import CamelModel

FileTypeOut = Literal["pdf", "docx", "doc"]


class CurriculumMetadataOut(CamelModel):
    institution_name: str | None = None
    tutor_name: str | None = None
    date_created: str | None = None
    professor_contact: str | None = None
    course_requirements: str | None = None
    technology_requirements: str | None = None
    preliminary_info: str | None = None


class CurriculumOut(CamelModel):
    """Mirrors the frontend's `Curriculum` type (src/types/curriculum.ts).

    Deliberately omits the extracted text and storage key; the file is fetched through
    GET /curriculum/{id}/file, which returns a short-lived link.
    """

    id: str = Field(alias="_id")
    title: str
    description: str
    created_by: str
    metadata: CurriculumMetadataOut
    is_published: bool
    created_at: datetime
    updated_at: datetime
    file_name: str
    file_type: FileTypeOut
    file_size_bytes: int
    page_count: int | None = Field(description="None when the document doesn't record pages")
    has_text: bool = Field(description="False for scanned/image-only files with no readable text")
    has_lesson: bool = Field(description="True once the user has personalized this material")
    assigned_by_teacher_name: str | None = None

    @classmethod
    def from_doc(cls, doc: Curriculum, *, has_lesson: bool = False) -> "CurriculumOut":
        return cls(
            id=str(doc.id),
            title=doc.title,
            description=doc.description,
            created_by=str(doc.created_by),
            metadata=CurriculumMetadataOut(**doc.metadata.model_dump()),
            is_published=doc.is_published,
            created_at=doc.created_at,
            updated_at=doc.updated_at,
            file_name=doc.file.original_filename,
            file_type=doc.file.file_type,
            file_size_bytes=doc.file.size_bytes,
            page_count=doc.page_count,
            has_text=bool(doc.extracted_text),
            has_lesson=has_lesson,
        )


class FileLinkResponse(CamelModel):
    url: str
    expires_in: int = Field(description="Seconds until the link stops working")
    file_type: FileTypeOut
    opens_in_browser: bool = Field(
        description="True for PDFs (open in a new tab); Word files download instead"
    )

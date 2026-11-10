from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status

from app.core.config import Settings
from app.core.deps import get_current_user, get_settings_dep, get_storage
from app.core.rate_limit import rate_limit
from app.models.user import User
from app.schemas.common import error_docs
from app.schemas.curriculum import CurriculumOut, FileLinkResponse
from app.services import curriculum as service
from app.services.storage import FileStorage

router = APIRouter(prefix="/curriculum", tags=["curriculum"])


@router.post(
    "/create",
    status_code=status.HTTP_201_CREATED,
    response_model=CurriculumOut,
    summary="Upload a curriculum (PDF, .docx or .doc; max 25 MB); its text is extracted",
    responses=error_docs(401, 413, 422, 429),
    dependencies=[rate_limit("upload", limit=20, window_seconds=3600)],
)
async def create_curriculum(
    title: str = Form(min_length=1, max_length=200),
    description: str = Form(default="", max_length=2000),
    file: UploadFile = File(description="The curriculum: PDF, Word .docx or Word .doc"),
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    storage: FileStorage = Depends(get_storage),
) -> CurriculumOut:
    doc = await service.create_curriculum(
        owner=user,
        title=" ".join(title.split()),
        description=description.strip(),
        upload=file,
        settings=settings,
        storage=storage,
    )
    return CurriculumOut.from_doc(doc)


@router.get(
    "",
    response_model=list[CurriculumOut],
    summary="Your uploaded materials, newest first",
    responses=error_docs(401),
)
async def list_curricula(
    limit: int = Query(default=100, ge=1, le=200),
    user: User = Depends(get_current_user),
) -> list[CurriculumOut]:
    return [CurriculumOut.from_doc(doc) for doc in await service.list_curricula(user, limit=limit)]


@router.get(
    "/{curriculum_id}",
    response_model=CurriculumOut,
    summary="One of your materials",
    responses=error_docs(401, 404),
)
async def get_curriculum(
    curriculum_id: str, user: User = Depends(get_current_user)
) -> CurriculumOut:
    return CurriculumOut.from_doc(await service.get_owned_curriculum(curriculum_id, user))


async def _file_link(
    curriculum_id: str,
    request: Request,
    user: User,
    settings: Settings,
    storage: FileStorage,
) -> FileLinkResponse:
    doc = await service.get_owned_curriculum(curriculum_id, user)
    url = storage.download_url(
        doc.file.key,
        filename=doc.file.original_filename,
        file_type=doc.file.file_type,
        base_url=str(request.base_url),
    )
    return FileLinkResponse(
        url=url,
        expires_in=settings.file_link_ttl_seconds,
        file_type=doc.file.file_type,
        opens_in_browser=doc.file.file_type == "pdf",
    )


@router.get(
    "/{curriculum_id}/file",
    response_model=FileLinkResponse,
    summary="A short-lived link to the original file (PDFs open in a new tab; Word downloads)",
    responses=error_docs(401, 404),
)
async def file_link(
    curriculum_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    storage: FileStorage = Depends(get_storage),
) -> FileLinkResponse:
    return await _file_link(curriculum_id, request, user, settings, storage)


@router.get(
    "/{curriculum_id}/pdf",
    response_model=FileLinkResponse,
    summary="Alias of /{id}/file (kept because the frontend's TODOs reference it)",
    responses=error_docs(401, 404),
)
async def pdf_link(
    curriculum_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    storage: FileStorage = Depends(get_storage),
) -> FileLinkResponse:
    return await _file_link(curriculum_id, request, user, settings, storage)


@router.delete(
    "/{curriculum_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Delete one of your materials and its file",
    responses=error_docs(401, 404),
)
async def delete_curriculum(
    curriculum_id: str,
    user: User = Depends(get_current_user),
    storage: FileStorage = Depends(get_storage),
) -> Response:
    doc = await service.get_owned_curriculum(curriculum_id, user)
    await service.delete_curriculum(doc, user, storage)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

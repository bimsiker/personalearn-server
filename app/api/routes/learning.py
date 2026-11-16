from fastapi import APIRouter, Depends, Path, status

from app.core.config import Settings
from app.core.deps import get_ai_provider, get_current_user, get_jobs, get_settings_dep
from app.core.rate_limit import rate_limit
from app.models.user import User
from app.schemas.common import error_docs
from app.schemas.learning import (
    LessonRef,
    LessonResponse,
    MicroLessonOut,
    PreferencesIn,
    UserCurriculumOut,
)
from app.services import curriculum as curriculum_service
from app.services import learning as service
from app.services.ai import AIProvider
from app.services.jobs import JobRunner

router = APIRouter(tags=["learning"])


@router.post(
    "/curriculum/{curriculum_id}/enroll",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=UserCurriculumOut,
    summary="Personalize a material: saves your preferences and starts building the course",
    description=(
        'Returns at once with `generationStatus: "queued"`. Poll '
        "GET /curriculum/{id}/user-curriculum every 2-3 seconds until it is `ready` or "
        "`failed`. If it failed, call this again to retry (with the same or new preferences)."
    ),
    responses=error_docs(401, 404, 409, 422, 429),
    dependencies=[rate_limit("enroll", limit=10, window_seconds=3600)],
)
async def enroll(
    curriculum_id: str,
    body: PreferencesIn,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    jobs: JobRunner = Depends(get_jobs),
    ai: AIProvider = Depends(get_ai_provider),
) -> UserCurriculumOut:
    curriculum = await curriculum_service.get_owned_curriculum(curriculum_id, user)
    course = await service.enroll(
        curriculum, user, body.to_model(), settings=settings, jobs=jobs, ai=ai
    )
    return UserCurriculumOut.from_doc(course)


@router.get(
    "/curriculum/{curriculum_id}/user-curriculum",
    response_model=UserCurriculumOut,
    summary="Your personalized course for a material (poll this while it is generating)",
    responses=error_docs(401, 404),
)
async def get_user_curriculum(
    curriculum_id: str, user: User = Depends(get_current_user)
) -> UserCurriculumOut:
    return UserCurriculumOut.from_doc(await service.course_for_curriculum(curriculum_id, user))


@router.get(
    "/user-curricula/{user_curriculum_id}/lessons/{module_number}/{lesson_number}",
    response_model=LessonResponse,
    summary="One lesson (module and lesson numbers start at 1)",
    responses=error_docs(401, 404, 409),
)
async def get_lesson(
    user_curriculum_id: str,
    module_number: int = Path(ge=1, le=100),
    lesson_number: int = Path(ge=1, le=100),
    user: User = Depends(get_current_user),
) -> LessonResponse:
    course = await service.owned_course(user_curriculum_id, user)
    service.require_ready(course)
    module, lesson = service.find_lesson(course, module_number, lesson_number)
    previous, following = service.neighbours(course, module_number, lesson_number)
    return LessonResponse(
        user_curriculum_id=str(course.id),
        curriculum_id=str(course.curriculum_id),
        course_title=course.course_title,
        lesson=MicroLessonOut.build(module, lesson),
        previous=LessonRef(module_number=previous[0], lesson_number=previous[1])
        if previous
        else None,
        next=LessonRef(module_number=following[0], lesson_number=following[1])
        if following
        else None,
    )

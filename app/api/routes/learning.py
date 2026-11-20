from fastapi import APIRouter, Depends, Path, status

from app.core.config import Settings
from app.core.deps import get_ai_provider, get_current_user, get_jobs, get_settings_dep
from app.core.rate_limit import rate_limit
from app.models.user import User
from app.schemas.common import error_docs
from app.schemas.learning import (
    AttemptRequest,
    AttemptResponse,
    CompleteResponse,
    ExplainRequest,
    ExplainResponse,
    LessonProgressOut,
    LessonRef,
    LessonResponse,
    MicroLessonOut,
    PreferencesIn,
    ProgressOut,
    UserCurriculumOut,
)
from app.services import curriculum as curriculum_service
from app.services import learning as service
from app.services import progress, quizzes
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


def _ref(position: tuple[int, int] | None) -> LessonRef | None:
    return LessonRef(module_number=position[0], lesson_number=position[1]) if position else None


@router.post(
    "/curriculum/{user_curriculum_id}/attempt",
    response_model=AttemptResponse,
    summary="Answer one quiz question; the server checks it and updates your progress",
    description=(
        "`quizIndex` is the question's position in the lesson (0-based); `selectedIndex` is "
        "the chosen option (0-3). The correct answer and explanation are returned once the "
        "question is answered correctly or after 3 wrong tries; before that, ask "
        "POST /curriculum/{ucId}/explain for a hint."
    ),
    responses=error_docs(401, 404, 409, 422, 429),
    dependencies=[rate_limit("quiz-attempt", limit=120, window_seconds=60)],
)
async def attempt(
    user_curriculum_id: str,
    body: AttemptRequest,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
) -> AttemptResponse:
    result = await quizzes.attempt(
        user_curriculum_id,
        user,
        module_number=body.module_number,
        lesson_number=body.lesson_number,
        quiz_index=body.quiz_index,
        selected_index=body.selected_index,
        settings=settings,
    )
    quiz, outcome, course = result.quiz, result.outcome, result.located.course
    return AttemptResponse(
        is_correct=outcome.is_correct,
        attempt_number=outcome.attempt_number,
        selected_index=body.selected_index,
        revealed=outcome.revealed,
        correct_index=quiz.correct_index if outcome.revealed else None,
        explanation=quiz.explanation if outcome.revealed else None,
        tries_before_reveal=None
        if outcome.revealed
        else max(settings.quiz_reveal_after_attempts - quiz.attempts, 0),
        lesson=LessonProgressOut.build(result.located.module, result.located.lesson),
        lesson_just_completed=outcome.lesson_just_completed,
        overall_progress=course.overall_progress,
        course_completed=course.is_completed,
        course_just_completed=outcome.course_just_completed,
    )


@router.post(
    "/curriculum/{user_curriculum_id}/explain",
    response_model=ExplainResponse,
    summary="Ask the tutor about your answer to a question (answer it first)",
    responses=error_docs(401, 404, 409, 422, 429, 503),
    dependencies=[rate_limit("tutor", limit=30, window_seconds=3600)],
)
async def explain(
    user_curriculum_id: str,
    body: ExplainRequest,
    user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings_dep),
    ai: AIProvider = Depends(get_ai_provider),
) -> ExplainResponse:
    text, revealed = await quizzes.explain(
        user_curriculum_id,
        user,
        module_number=body.module_number,
        lesson_number=body.lesson_number,
        quiz_index=body.quiz_index,
        selected_index=body.selected_index,
        settings=settings,
        ai=ai,
    )
    return ExplainResponse(explanation=text, answer_revealed=revealed)


@router.post(
    "/user-curricula/{user_curriculum_id}/lessons/{module_number}/{lesson_number}/complete",
    response_model=CompleteResponse,
    summary="Mark a lesson as done (safe to call more than once)",
    responses=error_docs(401, 404, 409),
)
async def complete_lesson(
    user_curriculum_id: str,
    module_number: int = Path(ge=1, le=100),
    lesson_number: int = Path(ge=1, le=100),
    user: User = Depends(get_current_user),
) -> CompleteResponse:
    located, lesson_done, course_done = await quizzes.complete_lesson(
        user_curriculum_id, user, module_number=module_number, lesson_number=lesson_number
    )
    course = located.course
    return CompleteResponse(
        lesson=LessonProgressOut.build(located.module, located.lesson),
        lesson_just_completed=lesson_done,
        overall_progress=course.overall_progress,
        course_completed=course.is_completed,
        course_just_completed=course_done,
        next=_ref(progress.next_lesson(course)),
    )


@router.get(
    "/progress/{user_curriculum_id}",
    response_model=ProgressOut,
    summary="Progress for one course: per module and lesson, scores, and where to resume",
    responses=error_docs(401, 404, 409),
)
async def get_progress(
    user_curriculum_id: str, user: User = Depends(get_current_user)
) -> ProgressOut:
    course = await service.owned_course(user_curriculum_id, user)
    service.require_ready(course)
    return ProgressOut.from_doc(course, progress.next_lesson(course))

"""Personalized courses: enroll (queue generation), run the generation job, read lessons.

Generation runs in the background (services/jobs.py). The frontend polls
GET /curriculum/{id}/user-curriculum until `generationStatus` is "ready" or "failed".
Job writes are conditional updates, never full-document saves, so a course deleted
mid-generation stays deleted.
"""

import logging
from typing import Any

from beanie import PydanticObjectId
from pymongo.errors import DuplicateKeyError

from app.core.clock import utcnow
from app.core.config import Settings
from app.core.errors import AppError
from app.models.curriculum import Curriculum
from app.models.user import User
from app.models.user_curriculum import (
    AvatarConfig,
    CourseModule,
    GenerationStatus,
    Learner,
    MicroLesson,
    PersonalizedMetadata,
    Preferences,
    Quiz,
    UserCurriculum,
)
from app.services.ai import AIProvider
from app.services.ai.agents import GenerationError, generate_lessons, generate_outline
from app.services.ai.schemas import LessonsRequest, Limits, OutlineRequest
from app.services.jobs import JobRunner

logger = logging.getLogger(__name__)

GENERIC_FAILURE = "Something went wrong while building your course. Please try again."
INTERRUPTED = "Course generation was interrupted. Please try again."
_ACTIVE = [GenerationStatus.QUEUED.value, GenerationStatus.GENERATING.value]


def limits_from(settings: Settings) -> Limits:
    return Limits(
        max_modules=settings.max_modules,
        max_lessons_per_module=settings.max_lessons_per_module,
        max_quizzes_per_lesson=settings.max_quizzes_per_lesson,
    )


def _not_found(what: str = "course") -> AppError:
    return AppError(404, "NOT_FOUND", f"We couldn't find that {what}.")


def _collection():
    return UserCurriculum.get_pymongo_collection()


# ============================================================ enroll


def _avatar_config(prefs: Preferences) -> AvatarConfig:
    """Text/audio learners get no video, so an avatar choice is ignored for them."""
    if not prefs.wants_video:
        return AvatarConfig()
    return AvatarConfig(avatar_id=prefs.avatar_id, voice_id=prefs.voice_id)


def _fresh_state(prefs: Preferences) -> dict[str, Any]:
    """Fields a retried enrollment is reset to (a new one gets the same via defaults)."""
    return {
        "preferences": prefs.model_dump(),
        "avatar_config": _avatar_config(prefs).model_dump(),
        "course_title": "",
        "metadata": PersonalizedMetadata().model_dump(),
        "modules": [],
        "total_modules": 0,
        "overall_progress": 0,
        "is_completed": False,
        "generation_status": GenerationStatus.QUEUED.value,
        "generation_error": None,
        "generation_started_at": None,
        "generation_finished_at": None,
        "video_generation_status": "not_started",
        "video_generation_progress": 0,
    }


async def enroll(
    curriculum: Curriculum,
    user: User,
    prefs: Preferences,
    *,
    settings: Settings,
    jobs: JobRunner,
    ai: AIProvider,
) -> UserCurriculum:
    """Create the learner's course and queue its generation. A failed course can be
    retried by enrolling again; any other existing course is a 409."""
    if not curriculum.extracted_text.strip():
        raise AppError(
            422,
            "NO_READABLE_TEXT",
            "We couldn't find any readable text in this file, so we can't build a course "
            "from it. Try a text-based PDF or Word file instead of a scan.",
        )

    learner = Learner(type="user", id=user.id)  # Milestone 3: child profiles
    already = AppError(409, "ALREADY_ENROLLED", "You've already personalized this material.")
    existing = await UserCurriculum.find_one(
        {"curriculum_id": curriculum.id, "learner.type": learner.type, "learner.id": learner.id}
    )
    if existing is not None:
        if existing.generation_status != GenerationStatus.FAILED:
            raise already
        now = utcnow()
        result = await _collection().update_one(
            {"_id": existing.id, "generation_status": GenerationStatus.FAILED.value},
            {"$set": {**_fresh_state(prefs), "enrolled_at": now, "updated_at": now}},
        )
        if result.modified_count == 0:  # another request retried it first
            raise already
        course = await UserCurriculum.get(existing.id)
        logger.info("Course generation retried course=%s user=%s", course.id, user.id)
    else:
        course = UserCurriculum(
            owner_id=user.id,
            learner=learner,
            curriculum_id=curriculum.id,
            preferences=prefs,
            avatar_config=_avatar_config(prefs),
        )
        try:
            await course.insert()
        except DuplicateKeyError as exc:  # a double-click raced us
            raise already from exc
        await User.find_one({"_id": user.id}).update({"$inc": {"curricula_enrolled": 1}})
        logger.info(
            "Enrolled course=%s curriculum=%s user=%s style=%s pace=%s media=%s",
            course.id,
            curriculum.id,
            user.id,
            prefs.learning_style,
            prefs.learning_pace,
            prefs.media_preference,
        )

    course_id = course.id
    jobs.submit(
        f"generate-course:{course_id}",
        lambda: run_generation(course_id, ai=ai, settings=settings),
    )
    return course


# ============================================================ generation job


async def _finish(course_id: PydanticObjectId, fields: dict[str, Any]) -> bool:
    """Write the job's result only if the course is still generating (not deleted/reset)."""
    now = utcnow()
    result = await _collection().update_one(
        {"_id": course_id, "generation_status": GenerationStatus.GENERATING.value},
        {"$set": {**fields, "generation_finished_at": now, "updated_at": now}},
    )
    return result.modified_count == 1


async def _fail(course_id: PydanticObjectId, message: str) -> None:
    await _finish(
        course_id,
        {"generation_status": GenerationStatus.FAILED.value, "generation_error": message},
    )


async def run_generation(
    course_id: PydanticObjectId, *, ai: AIProvider, settings: Settings
) -> None:
    claimed = await _collection().update_one(
        {"_id": course_id, "generation_status": GenerationStatus.QUEUED.value},
        {
            "$set": {
                "generation_status": GenerationStatus.GENERATING.value,
                "generation_started_at": utcnow(),
            }
        },
    )
    if claimed.modified_count == 0:
        logger.info("Generation skipped (deleted or already running) course=%s", course_id)
        return

    course = await UserCurriculum.get(course_id)
    curriculum = await Curriculum.get(course.curriculum_id) if course else None
    if course is None or curriculum is None:
        logger.warning("Generation stopped: course or material deleted course=%s", course_id)
        await _fail(course_id, "The material for this course no longer exists.")
        return

    limits = limits_from(settings)
    logger.info("Generation started course=%s provider=%s", course_id, ai.name)
    try:
        outline = await generate_outline(
            ai,
            OutlineRequest(
                source_title=curriculum.title,
                source_text=curriculum.extracted_text,
                preferences=course.preferences,
                limits=limits,
            ),
        )
        modules: list[CourseModule] = []
        for number, module_outline in enumerate(outline.modules, start=1):
            drafts = await generate_lessons(
                ai,
                LessonsRequest(
                    source_title=curriculum.title,
                    source_text=curriculum.extracted_text,
                    preferences=course.preferences,
                    limits=limits,
                    course_title=outline.course_title,
                    module_number=number,
                    module=module_outline,
                ),
            )
            modules.append(
                CourseModule(
                    module_number=number,
                    module_title=module_outline.module_title,
                    summary=module_outline.summary,
                    recommended_duration=module_outline.recommended_duration,
                    lessons=[
                        MicroLesson(
                            lesson_number=index,
                            lesson_title=draft.lesson_title,
                            duration=draft.duration,
                            script=draft.script,
                            quizzes=[Quiz(**quiz.model_dump()) for quiz in draft.quizzes],
                        )
                        for index, draft in enumerate(drafts.lessons, start=1)
                    ],
                )
            )
    except GenerationError as exc:
        logger.warning("Generation failed course=%s reason=%s", course_id, exc.message)
        await _fail(course_id, exc.message)
        return
    except Exception:
        logger.exception("Generation crashed course=%s", course_id)
        await _fail(course_id, GENERIC_FAILURE)
        return

    metadata = PersonalizedMetadata(
        summary=outline.summary,
        learning_objectives=outline.learning_objectives,
        key_takeaways=outline.key_takeaways,
        source_type=outline.source_type,
        estimated_total_duration=outline.estimated_total_duration,
    )
    saved = await _finish(
        course_id,
        {
            "generation_status": GenerationStatus.READY.value,
            "course_title": outline.course_title,
            "metadata": metadata.model_dump(),
            "modules": [module.model_dump() for module in modules],
            "total_modules": len(modules),
        },
    )
    lesson_count = sum(len(module.lessons) for module in modules)
    if saved:
        logger.info(
            "Generation finished course=%s modules=%d lessons=%d",
            course_id,
            len(modules),
            lesson_count,
        )
    else:
        logger.info("Generation result discarded (course deleted meanwhile) course=%s", course_id)


async def recover_interrupted() -> int:
    """At startup: jobs don't survive a restart, so mark leftovers failed (learner retries)."""
    now = utcnow()
    result = await _collection().update_many(
        {"generation_status": {"$in": _ACTIVE}},
        {
            "$set": {
                "generation_status": GenerationStatus.FAILED.value,
                "generation_error": INTERRUPTED,
                "generation_finished_at": now,
                "updated_at": now,
            }
        },
    )
    if result.modified_count:
        logger.warning(
            "Marked %d interrupted course generation(s) as failed", result.modified_count
        )
    return result.modified_count


# ============================================================ reading


async def course_for_curriculum(curriculum_id: str, user: User) -> UserCurriculum:
    try:
        oid = PydanticObjectId(curriculum_id)
    except Exception as exc:
        raise _not_found() from exc
    course = await UserCurriculum.find_one(
        {"curriculum_id": oid, "learner.type": "user", "learner.id": user.id}
    )
    if course is None:
        raise AppError(404, "NOT_ENROLLED", "You haven't personalized this material yet.")
    return course


async def owned_course(course_id: str, user: User) -> UserCurriculum:
    """404 for bad ids, missing courses and other people's courses alike."""
    try:
        oid = PydanticObjectId(course_id)
    except Exception as exc:
        raise _not_found() from exc
    course = await UserCurriculum.find_one({"_id": oid, "owner_id": user.id})
    if course is None:
        raise _not_found()
    return course


def require_ready(course: UserCurriculum) -> None:
    if course.generation_status != GenerationStatus.READY:
        raise AppError(
            409,
            "COURSE_NOT_READY",
            "This course is still being built."
            if course.generation_status in (GenerationStatus.QUEUED, GenerationStatus.GENERATING)
            else "This course couldn't be built. Personalize the material again to retry.",
        )


def find_lesson(
    course: UserCurriculum, module_number: int, lesson_number: int
) -> tuple[CourseModule, MicroLesson]:
    module = next((m for m in course.modules if m.module_number == module_number), None)
    lesson = (
        next((x for x in module.lessons if x.lesson_number == lesson_number), None)
        if module
        else None
    )
    if module is None or lesson is None:
        raise _not_found("lesson")
    return module, lesson


def neighbours(
    course: UserCurriculum, module_number: int, lesson_number: int
) -> tuple[tuple[int, int] | None, tuple[int, int] | None]:
    """(previous, next) lesson positions across module boundaries, or None at either end."""
    order = [(m.module_number, x.lesson_number) for m in course.modules for x in m.lessons]
    index = order.index((module_number, lesson_number))
    previous = order[index - 1] if index > 0 else None
    following = order[index + 1] if index + 1 < len(order) else None
    return previous, following


async def enrolled_curriculum_ids(user: User) -> set[PydanticObjectId]:
    ids = await _collection().distinct(
        "curriculum_id", {"learner.type": "user", "learner.id": user.id}
    )
    return set(ids)


# ============================================================ cleanup


async def delete_for_curriculum(curriculum_id: PydanticObjectId) -> int:
    """Delete every course built from this material and fix the owners' counters."""
    owners = (
        await _collection()
        .find({"curriculum_id": curriculum_id, "learner.type": "user"}, {"owner_id": 1})
        .to_list()
    )
    result = await _collection().delete_many({"curriculum_id": curriculum_id})
    for row in owners:
        await User.find_one({"_id": row["owner_id"], "curricula_enrolled": {"$gt": 0}}).update(
            {"$inc": {"curricula_enrolled": -1}}
        )
    if result.deleted_count:
        logger.info("Deleted %d course(s) for curriculum=%s", result.deleted_count, curriculum_id)
    return result.deleted_count

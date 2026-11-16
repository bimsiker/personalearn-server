import asyncio
import logging

import pytest

from app.services.jobs import JobRunner


async def test_job_runs_and_runner_goes_idle():
    runner = JobRunner()
    done = []

    async def job():
        await asyncio.sleep(0)
        done.append(1)

    runner.submit("ok", job)
    assert runner.pending == 1
    await runner.wait_idle()
    assert done == [1] and runner.pending == 0


async def test_crashing_job_is_logged_not_raised(caplog):
    runner = JobRunner()

    async def job():
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR, logger="app.services.jobs"):
        runner.submit("bad", job)
        await runner.wait_idle()
    assert "Job crashed job=bad" in caplog.text


async def test_wait_idle_includes_jobs_submitted_by_jobs():
    runner = JobRunner()
    order = []

    async def child():
        order.append("child")

    async def parent():
        order.append("parent")
        runner.submit("child", child)

    runner.submit("parent", parent)
    await runner.wait_idle()
    assert order == ["parent", "child"]


async def test_shutdown_cancels_running_jobs():
    runner = JobRunner()
    started = asyncio.Event()

    async def forever():
        started.set()
        await asyncio.sleep(3600)

    runner.submit("forever", forever)
    await started.wait()
    await runner.shutdown()
    assert runner.pending == 0


async def test_wait_idle_times_out():
    runner = JobRunner()
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()

    runner.submit("blocked", blocked)
    with pytest.raises(TimeoutError):
        await runner.wait_idle(timeout=0.05)
    gate.set()
    await runner.wait_idle()

import pytest

from nanobot.cron.service import CronService
from nanobot.cron.types import CronSchedule


@pytest.mark.asyncio
async def test_cron_service_run_job_updates_state_and_calls_callback(tmp_path) -> None:
    calls: list[str] = []

    async def on_job(job) -> str | None:  # type: ignore[no-untyped-def]
        calls.append(job.id)
        return "ok"

    service = CronService(tmp_path / "jobs.json", on_job=on_job)
    job = service.add_job(
        name="t1",
        schedule=CronSchedule(kind="every", every_ms=1000),
        message="hello",
        deliver=False,
    )

    ok = await service.run_job(job.id, force=True)
    assert ok is True
    assert calls == [job.id]

    # State should reflect successful run.
    assert job.state.last_status == "ok"
    assert job.state.last_error is None
    assert job.state.last_run_at_ms is not None
    assert job.state.next_run_at_ms is not None


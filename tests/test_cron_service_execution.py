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


def test_cron_payload_type_persists_and_defaults(tmp_path) -> None:
    service = CronService(tmp_path / "jobs.json")
    j1 = service.add_job(
        name="rem1",
        schedule=CronSchedule(kind="every", every_ms=1000),
        message="Drink water",
        payload_type="reminder",
        deliver=True,
        to="123",
        channel="telegram",
    )
    assert j1.payload.type == "reminder"

    # Reload from disk and ensure we keep payload.type.
    service2 = CronService(tmp_path / "jobs.json")
    jobs = service2.list_jobs(include_disabled=True)
    assert len(jobs) == 1
    assert jobs[0].payload.type == "reminder"

    # Backwards compatibility: if payload.type is missing in JSON, default to "task".
    (tmp_path / "jobs.json").write_text(
        """{
  "version": 1,
  "jobs": [
    {
      "id": "abc12345",
      "name": "old",
      "enabled": true,
      "schedule": { "kind": "every", "everyMs": 1000 },
      "payload": { "kind": "agent_turn", "message": "hi", "deliver": false },
      "state": { "nextRunAtMs": 1, "lastRunAtMs": null, "lastStatus": null, "lastError": null },
      "createdAtMs": 0,
      "updatedAtMs": 0,
      "deleteAfterRun": false
    }
  ]
}"""
    )
    service3 = CronService(tmp_path / "jobs.json")
    jobs3 = service3.list_jobs(include_disabled=True)
    assert jobs3[0].payload.type == "task"

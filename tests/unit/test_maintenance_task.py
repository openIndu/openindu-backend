"""The Website scheduler must retain account cleanup without indexing jobs."""

from unittest.mock import MagicMock, patch

from app.shared.tasks.maintenance_task import MaintenanceScheduler


def test_scheduler_registers_only_authentication_maintenance():
    with patch("app.shared.tasks.maintenance_task.BackgroundScheduler") as scheduler_class:
        scheduler = MagicMock()
        scheduler.running = False
        scheduler_class.return_value = scheduler

        maintenance = MaintenanceScheduler()
        maintenance.start()

    job_ids = {call.kwargs["id"] for call in scheduler.add_job.call_args_list}
    assert job_ids == {"token_cleanup", "session_cleanup"}
    scheduler.start.assert_called_once()

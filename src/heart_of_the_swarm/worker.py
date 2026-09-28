import asyncio
import os
import socket
from contextlib import suppress

from heart_of_the_swarm.application import Application
from heart_of_the_swarm.observability import audit_event, audit_exception
from heart_of_the_swarm.repository import Repository


async def run_worker() -> None:
    application = Application(process="worker")
    await application.initialize()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    audit_event("worker.started", worker_id=worker_id)
    try:
        while True:
            async with application.database.session() as session:
                repository = Repository(session)
                recovered = await repository.recover_expired_runs(
                    application.settings.worker_max_attempts
                )
                run_id = await repository.claim_next_run(
                    worker_id, application.settings.worker_lease_seconds
                )
            if recovered:
                audit_event("worker.runs.recovered", count=recovered)
            if run_id is None:
                await asyncio.sleep(application.settings.worker_poll_seconds)
                continue
            try:
                await application.executor.execute(run_id, worker_id)
            except Exception as exc:
                audit_exception("worker.run.failed", worker_id=worker_id, run_id=run_id)
                async with application.database.session() as session:
                    await Repository(session).fail_run(run_id, exc)
    except asyncio.CancelledError:
        raise
    except Exception:
        audit_exception("worker.failed", worker_id=worker_id)
        raise
    finally:
        audit_event("worker.stopped", worker_id=worker_id)
        await application.close()


def main() -> None:
    with suppress(KeyboardInterrupt):
        asyncio.run(run_worker())


if __name__ == "__main__":
    main()

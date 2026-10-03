import asyncio
import os
import socket
from contextlib import suppress

from heart_of_the_swarm.application import Application
from heart_of_the_swarm.observability import audit_event, audit_exception
from heart_of_the_swarm.repositories import RunRepository, WorkflowRunRepository


async def run_worker() -> None:
    application = Application(process="worker")
    await application.initialize()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    prefer_workflow = True
    audit_event("worker.started", worker_id=worker_id)
    try:
        while True:
            await application.sync_mcp_tools()
            async with application.database.session() as session:
                agent_repository = RunRepository(session)
                workflow_repository = WorkflowRunRepository(session)
                recovered_agents = await agent_repository.recover_expired(
                    application.settings.worker_max_attempts
                )
                recovered_workflows = await workflow_repository.recover_expired()
                agent_run_id = None
                workflow_run_id = None
                if prefer_workflow:
                    workflow_run_id = await workflow_repository.claim_next(
                        worker_id, application.settings.worker_lease_seconds
                    )
                    if workflow_run_id is None:
                        agent_run_id = await agent_repository.claim_next(
                            worker_id, application.settings.worker_lease_seconds
                        )
                else:
                    agent_run_id = await agent_repository.claim_next(
                        worker_id, application.settings.worker_lease_seconds
                    )
                    if agent_run_id is None:
                        workflow_run_id = await workflow_repository.claim_next(
                            worker_id, application.settings.worker_lease_seconds
                        )
            prefer_workflow = not prefer_workflow
            recovered = recovered_agents + recovered_workflows
            if recovered:
                audit_event(
                    "worker.runs.recovered",
                    count=recovered,
                    agent_count=recovered_agents,
                    workflow_count=recovered_workflows,
                )
            if agent_run_id is None and workflow_run_id is None:
                await asyncio.sleep(application.settings.worker_poll_seconds)
                continue
            try:
                if workflow_run_id is not None:
                    await application.workflow_executor.execute(workflow_run_id, worker_id)
                elif agent_run_id is not None:
                    await application.executor.execute(agent_run_id, worker_id)
            except Exception as exc:
                run_id = workflow_run_id or agent_run_id
                audit_exception("worker.run.failed", worker_id=worker_id, run_id=run_id)
                if run_id is not None:
                    async with application.database.session() as session:
                        if workflow_run_id is not None:
                            await WorkflowRunRepository(session).fail(run_id, exc)
                        else:
                            await RunRepository(session).fail(run_id, exc)
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

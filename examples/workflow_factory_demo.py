"""Run the agent-creates-an-agent demonstration through the public API."""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from typing import Any
from uuid import uuid4

import httpx

SKILL_NAME = "workflow_spec_authoring"


def api_request(
    client: httpx.Client,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> Any:
    """Call one API endpoint and expose a concise failure to the demo operator."""
    response = client.request(method, path, json=payload)
    if response.is_error:
        raise RuntimeError(
            f"{method} {path} returned {response.status_code}: {response.text[:1000]}"
        )
    return response.json()


def wait_for_run(client: httpx.Client, run_id: str, timeout_seconds: int) -> dict[str, Any]:
    """Poll one durable workflow run until it reaches a terminal state."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = api_request(client, "GET", f"/api/v1/workflow-runs/{run_id}")
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(2)
    raise TimeoutError(f"Workflow run {run_id} did not finish within {timeout_seconds} seconds")


def save_and_version(client: httpx.Client, spec: dict[str, Any]) -> dict[str, Any]:
    """Persist a draft and publish the exact declaration as an immutable version."""
    workflow_id = spec["id"]
    api_request(
        client,
        "PUT",
        f"/api/v1/workflows/{workflow_id}",
        {
            "spec": spec,
            "editor": {"positions": {}, "viewport": {"x": 0, "y": 0, "zoom": 1}},
            "expected_revision": None,
        },
    )
    return api_request(client, "POST", f"/api/v1/workflows/{workflow_id}/versions", {})


def build_factory_spec(workflow_schema: dict[str, Any], model_id: str) -> dict[str, Any]:
    """Create the declarative workflow whose inline agent designs another workflow."""
    response_schema = copy.deepcopy(workflow_schema)
    response_schema["description"] = "A complete executable Heart of the Swarm WorkflowSpec."
    response_schema["required"] = list(response_schema["properties"])
    outputs = {name: {"to_state": f"$.candidate.{name}"} for name in response_schema["properties"]}
    return {
        "schema_version": "1",
        "id": str(uuid4()),
        "name": "Workflow Spec Factory Demo",
        "description": "Generates an executable tool-using workflow from natural language.",
        "input_schema": {
            "type": "object",
            "properties": {"request": {"type": "string"}},
            "required": ["request"],
            "additionalProperties": False,
        },
        "output_schema": None,
        "entrypoint": "input",
        "nodes": [
            {"id": "input", "type": "input", "name": "Request", "config": {}},
            {
                "id": "designer",
                "type": "agent",
                "name": "Workflow designer",
                "config": {
                    "source": {
                        "type": "inline",
                        "agent": {
                            "name": "WorkflowSpecDesigner",
                            "goal": "Design one valid executable Heart of the Swarm WorkflowSpec.",
                            "instructions": (
                                "Translate the request into the smallest valid WorkflowSpec. "
                                "Follow the provided skill exactly. Return only the structured "
                                "response requested by the runtime."
                            ),
                            "model": {
                                "provider": "openrouter",
                                "model_id": model_id,
                                "temperature": 0,
                                "max_tokens": 8000,
                            },
                            "tools": [],
                            "skills": [SKILL_NAME],
                        },
                    },
                    "inputs": {"request": {"from_state": "$.request"}},
                    "outputs": outputs,
                    "response_schema": response_schema,
                },
            },
            {
                "id": "output",
                "type": "output",
                "name": "Generated workflow",
                "config": {"outputs": {"workflow_spec": {"from_state": "$.candidate"}}},
            },
        ],
        "edges": [
            {"source": "input", "target": "designer"},
            {"source": "designer", "target": "output"},
        ],
    }


def queue_run(
    client: httpx.Client,
    version_id: str,
    input_data: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    """Create and await one durable workflow-version run."""
    accepted = api_request(
        client,
        "POST",
        f"/api/v1/workflow-versions/{version_id}/runs",
        {"input": input_data},
    )
    return wait_for_run(client, accepted["run_id"], timeout_seconds)


def generate_workflow(
    client: httpx.Client,
    factory_version_id: str,
    model_id: str,
    timeout_seconds: int,
    attempts: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ask the factory for a tool-using agent workflow, tolerating free-pool saturation."""
    request = (
        "Create a sequential WorkflowSpec with INPUT, one inline AGENT, and OUTPUT. "
        "The workflow input must contain required feed_url and request strings. Give the inline "
        "agent the rss_reader tool. It must call rss_reader exactly once with feed_url and "
        "max_items 5, identify the main themes, and answer the user's request in French. Return "
        f"the final text as result. Use model {model_id}. Do not use a connector node."
    )
    failures: list[str] = []
    for attempt in range(1, attempts + 1):
        run = queue_run(client, factory_version_id, {"request": request}, timeout_seconds)
        if run["status"] == "completed":
            return run["output"]["workflow_spec"], run
        failures.append(f"attempt {attempt}, run {run['run_id']}: {run.get('error')}")
        if attempt < attempts:
            time.sleep(3)
    raise RuntimeError("Factory execution failed: " + "; ".join(failures))


def execute_generated_workflow(
    client: httpx.Client,
    version_id: str,
    input_data: dict[str, Any],
    timeout_seconds: int,
    attempts: int,
) -> tuple[dict[str, Any], list[str]]:
    """Run the immutable generated version, preserving each external-provider attempt."""
    run_ids: list[str] = []
    failures: list[str] = []
    for attempt in range(1, attempts + 1):
        run = queue_run(client, version_id, input_data, timeout_seconds)
        run_ids.append(run["run_id"])
        result = (run.get("output") or {}).get("result")
        if run["status"] == "completed" and isinstance(result, str) and result.strip():
            return run, run_ids
        failures.append(f"attempt {attempt}, run {run['run_id']}: {run.get('error')}")
        if attempt < attempts:
            time.sleep(3)
    raise RuntimeError("Generated workflow execution failed: " + "; ".join(failures))


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Demonstrate an agent generating and running another tool-using agent."
    )
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--model-id", default="nvidia/nemotron-3-super-120b-a12b:free")
    parser.add_argument("--feed-url", default="https://feeds.bbci.co.uk/news/technology/rss.xml")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--factory-attempts", type=int, default=2)
    parser.add_argument("--execution-attempts", type=int, default=3)
    args = parser.parse_args()

    with httpx.Client(base_url=args.api_url.rstrip("/"), timeout=30) as client:
        api_request(client, "GET", "/ready")
        skills = api_request(client, "GET", "/api/v1/skills")["skills"]
        if SKILL_NAME not in skills:
            raise RuntimeError(f"Required Skill '{SKILL_NAME}' is not loaded")

        capabilities = api_request(client, "GET", "/api/v1/workflows/capabilities")
        factory_spec = build_factory_spec(capabilities["workflow_schema"], args.model_id)
        factory_version = save_and_version(client, factory_spec)
        print(f"Factory version created: {factory_version['id']}")
        generated_spec, factory_run = generate_workflow(
            client,
            factory_version["id"],
            args.model_id,
            args.timeout,
            args.factory_attempts,
        )

        validation = api_request(client, "POST", "/api/v1/workflows/validate", generated_spec)
        if not validation["valid"]:
            raise RuntimeError(f"Generated WorkflowSpec is invalid: {validation['issues']}")

        generated_version = save_and_version(client, generated_spec)
        print(
            f"Generated workflow validated and versioned: "
            f"{generated_spec['id']} / {generated_version['id']}"
        )
        generated_run, generated_run_ids = execute_generated_workflow(
            client,
            generated_version["id"],
            {
                "feed_url": args.feed_url,
                "request": (
                    "Présente les principaux thèmes des cinq articles les plus récents et cite "
                    "leurs titres."
                ),
            },
            args.timeout,
            args.execution_attempts,
        )
        result = generated_run.get("output", {}).get("result")

        print(
            json.dumps(
                {
                    "factory_workflow_id": factory_spec["id"],
                    "factory_version_id": factory_version["id"],
                    "factory_run_id": factory_run["run_id"],
                    "generated_workflow_id": generated_spec["id"],
                    "generated_version_id": generated_version["id"],
                    "generated_run_ids": generated_run_ids,
                    "successful_generated_run_id": generated_run["run_id"],
                    "result": result,
                },
                indent=2,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any


JOB_ID = "lingxing-wms-pending-count-daily-1700"
JOB_NAME = "LingXing WMS Pending Count"


def load_store(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "jobs": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_store(path: Path, store: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_message(workspace: Path) -> str:
    job_dir = workspace / "lingxing_wms_pending_count"
    runner = job_dir / "run_pending_count.py"
    output = job_dir / "output"
    return (
        f"Run the LingXing WMS pending-count job in {job_dir}. "
        f"Execute `python3 {runner}` exactly once. "
        f"Do not modify any files outside {output}. "
        f"If the script fails because login state expired or a captcha blocks the page, "
        f"leave the generated error artifacts in place and stop."
    )


def build_job(workspace: Path, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    now = int(time.time() * 1000)
    created_at = existing.get("createdAtMs") if existing else now
    return {
        "id": JOB_ID,
        "name": JOB_NAME,
        "description": "Capture LingXing WMS 待处理 footer total every day at 17:00 Asia/Shanghai.",
        "enabled": True,
        "createdAtMs": created_at,
        "updatedAtMs": now,
        "schedule": {
            "kind": "cron",
            "expr": "0 17 * * *",
            "tz": "Asia/Shanghai",
            "staggerMs": 0,
        },
        "sessionTarget": "isolated",
        "wakeMode": "now",
        "payload": {
            "kind": "agentTurn",
            "message": build_message(workspace),
            "thinking": "minimal",
            "timeoutSeconds": 300,
            "lightContext": True,
        },
        "delivery": {
            "mode": "none",
        },
        "state": existing.get("state", {}) if existing else {},
    }


def install_job(cron_store: Path, workspace: Path) -> None:
    store = load_store(cron_store)
    jobs = store.get("jobs")
    if not isinstance(jobs, list):
        jobs = []

    existing = None
    kept_jobs = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        if job.get("id") == JOB_ID or job.get("name") == JOB_NAME:
            existing = job
            continue
        kept_jobs.append(job)

    kept_jobs.append(build_job(workspace, existing))
    store["version"] = 1
    store["jobs"] = kept_jobs
    save_store(cron_store, store)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cron-store", required=True)
    parser.add_argument("--workspace", required=True)
    args = parser.parse_args()

    install_job(Path(args.cron_store), Path(args.workspace))
    print(f"Installed cron job '{JOB_NAME}' into {args.cron_store}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

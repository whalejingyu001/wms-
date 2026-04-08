#!/usr/bin/env python3

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
SCRAPER = BASE_DIR / "scripts" / "lingxing_wms_scraper.py"
CONFIG = BASE_DIR / "references" / "parcel-pending-total.job.json"
OUTPUT_DIR = BASE_DIR / "output"
LATEST_PATH = OUTPUT_DIR / "latest.json"
HISTORY_PATH = OUTPUT_DIR / "history.jsonl"
ERROR_PATH = OUTPUT_DIR / "last-error.txt"
WECOM_TARGET = os.environ.get("LINGXING_WMS_WECOM_TARGET", "JingYu")


def append_history(entry: dict[str, Any]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def write_error(message: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ERROR_PATH.write_text(message + "\n", encoding="utf-8")


def run_scrape() -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(SCRAPER),
        "scrape",
        "--config",
        str(CONFIG),
        "--output",
        str(LATEST_PATH),
    ]
    env = dict(**__import__("os").environ)
    env.setdefault("PYTHONPYCACHEPREFIX", "/tmp/pycache")
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        env=env,
    )


def load_latest() -> dict[str, Any]:
    return json.loads(LATEST_PATH.read_text(encoding="utf-8"))


def build_success_entry(payload: dict[str, Any], stdout: str) -> dict[str, Any]:
    record = (payload.get("records") or [{}])[0]
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "page_url": payload.get("page_url"),
        "captured_at": record.get("captured_at"),
        "status_tab_text": record.get("status_tab_text"),
        "status_tab_count": record.get("status_tab_count"),
        "footer_total_text": record.get("footer_total_text"),
        "footer_total_count": record.get("footer_total_count"),
        "stdout": stdout.strip(),
    }


def build_error_entry(stderr: str, stdout: str, returncode: int) -> dict[str, Any]:
    return {
        "ran_at": datetime.now(timezone.utc).isoformat(),
        "status": "error",
        "returncode": returncode,
        "stdout": stdout.strip(),
        "stderr": stderr.strip(),
    }


def build_wecom_message(entry: dict[str, Any]) -> str:
    count = entry.get("footer_total_count")
    captured_at = entry.get("captured_at") or entry.get("ran_at")
    return (
        "领星WMS 待处理数量汇报\n"
        f"时间: {captured_at}\n"
        f"数量: {count}条\n"
        "页面: https://wms.xlwms.com/outbound/parcel"
    )


def send_wecom_notification(message: str) -> subprocess.CompletedProcess[str]:
    cmd = [
        "openclaw",
        "message",
        "send",
        "--channel",
        "wecom",
        "--target",
        WECOM_TARGET,
        "--message",
        message,
    ]
    env = dict(os.environ)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(BASE_DIR),
        env=env,
    )


def main() -> int:
    result = run_scrape()

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "LingXing scrape failed"
        write_error(message)
        append_history(build_error_entry(result.stderr, result.stdout, result.returncode))
        print(message, file=sys.stderr)
        return result.returncode

    payload = load_latest()
    entry = build_success_entry(payload, result.stdout)
    notification_message = build_wecom_message(entry)
    notify_result = send_wecom_notification(notification_message)
    entry["wecom_target"] = WECOM_TARGET
    entry["wecom_sent"] = notify_result.returncode == 0
    entry["wecom_stdout"] = notify_result.stdout.strip()
    entry["wecom_stderr"] = notify_result.stderr.strip()
    append_history(entry)
    if notify_result.returncode != 0:
        message = notify_result.stderr.strip() or notify_result.stdout.strip() or "WeCom notification failed"
        write_error(message)
        print(message, file=sys.stderr)
        return notify_result.returncode

    ERROR_PATH.unlink(missing_ok=True)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

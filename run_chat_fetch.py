#!/usr/bin/env python3

from __future__ import annotations

import json
import sys

from run_pending_count import (
    ERROR_PATH,
    append_history,
    build_error_entry,
    build_success_entry,
    load_latest,
    run_scrape,
    write_error,
)


def main() -> int:
    result = run_scrape()

    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "LingXing scrape failed"
        write_error(message)
        entry = build_error_entry(result.stderr, result.stdout, result.returncode)
        entry["trigger"] = "manual-skill"
        entry["delivery"] = "current-chat"
        append_history(entry)
        print(message, file=sys.stderr)
        return result.returncode

    payload = load_latest()
    entry = build_success_entry(payload, result.stdout)
    entry["trigger"] = "manual-skill"
    entry["delivery"] = "current-chat"
    append_history(entry)
    ERROR_PATH.unlink(missing_ok=True)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

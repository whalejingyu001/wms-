#!/usr/bin/env python3
"""
Browser-based scraper for LingXing WMS pages that do not provide a public API.

Typical usage:
1. Save a login state once with `save-state`.
2. Put selectors and page actions into a JSON config.
3. Run `scrape` on a schedule to write JSON or CSV output.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EXAMPLE_CONFIG = {
    "page_url": "https://erp.lingxing.com/example/wms/inventory",
    "state_file": "tmp/lingxing-state.json",
    "ready_selector": "table tbody tr",
    "actions": [
        {"type": "click", "selector": "button:has-text('Search')"},
        {"type": "wait_for_selector", "selector": "table tbody tr"},
    ],
    "record_selector": "table tbody tr",
    "fields": [
        {"name": "captured_at", "type": "timestamp"},
        {"name": "sku", "selector": "td:nth-child(2)", "scope": "record"},
        {"name": "available_qty", "selector": "td:nth-child(5)", "scope": "record"},
        {
            "name": "warehouse_name",
            "selector": "div.current-warehouse",
            "scope": "page",
            "required": False,
        },
    ],
}

SUPPORTED_ACTIONS = {
    "click",
    "fill",
    "press",
    "select_option",
    "wait_for_selector",
    "wait_for_timeout",
}
SUPPORTED_FIELD_TYPES = {
    "text",
    "value",
    "attr",
    "count",
    "exists",
    "timestamp",
    "constant",
}


class ConfigError(ValueError):
    """Raised when the scraper config is invalid."""


def load_config(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text()
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc

    try:
        config = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"Config file is not valid JSON: {exc}") from exc

    validate_config(config)
    return config


def validate_config(config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise ConfigError("Config root must be a JSON object.")

    page_url = config.get("page_url")
    if not isinstance(page_url, str) or not page_url.strip():
        raise ConfigError("Config requires a non-empty 'page_url' string.")

    fields = config.get("fields")
    if not isinstance(fields, list) or not fields:
        raise ConfigError("Config requires a non-empty 'fields' array.")

    record_selector = config.get("record_selector")
    if record_selector is not None and not isinstance(record_selector, str):
        raise ConfigError("'record_selector' must be a string when provided.")

    ready_selector = config.get("ready_selector")
    if ready_selector is not None and not isinstance(ready_selector, str):
        raise ConfigError("'ready_selector' must be a string when provided.")

    actions = config.get("actions", [])
    if not isinstance(actions, list):
        raise ConfigError("'actions' must be an array when provided.")

    for index, action in enumerate(actions):
        validate_action(action, index)

    for index, field in enumerate(fields):
        validate_field(field, index, has_record_selector=bool(record_selector))


def validate_action(action: Any, index: int) -> None:
    if not isinstance(action, dict):
        raise ConfigError(f"Action #{index} must be an object.")

    action_type = action.get("type")
    if action_type not in SUPPORTED_ACTIONS:
        raise ConfigError(
            f"Action #{index} has unsupported type '{action_type}'. "
            f"Supported: {sorted(SUPPORTED_ACTIONS)}"
        )

    if action_type in {"click", "fill", "press", "select_option", "wait_for_selector"}:
        selector = action.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise ConfigError(f"Action #{index} requires a non-empty 'selector'.")

    if action_type == "fill" and "value" not in action:
        raise ConfigError(f"Action #{index} requires 'value' for fill.")

    if action_type == "press":
        key = action.get("key")
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(f"Action #{index} requires a non-empty 'key'.")

    if action_type == "select_option" and "value" not in action:
        raise ConfigError(f"Action #{index} requires 'value' for select_option.")

    if action_type == "wait_for_timeout":
        ms = action.get("ms")
        if not isinstance(ms, int) or ms < 0:
            raise ConfigError(f"Action #{index} requires a non-negative integer 'ms'.")


def validate_field(field: Any, index: int, has_record_selector: bool) -> None:
    if not isinstance(field, dict):
        raise ConfigError(f"Field #{index} must be an object.")

    name = field.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigError(f"Field #{index} requires a non-empty 'name'.")

    field_type = field.get("type", "text")
    if field_type not in SUPPORTED_FIELD_TYPES:
        raise ConfigError(
            f"Field '{name}' has unsupported type '{field_type}'. "
            f"Supported: {sorted(SUPPORTED_FIELD_TYPES)}"
        )

    scope = field.get("scope")
    if scope is not None and scope not in {"page", "record"}:
        raise ConfigError(f"Field '{name}' has invalid 'scope': {scope}")

    if field_type in {"text", "value", "attr", "count", "exists"}:
        selector = field.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise ConfigError(f"Field '{name}' requires a non-empty 'selector'.")

    if field_type == "attr":
        attr_name = field.get("attr")
        if not isinstance(attr_name, str) or not attr_name.strip():
            raise ConfigError(f"Field '{name}' requires a non-empty 'attr'.")

    if field_type == "constant" and "value" not in field:
        raise ConfigError(f"Field '{name}' requires 'value' for constant.")

    if scope == "record" and not has_record_selector:
        raise ConfigError(f"Field '{name}' uses scope=record but no 'record_selector' is set.")


def import_playwright():
    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run "
            "`python3 -m pip install playwright && python3 -m playwright install chromium`."
        ) from exc

    return sync_playwright, PlaywrightTimeoutError


def save_state(
    login_url: str,
    state_file: Path,
    headless: bool,
    timeout_ms: int,
    wait_for_selector: str | None,
    wait_for_url_contains: str | None,
) -> None:
    sync_playwright, PlaywrightTimeoutError = import_playwright()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(login_url, wait_until="domcontentloaded")

        if wait_for_selector:
            print(f"Waiting for selector after login: {wait_for_selector}")
            page.locator(wait_for_selector).first.wait_for(timeout=timeout_ms)
        elif wait_for_url_contains:
            print(f"Waiting for URL to contain: {wait_for_url_contains}")
            deadline = time.time() + (timeout_ms / 1000.0)
            while time.time() < deadline:
                if wait_for_url_contains in page.url:
                    break
                page.wait_for_timeout(500)
            else:
                raise PlaywrightTimeoutError(
                    f"Timed out waiting for URL containing: {wait_for_url_contains}"
                )
        else:
            print("Complete login in the opened browser window, then press Enter here.")
            input()

        state_file.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_file))
        browser.close()

    print(f"Saved browser state to: {state_file}")


def run_scrape(
    config: dict[str, Any],
    output_path: Path | None,
    output_format: str | None,
    state_override: Path | None,
    headless: bool,
    timeout_ms: int,
) -> None:
    sync_playwright, _ = import_playwright()

    resolved_state = state_override or read_optional_path(config.get("state_file"))
    page_url = config["page_url"]
    ready_selector = config.get("ready_selector")
    actions = config.get("actions", [])
    record_selector = config.get("record_selector")
    fields = config["fields"]

    extracted_at = datetime.now(timezone.utc).isoformat()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        context_kwargs: dict[str, Any] = {}
        if resolved_state:
            context_kwargs["storage_state"] = str(resolved_state)
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.set_default_timeout(timeout_ms)
        page.goto(page_url, wait_until="domcontentloaded")

        if ready_selector:
            page.locator(ready_selector).first.wait_for(timeout=timeout_ms)

        apply_actions(page, actions, timeout_ms)

        if record_selector:
            records = extract_record_list(page, record_selector, fields, extracted_at)
        else:
            records = [extract_single_record(page, fields, extracted_at)]

        browser.close()

    payload = {
        "page_url": page_url,
        "extracted_at": extracted_at,
        "record_count": len(records),
        "records": records,
    }
    write_output(payload, output_path, output_format)


def apply_actions(page, actions: list[dict[str, Any]], timeout_ms: int) -> None:
    for action in actions:
        action_type = action["type"]
        selector = action.get("selector")

        if action_type == "click":
            page.locator(selector).first.click(timeout=timeout_ms)
        elif action_type == "fill":
            page.locator(selector).first.fill(str(action["value"]), timeout=timeout_ms)
        elif action_type == "press":
            page.locator(selector).first.press(str(action["key"]), timeout=timeout_ms)
        elif action_type == "select_option":
            value = action["value"]
            values = value if isinstance(value, list) else [value]
            page.locator(selector).first.select_option(
                [str(item) for item in values],
                timeout=timeout_ms,
            )
        elif action_type == "wait_for_selector":
            page.locator(selector).first.wait_for(
                state=action.get("state", "visible"),
                timeout=timeout_ms,
            )
        elif action_type == "wait_for_timeout":
            page.wait_for_timeout(action["ms"])


def extract_record_list(
    page,
    record_selector: str,
    fields: list[dict[str, Any]],
    extracted_at: str,
) -> list[dict[str, Any]]:
    rows = page.locator(record_selector)
    row_count = rows.count()
    records: list[dict[str, Any]] = []

    for index in range(row_count):
        row = rows.nth(index)
        record = extract_record_from_roots(page, row, fields, extracted_at)
        records.append(record)

    return records


def extract_single_record(page, fields: list[dict[str, Any]], extracted_at: str) -> dict[str, Any]:
    return extract_record_from_roots(page, None, fields, extracted_at)


def extract_record_from_roots(page, row, fields: list[dict[str, Any]], extracted_at: str) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in fields:
        record[field["name"]] = extract_field_value(page, row, field, extracted_at)
    return record


def extract_field_value(page, row, field: dict[str, Any], extracted_at: str) -> Any:
    field_type = field.get("type", "text")

    if field_type == "timestamp":
        return extracted_at

    if field_type == "constant":
        return field["value"]

    scope = field.get("scope") or ("record" if row is not None else "page")
    root = row if scope == "record" else page
    selector = field["selector"]
    locator = root.locator(selector)

    if field_type == "count":
        return locator.count()
    if field_type == "exists":
        return locator.count() > 0

    count = locator.count()
    required = bool(field.get("required", True))
    if count == 0:
        if required:
            raise RuntimeError(f"Required selector not found for field '{field['name']}': {selector}")
        return None

    target = locator.first
    if field_type == "text":
        value = target.inner_text()
        value = value.strip() if field.get("strip", True) else value
        return apply_regex_if_needed(value, field)
    if field_type == "value":
        value = target.input_value()
        value = value.strip() if field.get("strip", True) else value
        return apply_regex_if_needed(value, field)
    if field_type == "attr":
        value = target.get_attribute(field["attr"])
        if value is None:
            return None
        return apply_regex_if_needed(value, field)

    raise RuntimeError(f"Unsupported field type reached unexpectedly: {field_type}")


def apply_regex_if_needed(value: str, field: dict[str, Any]) -> Any:
    pattern = field.get("regex")
    if not pattern:
        return value

    match = re.search(pattern, value)
    if not match:
        if field.get("required", True):
            raise RuntimeError(
                f"Regex '{pattern}' did not match value for field '{field['name']}': {value}"
            )
        return None

    group = field.get("group", 1)
    extracted = match.group(group)

    if field.get("as_int"):
        return int(extracted)

    return extracted


def write_output(payload: dict[str, Any], output_path: Path | None, output_format: str | None) -> None:
    resolved_format = resolve_output_format(output_path, output_format)
    records = payload["records"]

    if output_path is None:
        if resolved_format == "json":
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return
        raise RuntimeError("CSV output requires --output.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if resolved_format == "json":
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(f"Wrote JSON output to: {output_path}")
        return

    if resolved_format == "csv":
        write_csv(records, output_path)
        print(f"Wrote CSV output to: {output_path}")
        return

    raise RuntimeError(f"Unsupported output format: {resolved_format}")


def write_csv(records: list[dict[str, Any]], output_path: Path) -> None:
    keys: list[str] = []
    seen: set[str] = set()

    for record in records:
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                keys.append(key)

    with output_path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for record in records:
            writer.writerow(record)


def resolve_output_format(output_path: Path | None, output_format: str | None) -> str:
    if output_format:
        return output_format
    if output_path and output_path.suffix.lower() == ".csv":
        return "csv"
    return "json"


def read_optional_path(raw_value: Any) -> Path | None:
    if raw_value is None:
        return None
    if not isinstance(raw_value, str) or not raw_value.strip():
        raise ConfigError("Optional path value must be a non-empty string when provided.")
    return Path(raw_value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LingXing WMS browser scraper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    print_example = subparsers.add_parser(
        "print-example-config",
        help="Print a starter JSON config",
    )
    print_example.set_defaults(func=handle_print_example_config)

    save_state_parser = subparsers.add_parser(
        "save-state",
        help="Open a browser, complete login manually, and save session state",
    )
    save_state_parser.add_argument("--login-url", required=True, help="LingXing WMS login URL")
    save_state_parser.add_argument("--state-file", required=True, help="Path to storage state JSON")
    save_state_parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headlessly. Default is headed for manual login.",
    )
    save_state_parser.add_argument(
        "--timeout-ms",
        type=int,
        default=120000,
        help="Timeout for login completion checks",
    )
    save_state_parser.add_argument(
        "--wait-for-selector",
        help="Save state automatically after this selector appears",
    )
    save_state_parser.add_argument(
        "--wait-for-url-contains",
        help="Save state automatically after the page URL contains this text",
    )
    save_state_parser.set_defaults(func=handle_save_state)

    scrape_parser = subparsers.add_parser(
        "scrape",
        help="Run a configured scrape job and write JSON or CSV output",
    )
    scrape_parser.add_argument("--config", required=True, help="Path to scrape job config JSON")
    scrape_parser.add_argument("--output", help="Output file path. Defaults to stdout JSON.")
    scrape_parser.add_argument(
        "--output-format",
        choices=["json", "csv"],
        help="Override output format",
    )
    scrape_parser.add_argument(
        "--state-file",
        help="Override state file path from config",
    )
    scrape_parser.add_argument(
        "--headed",
        action="store_true",
        help="Run browser headed for debugging. Default is headless.",
    )
    scrape_parser.add_argument(
        "--timeout-ms",
        type=int,
        default=30000,
        help="Default action and selector timeout",
    )
    scrape_parser.set_defaults(func=handle_scrape)

    return parser


def handle_print_example_config(_args: argparse.Namespace) -> None:
    print(json.dumps(EXAMPLE_CONFIG, ensure_ascii=False, indent=2))


def handle_save_state(args: argparse.Namespace) -> None:
    save_state(
        login_url=args.login_url,
        state_file=Path(args.state_file),
        headless=args.headless,
        timeout_ms=args.timeout_ms,
        wait_for_selector=args.wait_for_selector,
        wait_for_url_contains=args.wait_for_url_contains,
    )


def handle_scrape(args: argparse.Namespace) -> None:
    config_path = Path(args.config)
    config = load_config(config_path)
    output_path = Path(args.output) if args.output else None
    state_override = Path(args.state_file) if args.state_file else None

    run_scrape(
        config=config,
        output_path=output_path,
        output_format=args.output_format,
        state_override=state_override,
        headless=not args.headed,
        timeout_ms=args.timeout_ms,
    )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    try:
        args.func(args)
    except (ConfigError, RuntimeError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

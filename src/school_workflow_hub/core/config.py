"""Safe JSON configuration loading and validation without third-party packages."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import ClassRecord
from .database import require_synthetic_runtime_path


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class HubConfig:
    school_year: str
    timezone: str
    classes: Tuple[ClassRecord, ...]
    approved_destinations: Dict[str, str]
    class_meeting_calendar: Dict[str, Any]
    schedules: Dict[str, Dict[str, Any]]
    feature_flags: Dict[str, bool]
    model_provider: Dict[str, Any]
    runtime: Dict[str, str]
    permission_policy: Dict[str, Any]
    operation_mode: str
    course_rules: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    workflow_policy: Dict[str, Any] = field(default_factory=dict)


def _required_mapping(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be an object")
    return value


def parse_config(payload: Dict[str, Any]) -> HubConfig:
    if not isinstance(payload.get("school_year"), str) or not payload["school_year"].strip():
        raise ConfigError("school_year is required")
    timezone_name = payload.get("timezone")
    if not isinstance(timezone_name, str):
        raise ConfigError("timezone is required")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as error:
        raise ConfigError(f"unknown timezone: {timezone_name}") from error

    raw_classes = payload.get("classes")
    if not isinstance(raw_classes, list) or not raw_classes:
        raise ConfigError("at least one class mapping is required")
    classes = []
    class_ids = set()
    for item in raw_classes:
        if not isinstance(item, dict):
            raise ConfigError("each class must be an object")
        stable_id = item.get("id")
        display_name = item.get("name")
        calendar_id = item.get("calendar_id")
        if not all(isinstance(value, str) and value.strip() for value in (stable_id, display_name, calendar_id)):
            raise ConfigError("every class requires id, name, and calendar_id")
        if stable_id in class_ids:
            raise ConfigError(f"duplicate class id: {stable_id}")
        class_ids.add(stable_id)
        classes.append(ClassRecord(stable_id, display_name, calendar_id))

    feature_flags = _required_mapping(payload, "feature_flags")
    for key in ("external_writes", "live_models"):
        if not isinstance(feature_flags.get(key), bool):
            raise ConfigError(f"feature_flags.{key} must be boolean")
    permission_policy = _required_mapping(payload, "permission_policy")
    if permission_policy.get("default") != "deny":
        raise ConfigError("permission_policy.default must be deny")
    allowed = permission_policy.get("allowed", [])
    if not isinstance(allowed, list) or not all(isinstance(value, str) for value in allowed):
        raise ConfigError("permission_policy.allowed must be a list of strings")

    operation_mode = payload.get("operation_mode", "dry_run")
    if operation_mode != "dry_run":
        raise ConfigError("operation_mode must be dry_run in the synthetic foundation")

    schedules = _required_mapping(payload, "schedules")
    for name, schedule in schedules.items():
        if not isinstance(schedule, dict) or schedule.get("enabled", False) is not False:
            raise ConfigError("schedules must remain disabled in the synthetic build")
    course_rules = payload.get("course_rules", {})
    if not isinstance(course_rules, dict) or any(not isinstance(v, dict) for v in course_rules.values()):
        raise ConfigError("course_rules must map course names to rule objects")
    workflow_policy = payload.get("workflow_policy", {})
    if not isinstance(workflow_policy, dict):
        raise ConfigError("workflow_policy must be an object")
    for key, minimum, maximum in (("max_attempts", 1, 100), ("retention_days", 14, 3650), ("research_assignment_threshold", 3, 1000)):
        if key in workflow_policy and (type(workflow_policy[key]) is not int or not minimum <= workflow_policy[key] <= maximum):
            raise ConfigError("workflow_policy contains an invalid bound")

    runtime = _required_mapping(payload, "runtime")
    for key in ("database_path", "reports_directory"):
        if not isinstance(runtime.get(key), str) or not runtime[key].strip():
            raise ConfigError(f"runtime.{key} is required")
        try:
            require_synthetic_runtime_path(runtime[key])
        except ValueError as error:
            raise ConfigError(f"runtime.{key} is not an approved local synthetic path") from error

    return HubConfig(
        school_year=payload["school_year"],
        timezone=timezone_name,
        classes=tuple(classes),
        approved_destinations=_required_mapping(payload, "approved_destinations"),
        class_meeting_calendar=_required_mapping(payload, "class_meeting_calendar"),
        schedules=schedules,
        feature_flags={key: bool(value) for key, value in feature_flags.items()},
        model_provider=_required_mapping(payload, "model_provider"),
        runtime={key: str(value) for key, value in runtime.items()},
        permission_policy={"default": "deny", "allowed": list(allowed)},
        operation_mode=operation_mode,
        course_rules=course_rules,
        workflow_policy=workflow_policy,
    )


def load_config(path: str | Path) -> HubConfig:
    config_path = Path(path)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ConfigError(f"cannot read config: {config_path}") from error
    except json.JSONDecodeError as error:
        raise ConfigError(f"invalid JSON config: {config_path}") from error
    if not isinstance(payload, dict):
        raise ConfigError("configuration root must be an object")
    return parse_config(payload)

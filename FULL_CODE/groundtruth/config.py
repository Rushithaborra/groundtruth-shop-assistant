"""Settings: one JSON file (FULL_CODE/config.json) plus a few environment-variable overrides.

Every value is checked for type and range when it is loaded, so a bad setting stops start-up with a clear message
instead of failing later. Paths in the file are relative to FULL_CODE/.

Environment overrides (see .env.example):
    GROUNDTRUTH_CONFIG       another config file
    GROUNDTRUTH_CATALOGUE    catalogue file           GROUNDTRUTH_POLICY      policy file
    GROUNDTRUTH_EMBED_MODEL  embedding model folder   GROUNDTRUTH_LLM_MODEL   instruct model folder
    GROUNDTRUTH_HOST, GROUNDTRUTH_PORT                GROUNDTRUTH_LOG_LEVEL
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from .paths import ROOT

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")


class ConfigError(ValueError):
    """A setting is missing, unknown, or outside its allowed range."""


@dataclass(frozen=True)
class PathSettings:
    data_dir: Path
    scale_dir: Path
    catalogue: Path
    policy: Path
    questions: Path
    demo_catalogue: Path
    demo_policy: Path
    bench_questions: Path
    pack_catalogue: Path
    pack_policy: Path
    dev_questions: Path
    dev_truth: Path
    dev_split: Path
    test_questions: Path
    official_scorer: Path
    submission: Path
    dev_predictions: Path
    reports_dir: Path
    cache_dir: Path
    semantic_config: Path
    vocabulary: Path


@dataclass(frozen=True)
class AssistantSettings:
    list_limit: int
    clarify_limit: int
    policy_max_sentences: int


@dataclass(frozen=True)
class SearchSettings:
    typo_cutoff: float
    typo_cache_size: int


@dataclass(frozen=True)
class ServerSettings:
    host: str
    port: int
    max_sessions: int
    max_message_chars: int
    max_body_bytes: int


@dataclass(frozen=True)
class SessionSettings:
    max_sessions: int
    ttl_seconds: float


@dataclass(frozen=True)
class ModelSettings:
    embed_model: str | None
    llm_model: str | None
    embed_max_length: int
    llm_max_new_tokens: int
    llm_max_values_per_attribute: int


@dataclass(frozen=True)
class CalibrationSettings:
    margin: float
    alt_gap: float


@dataclass(frozen=True)
class EvaluationSettings:
    weight_groundedness: float
    weight_helpfulness: float
    weight_refusal: float
    hallucinated_price_penalty: float
    scorer_timeout_s: float


@dataclass(frozen=True)
class LoggingSettings:
    level: str


@dataclass(frozen=True)
class Settings:
    paths: PathSettings
    assistant: AssistantSettings
    search: SearchSettings
    server: ServerSettings
    sessions: SessionSettings
    models: ModelSettings
    calibration: CalibrationSettings
    evaluation: EvaluationSettings
    logging: LoggingSettings


def _int(name: str, value: Any, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{name} must be a whole number from {low} to {high}, got {value!r}")
    return value


def _float(name: str, value: Any, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ConfigError(f"{name} must be a number from {low} to {high}, got {value!r}")
    return float(value)


def _text(name: str, value: Any, allowed: tuple[str, ...] = ()) -> str:
    if not isinstance(value, str) or not value.strip() or (allowed and value not in allowed):
        hint = f" (one of {', '.join(allowed)})" if allowed else ""
        raise ConfigError(f"{name} must be a non-empty string{hint}, got {value!r}")
    return value


def _optional_text(name: str, value: Any) -> str | None:
    return None if value is None else _text(name, value)


def _path(name: str, value: Any) -> Path:
    path = Path(_text(name, value))
    return path if path.is_absolute() else ROOT / path


def _section(raw: dict, name: str, fields: tuple[str, ...]) -> dict:
    section = raw.get(name)
    if not isinstance(section, dict):
        raise ConfigError(f"config section '{name}' is missing or not an object")
    missing, unknown = set(fields) - set(section), set(section) - set(fields)
    if missing or unknown:
        raise ConfigError(f"config section '{name}': missing {sorted(missing)}, unknown {sorted(unknown)}")
    return section


def _apply_env(raw: dict) -> None:
    """Environment variables override the file; values are validated like the file's own."""
    overrides = {
        "GROUNDTRUTH_CATALOGUE": ("paths", "catalogue", str),
        "GROUNDTRUTH_POLICY": ("paths", "policy", str),
        "GROUNDTRUTH_EMBED_MODEL": ("models", "embed_model", str),
        "GROUNDTRUTH_LLM_MODEL": ("models", "llm_model", str),
        "GROUNDTRUTH_HOST": ("server", "host", str),
        "GROUNDTRUTH_PORT": ("server", "port", int),
        "GROUNDTRUTH_LOG_LEVEL": ("logging", "level", str.upper),
    }
    for var, (section, key, convert) in overrides.items():
        value = os.environ.get(var)
        if value is None or section not in raw or not isinstance(raw[section], dict):
            continue
        try:
            raw[section][key] = convert(value)
        except ValueError as exc:
            raise ConfigError(f"{var} has an invalid value {value!r}") from exc


def parse_settings(raw: dict) -> Settings:
    """Validate a config dictionary (as read from config.json) into typed settings."""
    p = _section(raw, "paths", tuple(PathSettings.__dataclass_fields__))
    a = _section(raw, "assistant", tuple(AssistantSettings.__dataclass_fields__))
    s = _section(raw, "search", tuple(SearchSettings.__dataclass_fields__))
    w = _section(raw, "server", tuple(ServerSettings.__dataclass_fields__))
    ss = _section(raw, "sessions", tuple(SessionSettings.__dataclass_fields__))
    m = _section(raw, "models", tuple(ModelSettings.__dataclass_fields__))
    c = _section(raw, "calibration", tuple(CalibrationSettings.__dataclass_fields__))
    e = _section(raw, "evaluation", tuple(EvaluationSettings.__dataclass_fields__))
    lg = _section(raw, "logging", tuple(LoggingSettings.__dataclass_fields__))
    evaluation = EvaluationSettings(
        weight_groundedness=_float("evaluation.weight_groundedness", e["weight_groundedness"], 0, 1),
        weight_helpfulness=_float("evaluation.weight_helpfulness", e["weight_helpfulness"], 0, 1),
        weight_refusal=_float("evaluation.weight_refusal", e["weight_refusal"], 0, 1),
        hallucinated_price_penalty=_float(
            "evaluation.hallucinated_price_penalty", e["hallucinated_price_penalty"], 0, 1
        ),
        scorer_timeout_s=_float("evaluation.scorer_timeout_s", e["scorer_timeout_s"], 1, 3600),
    )
    total = evaluation.weight_groundedness + evaluation.weight_helpfulness + evaluation.weight_refusal
    if abs(total - 1.0) > 1e-9:
        raise ConfigError(f"evaluation weights must add up to 1, got {total}")
    return Settings(
        paths=PathSettings(**{key: _path(f"paths.{key}", value) for key, value in p.items()}),
        assistant=AssistantSettings(
            list_limit=_int("assistant.list_limit", a["list_limit"], 1, 20),
            clarify_limit=_int("assistant.clarify_limit", a["clarify_limit"], 1, 20),
            policy_max_sentences=_int("assistant.policy_max_sentences", a["policy_max_sentences"], 1, 10),
        ),
        search=SearchSettings(
            typo_cutoff=_float("search.typo_cutoff", s["typo_cutoff"], 0.5, 1.0),
            typo_cache_size=_int("search.typo_cache_size", s["typo_cache_size"], 0, 1_000_000),
        ),
        server=ServerSettings(
            host=_text("server.host", w["host"]),
            port=_int("server.port", w["port"], 1, 65535),
            max_sessions=_int("server.max_sessions", w["max_sessions"], 1, 1_000_000),
            max_message_chars=_int("server.max_message_chars", w["max_message_chars"], 1, 10_000),
            max_body_bytes=_int("server.max_body_bytes", w["max_body_bytes"], 100, 1_000_000),
        ),
        sessions=SessionSettings(
            max_sessions=_int("sessions.max_sessions", ss["max_sessions"], 1, 10_000_000),
            ttl_seconds=_float("sessions.ttl_seconds", ss["ttl_seconds"], 0, 7 * 24 * 3600),
        ),
        models=ModelSettings(
            embed_model=_optional_text("models.embed_model", m["embed_model"]),
            llm_model=_optional_text("models.llm_model", m["llm_model"]),
            embed_max_length=_int("models.embed_max_length", m["embed_max_length"], 8, 8192),
            llm_max_new_tokens=_int("models.llm_max_new_tokens", m["llm_max_new_tokens"], 1, 4096),
            llm_max_values_per_attribute=_int(
                "models.llm_max_values_per_attribute", m["llm_max_values_per_attribute"], 1, 1000
            ),
        ),
        calibration=CalibrationSettings(
            margin=_float("calibration.margin", c["margin"], 0, 1),
            alt_gap=_float("calibration.alt_gap", c["alt_gap"], 0, 1),
        ),
        evaluation=evaluation,
        logging=LoggingSettings(level=_text("logging.level", lg["level"], LOG_LEVELS)),
    )


def load_settings(path: str | Path | None = None) -> Settings:
    """Read and validate a config file (default: GROUNDTRUTH_CONFIG, else FULL_CODE/config.json)."""
    path = Path(path or os.environ.get("GROUNDTRUTH_CONFIG") or ROOT / "config.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid JSON: {exc.msg} (line {exc.lineno})") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config file {path} must hold a JSON object")
    _apply_env(raw)
    return parse_settings(raw)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """The process-wide settings, loaded once."""
    return load_settings()

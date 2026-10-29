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
import re
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
class SubmissionSettings:
    team_id: str  # the zip is named f"{team_id}_{problem_id}.zip"
    problem_id: str


@dataclass(frozen=True)
class CatalogueSettings:
    pack_strict: bool  # the organisers' catalogue: a bad row or duplicate id stops the run instead of being skipped
    pack_expected_products: int | None  # how many products it must hold (None: don't check the count)


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
    rate_limit_per_minute: int
    rate_limit_per_address_per_minute: int


@dataclass(frozen=True)
class SessionSettings:
    max_sessions: int
    ttl_seconds: float


@dataclass(frozen=True)
class ModelSettings:
    embed_model: str | None
    llm_model: str | None
    embed_max_length: int
    embed_timeout_s: float
    llm_context_tokens: int
    llm_temperature: float
    llm_max_new_tokens: int
    llm_max_values_per_attribute: int
    llm_timeout_s: float
    model_retries: int
    model_backoff_s: float


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
class CostReportSettings:
    """One measured call of the provided instruct model, used to estimate "an LLM writes every reply"."""

    llm_prompt_tokens: int
    llm_output_tokens: int
    llm_seconds_per_call: float


@dataclass(frozen=True)
class LoggingSettings:
    level: str


@dataclass(frozen=True)
class Settings:
    paths: PathSettings
    submission: SubmissionSettings
    catalogue: CatalogueSettings
    assistant: AssistantSettings
    search: SearchSettings
    server: ServerSettings
    sessions: SessionSettings
    models: ModelSettings
    calibration: CalibrationSettings
    evaluation: EvaluationSettings
    cost_report: CostReportSettings
    logging: LoggingSettings


def _int(name: str, value: Any, low: int, high: int) -> int:
    """A whole number within a range, or a ConfigError naming the setting."""
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ConfigError(f"{name} must be a whole number from {low} to {high}, got {value!r}")
    return value


def _float(name: str, value: Any, low: float, high: float) -> float:
    """A number within a range, or a ConfigError naming the setting."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
        raise ConfigError(f"{name} must be a number from {low} to {high}, got {value!r}")
    return float(value)


def _text(name: str, value: Any, allowed: tuple[str, ...] = ()) -> str:
    """A non-empty string (optionally one of a fixed set), or a ConfigError naming the setting."""
    if not isinstance(value, str) or not value.strip() or (allowed and value not in allowed):
        hint = f" (one of {', '.join(allowed)})" if allowed else ""
        raise ConfigError(f"{name} must be a non-empty string{hint}, got {value!r}")
    return value


def _flag(name: str, value: Any) -> bool:
    """A true/false setting, or a ConfigError naming the setting."""
    if not isinstance(value, bool):
        raise ConfigError(f"{name} must be true or false, got {value!r}")
    return value


def _identifier(name: str, value: Any) -> str:
    """A team or problem ID used in a file name: letters, digits and hyphens only."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,40}", value):
        raise ConfigError(f"{name} must be letters, digits and hyphens (it names the zip), got {value!r}")
    return value


def _optional_text(name: str, value: Any) -> str | None:
    """A string setting that may be null."""
    return None if value is None else _text(name, value)


def _path(name: str, value: Any) -> Path:
    """A path setting, resolved against FULL_CODE/ when relative."""
    path = Path(_text(name, value))
    return path if path.is_absolute() else ROOT / path


def _section(raw: dict, name: str, fields: tuple[str, ...]) -> dict:
    """One config section, which must have exactly the expected fields."""
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


def _paths(p: dict) -> PathSettings:
    """Every data and output path, resolved."""
    return PathSettings(**{key: _path(f"paths.{key}", value) for key, value in p.items()})


def _submission(sub: dict) -> SubmissionSettings:
    """The team and problem ids used in the zip's name."""
    return SubmissionSettings(
        team_id=_identifier("submission.team_id", sub["team_id"]),
        problem_id=_identifier("submission.problem_id", sub["problem_id"]),
    )


def _catalogue(c: dict) -> CatalogueSettings:
    """Strict loading of the pack's catalogue and its expected product count."""
    expected = c["pack_expected_products"]
    return CatalogueSettings(
        pack_strict=_flag("catalogue.pack_strict", c["pack_strict"]),
        pack_expected_products=None
        if expected is None
        else _int("catalogue.pack_expected_products", expected, 1, 10_000_000),
    )


def _assistant(a: dict) -> AssistantSettings:
    """How many products a listing or clarification shows, and policy sentences per answer."""
    return AssistantSettings(
        list_limit=_int("assistant.list_limit", a["list_limit"], 1, 20),
        clarify_limit=_int("assistant.clarify_limit", a["clarify_limit"], 1, 20),
        policy_max_sentences=_int("assistant.policy_max_sentences", a["policy_max_sentences"], 1, 10),
    )


def _search(s: dict) -> SearchSettings:
    """The typo matcher's similarity cutoff and cache size."""
    return SearchSettings(
        typo_cutoff=_float("search.typo_cutoff", s["typo_cutoff"], 0.5, 1.0),
        typo_cache_size=_int("search.typo_cache_size", s["typo_cache_size"], 0, 1_000_000),
    )


def _server(w: dict) -> ServerSettings:
    """Web server address and its limits: sessions, message and body size, rate limits."""
    return ServerSettings(
        host=_text("server.host", w["host"]),
        port=_int("server.port", w["port"], 1, 65535),
        max_sessions=_int("server.max_sessions", w["max_sessions"], 1, 1_000_000),
        max_message_chars=_int("server.max_message_chars", w["max_message_chars"], 1, 10_000),
        max_body_bytes=_int("server.max_body_bytes", w["max_body_bytes"], 100, 1_000_000),
        rate_limit_per_minute=_int("server.rate_limit_per_minute", w["rate_limit_per_minute"], 1, 10_000),
        rate_limit_per_address_per_minute=_int(
            "server.rate_limit_per_address_per_minute", w["rate_limit_per_address_per_minute"], 1, 100_000
        ),
    )


def _sessions(ss: dict) -> SessionSettings:
    """How many chat sessions are kept, and for how long."""
    return SessionSettings(
        max_sessions=_int("sessions.max_sessions", ss["max_sessions"], 1, 10_000_000),
        ttl_seconds=_float("sessions.ttl_seconds", ss["ttl_seconds"], 0, 7 * 24 * 3600),
    )


def _models(m: dict) -> ModelSettings:
    """Optional model paths, timeouts, retries and generation limits."""
    return ModelSettings(
        embed_model=_optional_text("models.embed_model", m["embed_model"]),
        llm_model=_optional_text("models.llm_model", m["llm_model"]),
        embed_max_length=_int("models.embed_max_length", m["embed_max_length"], 8, 8192),
        embed_timeout_s=_float("models.embed_timeout_s", m["embed_timeout_s"], 0.1, 600),
        llm_context_tokens=_int("models.llm_context_tokens", m["llm_context_tokens"], 256, 32768),
        llm_temperature=_float("models.llm_temperature", m["llm_temperature"], 0, 2),
        llm_max_new_tokens=_int("models.llm_max_new_tokens", m["llm_max_new_tokens"], 1, 4096),
        llm_max_values_per_attribute=_int(
            "models.llm_max_values_per_attribute", m["llm_max_values_per_attribute"], 1, 1000
        ),
        llm_timeout_s=_float("models.llm_timeout_s", m["llm_timeout_s"], 0.1, 600),
        model_retries=_int("models.model_retries", m["model_retries"], 0, 5),
        model_backoff_s=_float("models.model_backoff_s", m["model_backoff_s"], 0, 60),
    )


def _calibration(c: dict) -> CalibrationSettings:
    """Thresholds for calibrating the embedding fallback."""
    return CalibrationSettings(
        margin=_float("calibration.margin", c["margin"], 0, 1),
        alt_gap=_float("calibration.alt_gap", c["alt_gap"], 0, 1),
    )


def _evaluation(e: dict) -> EvaluationSettings:
    """The local evaluator's weights (which must add up to 1), price penalty and scorer timeout."""
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
    return evaluation


def _cost_report(cr: dict) -> CostReportSettings:
    """The measured LLM call used for the every-query cost estimate."""
    return CostReportSettings(
        llm_prompt_tokens=_int("cost_report.llm_prompt_tokens", cr["llm_prompt_tokens"], 0, 1_000_000),
        llm_output_tokens=_int("cost_report.llm_output_tokens", cr["llm_output_tokens"], 0, 100_000),
        llm_seconds_per_call=_float("cost_report.llm_seconds_per_call", cr["llm_seconds_per_call"], 0, 3600),
    )


def _logging(lg: dict) -> LoggingSettings:
    """The log level."""
    return LoggingSettings(level=_text("logging.level", lg["level"], LOG_LEVELS))


def parse_settings(raw: dict) -> Settings:
    """Validate a config dictionary (as read from config.json) into typed settings."""
    sections = {
        name: _section(raw, name, tuple(kind.__dataclass_fields__))
        for name, kind in (
            ("paths", PathSettings),
            ("submission", SubmissionSettings),
            ("catalogue", CatalogueSettings),
            ("assistant", AssistantSettings),
            ("search", SearchSettings),
            ("server", ServerSettings),
            ("sessions", SessionSettings),
            ("models", ModelSettings),
            ("calibration", CalibrationSettings),
            ("evaluation", EvaluationSettings),
            ("cost_report", CostReportSettings),
            ("logging", LoggingSettings),
        )
    }
    evaluation = _evaluation(sections["evaluation"])  # checked first, as before: its weights must add up to 1
    return Settings(
        paths=_paths(sections["paths"]),
        submission=_submission(sections["submission"]),
        catalogue=_catalogue(sections["catalogue"]),
        assistant=_assistant(sections["assistant"]),
        search=_search(sections["search"]),
        server=_server(sections["server"]),
        sessions=_sessions(sections["sessions"]),
        models=_models(sections["models"]),
        calibration=_calibration(sections["calibration"]),
        evaluation=evaluation,
        cost_report=_cost_report(sections["cost_report"]),
        logging=_logging(sections["logging"]),
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

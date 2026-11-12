from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / 'config.toml'

_ENV_PATTERN = re.compile(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}')

load_dotenv(PROJECT_ROOT / '.env')


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LLMSettings:
    model: str
    api_key: str
    base_url: str
    temperature: float = 0.0
    timeout: float = 30.0
    max_retries: int = 2
    trust_env: bool = False


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on'}:
            return True
        if normalized in {'0', 'false', 'no', 'off'}:
            return False
    return default


def _expand_env(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return _ENV_PATTERN.sub(lambda match: os.getenv(match.group(1), ''), value)


def load_toml_config(config_path: str | Path | None = None) -> dict[str, Any]:
    path = Path(
        os.getenv('APP_CONFIG_FILE')
        or config_path
        or DEFAULT_CONFIG_PATH
    )
    if not path.is_file():
        return {}
    with path.open('rb') as config_file:
        return tomllib.load(config_file)


def _walk_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)


def _normalized_section(section: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key).lower().replace('-', '_'): value
        for key, value in section.items()
    }


def _find_llm_section(config: dict[str, Any]) -> dict[str, Any]:
    best_section: dict[str, Any] = {}
    best_score = 0

    for section in _walk_mappings(config):
        normalized = _normalized_section(section)
        keys = set(normalized)
        score = 0
        if 'model' in keys or 'model_name' in keys:
            score += 2
        if 'api_key' in keys or 'api_key_env' in keys:
            score += 3
        if 'base_url' in keys or 'api_base' in keys:
            score += 1
        if score > best_score:
            best_section = normalized
            best_score = score

    return best_section


def _first_value(
    section: Mapping[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    for key in keys:
        value = section.get(key)
        if value not in (None, ''):
            return _expand_env(value)
    return default


def load_llm_settings(config_path: str | Path | None = None) -> LLMSettings:
    config = load_toml_config(config_path)
    section = _find_llm_section(config)

    api_key = os.getenv('DEEPSEEK_API_KEY') or _first_value(
        section, 'api_key', 'deepseek_api_key'
    )
    if not api_key:
        api_key_env = _first_value(section, 'api_key_env')
        if isinstance(api_key_env, str) and api_key_env:
            api_key = os.getenv(api_key_env)
    if not api_key:
        api_key = os.getenv('OPENAI_API_KEY', '')

    model = (
        os.getenv('DEEPSEEK_MODEL')
        or _first_value(section, 'model', 'model_name')
        or os.getenv('OPENAI_MODEL')
        or 'deepseek-chat'
    )
    base_url = (
        os.getenv('DEEPSEEK_BASE_URL')
        or _first_value(section, 'base_url', 'api_base')
        or os.getenv('OPENAI_BASE_URL')
        or os.getenv('OPENAI_API_BASE')
        or 'https://api.deepseek.com'
    )
    temperature = float(
        os.getenv('DEEPSEEK_TEMPERATURE')
        or _first_value(section, 'temperature', default=0.0)
    )
    timeout = float(
        os.getenv('DEEPSEEK_TIMEOUT')
        or _first_value(section, 'timeout', default=30.0)
    )
    max_retries = int(
        os.getenv('DEEPSEEK_MAX_RETRIES')
        or _first_value(section, 'max_retries', default=2)
    )
    trust_env = _as_bool(
        os.getenv('DEEPSEEK_TRUST_ENV')
        if os.getenv('DEEPSEEK_TRUST_ENV') not in (None, '')
        else _first_value(section, 'trust_env', default=False)
    )

    if not api_key:
        raise ConfigError(
            '未找到 DeepSeek API Key，请在 config.toml 中配置 '
            '[deepseek].api_key，或设置 DEEPSEEK_API_KEY 环境变量。'
        )

    return LLMSettings(
        model=str(model),
        api_key=str(api_key),
        base_url=str(base_url),
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        trust_env=trust_env,
    )

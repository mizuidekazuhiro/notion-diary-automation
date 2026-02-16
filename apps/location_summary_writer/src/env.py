import os


class ConfigError(ValueError):
    pass


def _get_raw_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None

    stripped = value.strip()
    if stripped == "":
        return None
    return stripped


def get_env_str(name: str, default: str | None = None, required: bool = False) -> str:
    value = _get_raw_env(name)
    if value is None:
        if required:
            raise ConfigError(f"{name} is required")
        return default
    return value


def get_env_int(
    name: str,
    default: int | None = None,
    required: bool = False,
    min: int | None = None,
    max: int | None = None,
) -> int:
    raw = _get_raw_env(name)
    if raw is None:
        if required:
            raise ConfigError(f"{name} is required")
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be int: got {raw!r}") from exc

    if min is not None and value < min:
        raise ConfigError(f"{name} must be >= {min}: got {value}")
    if max is not None and value > max:
        raise ConfigError(f"{name} must be <= {max}: got {value}")

    return value


def get_env_float(
    name: str,
    default: float | None = None,
    required: bool = False,
    min: float | None = None,
    max: float | None = None,
) -> float:
    raw = _get_raw_env(name)
    if raw is None:
        if required:
            raise ConfigError(f"{name} is required")
        return default

    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be float: got {raw!r}") from exc

    if min is not None and value < min:
        raise ConfigError(f"{name} must be >= {min}: got {value}")
    if max is not None and value > max:
        raise ConfigError(f"{name} must be <= {max}: got {value}")

    return value


def get_env_bool(name: str, default: bool = False) -> bool:
    raw = _get_raw_env(name)
    if raw is None:
        return default

    lowered = raw.lower()
    truthy = {"true", "1", "yes", "y", "on"}
    falsy = {"false", "0", "no", "n", "off"}

    if lowered in truthy:
        return True
    if lowered in falsy:
        return False

    raise ConfigError(f"{name} must be boolean: got {raw!r}")

from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent
from typing import Literal, Optional, Tuple, Union

import yaml
from pydantic import BaseModel, Field, ValidationError, model_validator


CONFIG_ENV_VAR = "EASYEDA2KICAD_CONFIG"
DEFAULT_CONFIG_BASENAMES = ("easyeda2kicad.yaml", "easyeda2kicad.yml")


class ConfigError(ValueError):
    pass


class ActionsConfig(BaseModel):
    symbol: bool = True
    footprint: bool = True
    model_3d: bool = Field(default=True, alias="model_3d")

    @model_validator(mode="after")
    def validate_one_action_enabled(self) -> "ActionsConfig":
        if not any([self.symbol, self.footprint, self.model_3d]):
            raise ValueError("at least one default action must be enabled")
        return self


class InteractiveConfig(BaseModel):
    enabled: Union[Literal["auto"], bool] = "auto"
    confirm_before_apply: bool = True

    def enabled_mode(self) -> Literal["auto", "true", "false"]:
        if self.enabled == "auto":
            return "auto"
        return "true" if self.enabled else "false"


class OutputConfig(BaseModel):
    base_path: Optional[str] = None
    overwrite: bool = False
    project_relative: bool = False


class FootprintLinkConfig(BaseModel):
    mode: Literal["generated", "explicit", "none"] = "generated"
    value: Optional[str] = None

    @model_validator(mode="after")
    def validate_mode_and_value(self) -> "FootprintLinkConfig":
        if self.mode == "explicit":
            if not self.value:
                raise ValueError(
                    "defaults.symbol.footprint_link.value is required when mode is explicit"
                )
            if ":" not in self.value or self.value.startswith(":") or self.value.endswith(":"):
                raise ValueError(
                    "defaults.symbol.footprint_link.value must match Library:Footprint"
                )
        elif self.value is not None:
            raise ValueError(
                "defaults.symbol.footprint_link.value may only be set when mode is explicit"
            )
        return self


class SymbolConfig(BaseModel):
    custom_fields: dict[str, str] = Field(default_factory=dict)
    footprint_link: FootprintLinkConfig = Field(default_factory=FootprintLinkConfig)

    @model_validator(mode="after")
    def validate_custom_fields(self) -> "SymbolConfig":
        invalid_keys = [key for key in self.custom_fields if not str(key).strip()]
        if invalid_keys:
            raise ValueError("custom field names must not be empty")
        return self


class DefaultsConfig(BaseModel):
    actions: ActionsConfig = Field(default_factory=ActionsConfig)
    interactive: InteractiveConfig = Field(default_factory=InteractiveConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    symbol: SymbolConfig = Field(default_factory=SymbolConfig)


class RootConfig(BaseModel):
    version: Literal[1]
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)


def resolve_config_path(cli_path: Optional[str]) -> Optional[Path]:
    if cli_path:
        return Path(cli_path).expanduser().resolve()

    env_path = os.environ.get(CONFIG_ENV_VAR)
    if env_path:
        return Path(env_path).expanduser().resolve()

    cwd = Path.cwd()
    for basename in DEFAULT_CONFIG_BASENAMES:
        candidate = cwd / basename
        if candidate.is_file():
            return candidate.resolve()

    return None


def load_config(cli_path: Optional[str]) -> Tuple[Optional[RootConfig], Optional[Path]]:
    path = resolve_config_path(cli_path)
    if path is None:
        return None, None
    if not path.is_file():
        raise ConfigError(f"Config file does not exist: {path}")

    try:
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise ConfigError(f"Failed to parse config file {path}: {err}") from err

    if raw_data is None:
        raw_data = {"version": 1}

    try:
        return RootConfig.model_validate(raw_data), path
    except ValidationError as err:
        raise ConfigError(f"Invalid config file {path}:\n{err}") from err


def write_default_config(path: Path) -> None:
    path = path.expanduser().resolve()
    if path.exists():
        raise ConfigError(f"Refusing to overwrite existing config file: {path}")
    if not path.parent.exists():
        raise ConfigError(f"Parent directory does not exist: {path.parent}")
    try:
        path.write_text(default_config_yaml(), encoding="utf-8")
    except OSError as err:
        raise ConfigError(f"Failed to write config file {path}: {err}") from err


def default_config_yaml() -> str:
    return dedent(
        """\
        version: 1

        defaults:
          actions:
            symbol: true
            footprint: true
            model_3d: true

          interactive:
            enabled: auto
            confirm_before_apply: true

          output:
            base_path:
            overwrite: false
            project_relative: false

          symbol:
            custom_fields: {}
            footprint_link:
              mode: generated
        """
    )


def apply_config_defaults(arguments: dict, config: Optional[RootConfig]) -> None:
    if arguments.get("custom_field") is None:
        arguments["custom_field"] = []

    if config is None:
        arguments["interactive_default_mode"] = "auto"
        arguments["confirm_before_apply"] = True
        arguments["config_path"] = None
        return

    defaults = config.defaults
    arguments["interactive_default_mode"] = defaults.interactive.enabled_mode()
    arguments["confirm_before_apply"] = defaults.interactive.confirm_before_apply

    if not any(
        [
            arguments.get("full"),
            arguments.get("symbol"),
            arguments.get("footprint"),
            arguments.get("3d"),
        ]
    ):
        arguments["symbol"] = defaults.actions.symbol
        arguments["footprint"] = defaults.actions.footprint
        arguments["3d"] = defaults.actions.model_3d

    if not arguments.get("output") and defaults.output.base_path:
        arguments["output"] = defaults.output.base_path

    if arguments.get("overwrite") is None:
        arguments["overwrite"] = defaults.output.overwrite

    if arguments.get("project_relative") is None:
        arguments["project_relative"] = defaults.output.project_relative

    if not arguments.get("clear_custom_fields") and not arguments.get("custom_field"):
        arguments["custom_field"] = [
            f"{key}:{value}" for key, value in defaults.symbol.custom_fields.items()
        ]

    if not arguments.get("footprint_link_mode"):
        arguments["footprint_link_mode"] = defaults.symbol.footprint_link.mode
    if (
        not arguments.get("footprint_link")
        and defaults.symbol.footprint_link.mode == "explicit"
    ):
        arguments["footprint_link"] = defaults.symbol.footprint_link.value

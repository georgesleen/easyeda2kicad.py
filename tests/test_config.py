from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from easyeda2kicad.__main__ import valid_arguments
from easyeda2kicad.config import (
    ConfigError,
    RootConfig,
    apply_config_defaults,
    load_config,
    write_default_config,
)


class ConfigTests(unittest.TestCase):
    def test_load_minimal_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "easyeda2kicad.yaml"
            config_path.write_text("version: 1\n", encoding="utf-8")

            config, resolved = load_config(str(config_path))

            self.assertIsNotNone(config)
            self.assertEqual(resolved, config_path.resolve())
            self.assertIsInstance(config, RootConfig)
            self.assertTrue(config.defaults.actions.symbol)

    def test_rejects_all_actions_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "easyeda2kicad.yaml"
            config_path.write_text(
                "version: 1\n"
                "defaults:\n"
                "  actions:\n"
                "    symbol: false\n"
                "    footprint: false\n"
                "    model_3d: false\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(str(config_path))

    def test_requires_explicit_footprint_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "easyeda2kicad.yaml"
            config_path.write_text(
                "version: 1\n"
                "defaults:\n"
                "  symbol:\n"
                "    footprint_link:\n"
                "      mode: explicit\n",
                encoding="utf-8",
            )

            with self.assertRaises(ConfigError):
                load_config(str(config_path))

    def test_cli_fields_override_config_custom_fields(self) -> None:
        config = RootConfig.model_validate(
            {
                "version": 1,
                "defaults": {
                    "symbol": {
                        "custom_fields": {
                            "Manufacturer": "Texas Instruments",
                        }
                    }
                },
            }
        )
        arguments = {
            "full": False,
            "symbol": False,
            "footprint": False,
            "3d": False,
            "output": None,
            "overwrite": False,
            "project_relative": False,
            "custom_field": ["Manufacturer:CLI Value"],
            "footprint_link_mode": None,
            "footprint_link": None,
        }

        apply_config_defaults(arguments, config)

        self.assertEqual(arguments["custom_field"], ["Manufacturer:CLI Value"])

    def test_config_applies_defaults_when_cli_omits_them(self) -> None:
        config = RootConfig.model_validate(
            {
                "version": 1,
                "defaults": {
                    "actions": {"symbol": True, "footprint": False, "model_3d": False},
                    "output": {"overwrite": True},
                    "symbol": {
                        "footprint_link": {
                            "mode": "explicit",
                            "value": "CorpLib:Part",
                        }
                    },
                },
            }
        )
        arguments = {
            "full": False,
            "symbol": False,
            "footprint": False,
            "3d": False,
            "output": None,
            "overwrite": None,
            "project_relative": None,
            "custom_field": None,
            "clear_custom_fields": False,
            "footprint_link_mode": None,
            "footprint_link": None,
        }

        apply_config_defaults(arguments, config)

        self.assertTrue(arguments["symbol"])
        self.assertFalse(arguments["footprint"])
        self.assertFalse(arguments["3d"])
        self.assertTrue(arguments["overwrite"])
        self.assertEqual(arguments["footprint_link_mode"], "explicit")
        self.assertEqual(arguments["footprint_link"], "CorpLib:Part")

    def test_cli_false_values_override_config_booleans(self) -> None:
        config = RootConfig.model_validate(
            {
                "version": 1,
                "defaults": {
                    "output": {"overwrite": True, "project_relative": True},
                },
            }
        )
        arguments = {
            "full": False,
            "symbol": True,
            "footprint": False,
            "3d": False,
            "output": "/tmp/lib",
            "overwrite": False,
            "project_relative": False,
            "custom_field": [],
            "clear_custom_fields": False,
            "footprint_link_mode": None,
            "footprint_link": None,
        }

        apply_config_defaults(arguments, config)

        self.assertFalse(arguments["overwrite"])
        self.assertFalse(arguments["project_relative"])

    def test_no_custom_fields_blocks_config_defaults(self) -> None:
        config = RootConfig.model_validate(
            {
                "version": 1,
                "defaults": {
                    "symbol": {"custom_fields": {"Manufacturer": "Texas Instruments"}}
                },
            }
        )
        arguments = {
            "full": False,
            "symbol": True,
            "footprint": False,
            "3d": False,
            "output": None,
            "overwrite": None,
            "project_relative": None,
            "custom_field": None,
            "clear_custom_fields": True,
            "footprint_link_mode": None,
            "footprint_link": None,
        }

        apply_config_defaults(arguments, config)

        self.assertEqual(arguments["custom_field"], [])

    def test_write_default_config_requires_existing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_parent_path = Path(tmp_dir) / "missing" / "easyeda2kicad.yaml"

            with self.assertRaises(ConfigError):
                write_default_config(missing_parent_path)

    def test_valid_arguments_does_not_create_symbol_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_path = Path(tmp_dir) / "my_lib"
            arguments = {
                "lcsc_id": "C2040",
                "symbol": True,
                "footprint": False,
                "3d": False,
                "full": False,
                "output": str(base_path),
                "overwrite": False,
                "custom_field": [],
                "clear_custom_fields": False,
                "footprint_link_mode": None,
                "footprint_link": None,
                "config": None,
                "write_default_config": False,
                "interactive": False,
                "v5": False,
                "project_relative": False,
                "debug": False,
            }

            self.assertTrue(valid_arguments(arguments))
            self.assertFalse((Path(f"{base_path}.kicad_sym")).exists())


if __name__ == "__main__":
    unittest.main()

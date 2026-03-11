from __future__ import annotations

import unittest
from types import SimpleNamespace

from easyeda2kicad.kicad.export_kicad_symbol import tune_footprint_ref_path


class SymbolExportTests(unittest.TestCase):
    def test_generated_footprint_link_prefixes_library(self) -> None:
        symbol = SimpleNamespace(info=SimpleNamespace(package="SOIC-8"))

        tune_footprint_ref_path(symbol, footprint_lib_name="MyLib")

        self.assertEqual(symbol.info.package, "MyLib:SOIC-8")

    def test_explicit_footprint_link_replaces_package(self) -> None:
        symbol = SimpleNamespace(info=SimpleNamespace(package="SOIC-8"))

        tune_footprint_ref_path(
            symbol,
            footprint_lib_name="Ignored",
            footprint_link_mode="explicit",
            footprint_link_value="CorpLib:CustomPart",
        )

        self.assertEqual(symbol.info.package, "CorpLib:CustomPart")

    def test_none_footprint_link_clears_package(self) -> None:
        symbol = SimpleNamespace(info=SimpleNamespace(package="SOIC-8"))

        tune_footprint_ref_path(symbol, footprint_link_mode="none")

        self.assertEqual(symbol.info.package, "")


if __name__ == "__main__":
    unittest.main()

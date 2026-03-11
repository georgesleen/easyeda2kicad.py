# Global imports
import argparse
import logging
import os
import re
import sys
from textwrap import dedent
from typing import Dict, List, Optional, Tuple

from easyeda2kicad import __version__
from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
from easyeda2kicad.easyeda.easyeda_importer import (
    Easyeda3dModelImporter,
    EasyedaFootprintImporter,
    EasyedaSymbolImporter,
)
from easyeda2kicad.easyeda.parameters_easyeda import EeSymbol
from easyeda2kicad.helpers import (
    add_component_in_symbol_lib_file,
    get_local_config,
    id_already_in_symbol_lib,
    set_logger,
    update_component_in_symbol_lib_file,
    windows_to_unix_in_place
)
from easyeda2kicad.kicad.export_kicad_3d_model import Exporter3dModelKicad
from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad
from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad
from easyeda2kicad.kicad.parameters_kicad_symbol import KicadVersion


class InteractiveAbortError(Exception):
    pass


def parse_custom_fields(custom_field_args: List[str]) -> Dict[str, str]:
    custom_fields: Dict[str, str] = {}
    for custom_field in custom_field_args:
        key, separator, value = custom_field.partition(":")
        key = key.strip()
        value = value.strip()

        if not separator:
            raise ValueError(
                f'Invalid custom field "{custom_field}". Expected KEY:VALUE.'
            )
        if not key:
            raise ValueError(
                f'Invalid custom field "{custom_field}". Key must not be empty.'
            )

        custom_fields[key] = value

    return custom_fields


def get_default_output_base_path() -> str:
    return os.path.join(
        os.path.expanduser("~"),
        "Documents",
        "Kicad",
        "easyeda2kicad",
        "easyeda2kicad",
    )


def parse_output_base_path(output: str) -> Tuple[str, str]:
    normalized_output = output.replace("\\", "/")
    return (
        "/".join(normalized_output.split("/")[:-1]),
        normalized_output.split("/")[-1].split(".lib")[0].split(".kicad_sym")[0],
    )


def prompt_text(prompt: str) -> str:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt) as err:
        raise InteractiveAbortError from err


def is_interactive_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def prompt_yes_no(prompt: str, default: bool = False) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = prompt_text(f"{prompt} {suffix}: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def prompt_for_lcsc_id() -> str:
    while True:
        lcsc_id = prompt_text("LCSC ID: ").strip().upper()
        if lcsc_id.startswith("C"):
            return lcsc_id
        print("LCSC ID must start with C.")


def prompt_for_actions(arguments: dict) -> None:
    while True:
        raw_selection = prompt_text(
            "Select outputs [symbol/footprint/3d/full]: "
        ).strip().lower()

        if raw_selection == "full":
            arguments["full"] = True
            arguments["symbol"] = True
            arguments["footprint"] = True
            arguments["3d"] = True
            return

        selected_actions = {
            token.strip()
            for token in raw_selection.split(",")
            if token.strip()
        }
        if selected_actions and selected_actions.issubset({"symbol", "footprint", "3d"}):
            arguments["full"] = False
            arguments["symbol"] = "symbol" in selected_actions
            arguments["footprint"] = "footprint" in selected_actions
            arguments["3d"] = "3d" in selected_actions
            return

        print("Enter 'full' or a comma-separated list of symbol, footprint, and 3d.")


def prompt_for_output_path(
    prompt: str, allow_blank: bool = True
) -> Optional[str]:
    while True:
        output_path = prompt_text(prompt).strip()
        if not output_path and allow_blank:
            return None
        if not output_path:
            print("An explicit output path is required here.")
            continue

        base_folder, _lib_name = parse_output_base_path(output=output_path)
        if os.path.isdir(base_folder):
            return output_path

        print(f"Can't find the folder: {base_folder}")


def prompt_for_custom_fields(arguments: dict) -> None:
    wants_custom_fields = prompt_yes_no(
        "Add more custom symbol properties?"
        if arguments["custom_field"]
        else "Add custom symbol properties?",
        default=False,
    )
    while wants_custom_fields:
        custom_field = prompt_text("Custom field (KEY:VALUE): ").strip()
        try:
            custom_fields = parse_custom_fields([custom_field])
        except ValueError as err:
            print(err)
            continue

        new_key = next(iter(custom_fields))
        existing_fields = parse_custom_fields(arguments["custom_field"])
        if new_key in existing_fields:
            print(f"Replacing custom field '{new_key}'.")

        arguments["custom_field"].append(custom_field)
        wants_custom_fields = prompt_yes_no(
            "Add another custom field?", default=False
        )


def prompt_for_arguments(arguments: dict) -> None:
    if not arguments.get("lcsc_id"):
        arguments["lcsc_id"] = prompt_for_lcsc_id()

    if not any([arguments["symbol"], arguments["footprint"], arguments["3d"]]):
        prompt_for_actions(arguments)

    if not arguments["output"]:
        default_output_base_path = get_default_output_base_path()
        prompted_output = prompt_for_output_path(
            prompt=f"Output library base path [default: {default_output_base_path}]: ",
            allow_blank=True,
        )
        if prompted_output:
            arguments["output"] = prompted_output

    if not arguments["overwrite"]:
        arguments["overwrite"] = prompt_yes_no(
            "Overwrite existing symbol/footprint if already present?", default=False
        )

    if arguments["footprint"] and not arguments["project_relative"]:
        arguments["project_relative"] = prompt_yes_no(
            "Store 3D path relative to the project?", default=False
        )

    if arguments["project_relative"] and not arguments["output"]:
        arguments["output"] = prompt_for_output_path(
            prompt="Output library base path (required for project-relative paths): ",
            allow_blank=False,
        )

    if arguments["symbol"] and not arguments["v5"]:
        prompt_for_custom_fields(arguments)


def should_use_interactive(arguments: dict) -> bool:
    missing_required_arguments = not arguments.get("lcsc_id") or not any(
        [arguments["symbol"], arguments["footprint"], arguments["3d"]]
    )
    return arguments["interactive"] or (
        missing_required_arguments and is_interactive_terminal()
    )


def prompt_overwrite_for_duplicate(item_name: str) -> bool:
    return prompt_yes_no(f"{item_name} already exists in the target library. Overwrite?")


def get_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        description=(
            "A Python script that convert any electronic components from LCSC or"
            " EasyEDA to a Kicad library"
        )
    )

    parser.add_argument("--lcsc_id", help="LCSC id", required=False, type=str)

    parser.add_argument(
        "--symbol", help="Get symbol of this id", required=False, action="store_true"
    )

    parser.add_argument(
        "--footprint",
        help="Get footprint of this id",
        required=False,
        action="store_true",
    )

    parser.add_argument(
        "--3d",
        help="Get the 3d model of this id",
        required=False,
        action="store_true",
    )

    parser.add_argument(
        "--full",
        help="Get the symbol, footprint and 3d model of this id",
        required=False,
        action="store_true",
    )

    parser.add_argument(
        "--output",
        required=False,
        metavar="file.kicad_sym",
        help="Library base path to create or append to",
        type=str,
    )

    parser.add_argument(
        "--overwrite",
        required=False,
        help=(
            "replace an existing symbol or footprint already present in the target"
            " library"
        ),
        action="store_true",
    )

    parser.add_argument(
        "--custom-field",
        required=False,
        action="append",
        default=[],
        metavar="KEY:VALUE",
        help="Add a custom symbol property (repeatable, KiCad v6 symbol export only)",
    )

    parser.add_argument(
        "--interactive",
        required=False,
        help="Prompt for missing values and key export options in the terminal",
        action="store_true",
    )

    parser.add_argument(
        "--v5",
        required=False,
        help="Convert library in legacy format for KiCad 5.x",
        action="store_true",
    )

    parser.add_argument(
        "--project-relative",
        required=False,
        help="Sets the 3D file path stored relative to the project",
        action="store_true",
    )

    parser.add_argument(
        "--debug",
        help="set the logging level to debug",
        required=False,
        default=False,
        action="store_true",
    )

    return parser


def valid_arguments(arguments: dict) -> bool:

    if not arguments.get("lcsc_id"):
        logging.error("Missing --lcsc_id")
        return False

    if not arguments["lcsc_id"].startswith("C"):
        logging.error("lcsc_id should start by C....")
        return False

    if arguments["full"]:
        arguments["symbol"], arguments["footprint"], arguments["3d"] = True, True, True

    if not any([arguments["symbol"], arguments["footprint"], arguments["3d"]]):
        logging.error(
            "Missing action arguments\n"
            "  easyeda2kicad --lcsc_id=C2040 --footprint\n"
            "  easyeda2kicad --lcsc_id=C2040 --symbol"
        )
        return False

    kicad_version = KicadVersion.v5 if arguments.get("v5") else KicadVersion.v6
    arguments["kicad_version"] = kicad_version

    try:
        arguments["custom_fields"] = parse_custom_fields(
            custom_field_args=arguments["custom_field"]
        )
    except ValueError as err:
        logging.error(err)
        return False

    if arguments["custom_fields"] and kicad_version == KicadVersion.v5:
        logging.error("--custom-field is currently supported only for KiCad v6")
        return False

    if arguments["project_relative"] and not arguments["output"]:
        logging.error(
            "A project specific library path should be given with --output option when"
            " using --project-relative option\nFor example: easyeda2kicad"
            " --lcsc_id=C2040 --full"
            " --output=C:/Users/your_username/Documents/Kicad/6.0/projects/my_project"
            " --project-relative"
        )
        return False

    if arguments["output"]:
        base_folder, lib_name = parse_output_base_path(output=arguments["output"])

        if not os.path.isdir(base_folder):
            logging.error(f"Can't find the folder : {base_folder}")
            return False
    else:
        default_folder = os.path.join(
            os.path.dirname(get_default_output_base_path()),
        )
        if not os.path.isdir(default_folder):
            os.makedirs(default_folder, exist_ok=True)

        base_folder = default_folder
        lib_name = "easyeda2kicad"
        arguments["use_default_folder"] = True

    arguments["output"] = f"{base_folder}/{lib_name}"

    # Create new footprint folder if it does not exist
    if arguments["footprint"]:
        if not os.path.isdir(f"{arguments['output']}.pretty"):
            os.mkdir(f"{arguments['output']}.pretty")
            logging.info(f"Created {lib_name}.pretty footprint folder in {base_folder}")

    # Create new 3d model folder if it does not exist
    if arguments["3d"]:
        if not os.path.isdir(f"{arguments['output']}"):
            os.mkdir(f"{arguments['output']}")
            logging.info(f"Created {lib_name} 3D model folder in {base_folder}")

    # Create new symbol file if it does not exist
    if arguments["symbol"]:
        lib_extension = "kicad_sym" if kicad_version == KicadVersion.v6 else "lib"
        if not os.path.isfile(f"{arguments['output']}.{lib_extension}"):
            with open(
                file=f"{arguments['output']}.{lib_extension}", mode="w+", encoding="utf-8"
            ) as my_lib:
                my_lib.write(
                    dedent(
                        """\
                    (kicad_symbol_lib
                      (version 20211014)
                      (generator https://github.com/georgesleen/easyeda2kicad.py)
                    )"""
                    )
                    if kicad_version == KicadVersion.v6
                    else "EESchema-LIBRARY Version 2.4\n#encoding utf-8\n"
                )
            logging.info(f"Created {lib_name}.{lib_extension} symbol lib in {base_folder}")

    return True


def delete_component_in_symbol_lib(
    lib_path: str, component_id: str, component_name: str
) -> None:
    with open(file=lib_path, encoding="utf-8") as f:
        current_lib = f.read()
        new_data = re.sub(
            rf'(#\n# {component_name}\n#\n.*?F6 "{component_id}".*?ENDDEF\n)',
            "",
            current_lib,
            flags=re.DOTALL,
        )

    with open(file=lib_path, mode="w", encoding="utf-8") as my_lib:
        my_lib.write(new_data)


def fp_already_in_footprint_lib(lib_path: str, package_name: str) -> bool:
    if os.path.isfile(f"{lib_path}/{package_name}.kicad_mod"):
        logging.warning(f"The footprint for this id is already in {lib_path}")
        return True
    return False


def main(argv: List[str] = sys.argv[1:]) -> int:
    print(f"-- easyeda2kicad.py v{__version__} --")

    # cli interface
    parser = get_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as err:
        return err.code
    arguments = vars(args)

    if arguments["debug"]:
        set_logger(log_file=None, log_level=logging.DEBUG)
    else:
        set_logger(log_file=None, log_level=logging.INFO)

    if arguments["interactive"] and not is_interactive_terminal():
        logging.error("--interactive requires an interactive terminal")
        return 1

    arguments["interactive_session"] = should_use_interactive(arguments)
    if arguments["interactive_session"]:
        try:
            prompt_for_arguments(arguments)
        except InteractiveAbortError:
            logging.error("Interactive input cancelled")
            return 1

    if not valid_arguments(arguments=arguments):
        return 1

    # conf = get_local_config()

    component_id = arguments["lcsc_id"]
    kicad_version = arguments["kicad_version"]
    sym_lib_ext = "kicad_sym" if kicad_version == KicadVersion.v6 else "lib"

    # Get CAD data of the component using easyeda API
    api = EasyedaApi()
    cad_data = api.get_cad_data_of_component(lcsc_id=component_id)

    # API returned no data
    if not cad_data:
        logging.error(f"Failed to fetch data from EasyEDA API for part {component_id}")
        return 1

    # ---------------- SYMBOL ----------------
    if arguments["symbol"]:
        # Problems occur with CRLF (windows) style line terminators
        # This script changes all CRLF line terminators to LF (unix) style line terminators
        windows_to_unix_in_place(f"{arguments['output']}.{sym_lib_ext}")

        importer = EasyedaSymbolImporter(easyeda_cp_cad_data=cad_data)
        easyeda_symbol: EeSymbol = importer.get_symbol()
        # print(easyeda_symbol)

        is_id_already_in_symbol_lib = id_already_in_symbol_lib(
            lib_path=f"{arguments['output']}.{sym_lib_ext}",
            component_name=easyeda_symbol.info.name,
            kicad_version=kicad_version,
        )

        overwrite_symbol = arguments["overwrite"]
        if not overwrite_symbol and is_id_already_in_symbol_lib:
            if arguments["interactive_session"]:
                overwrite_symbol = prompt_overwrite_for_duplicate("Symbol")
                if not overwrite_symbol:
                    logging.info("Skipping symbol export")
                    is_id_already_in_symbol_lib = False
                    easyeda_symbol = None
            else:
                logging.error("Use --overwrite to update the older symbol lib")
                return 1

        if easyeda_symbol is None:
            pass
        else:
            exporter = ExporterSymbolKicad(
                symbol=easyeda_symbol,
                kicad_version=kicad_version,
                custom_fields=arguments["custom_fields"],
            )
            # print(exporter.output)
            kicad_symbol_lib = exporter.export(
                footprint_lib_name=arguments["output"].split("/")[-1].split(".")[0],
            )

            if is_id_already_in_symbol_lib:
                update_component_in_symbol_lib_file(
                    lib_path=f"{arguments['output']}.{sym_lib_ext}",
                    component_name=easyeda_symbol.info.name,
                    component_content=kicad_symbol_lib,
                    kicad_version=kicad_version,
                )
            else:
                add_component_in_symbol_lib_file(
                    lib_path=f"{arguments['output']}.{sym_lib_ext}",
                    component_content=kicad_symbol_lib,
                    kicad_version=kicad_version,
                )

            logging.info(
                f"Created Kicad symbol for ID : {component_id}\n"
                f"       Symbol name : {easyeda_symbol.info.name}\n"
                f"       Library path : {arguments['output']}.{sym_lib_ext}"
            )

    # ---------------- FOOTPRINT ----------------
    if arguments["footprint"]:
        importer = EasyedaFootprintImporter(easyeda_cp_cad_data=cad_data)
        easyeda_footprint = importer.get_footprint()

        is_id_already_in_footprint_lib = fp_already_in_footprint_lib(
            lib_path=f"{arguments['output']}.pretty",
            package_name=easyeda_footprint.info.name,
        )
        overwrite_footprint = arguments["overwrite"]
        if not overwrite_footprint and is_id_already_in_footprint_lib:
            if arguments["interactive_session"]:
                overwrite_footprint = prompt_overwrite_for_duplicate("Footprint")
                if not overwrite_footprint:
                    logging.info("Skipping footprint export")
                    easyeda_footprint = None
            else:
                logging.error("Use --overwrite to replace the older footprint lib")
                return 1

        if easyeda_footprint is None:
            pass
        else:
            ki_footprint = ExporterFootprintKicad(footprint=easyeda_footprint)
            footprint_filename = f"{easyeda_footprint.info.name}.kicad_mod"
            footprint_path = f"{arguments['output']}.pretty"
            model_3d_path = f"{arguments['output']}.3dshapes".replace(
                "\\", "/"
            ).replace("./", "/")

            if arguments.get("use_default_folder"):
                model_3d_path = "${EASYEDA2KICAD}/easyeda2kicad.3dshapes"
            if arguments["project_relative"]:
                model_3d_path = "${KIPRJMOD}" + model_3d_path

            ki_footprint.export(
                footprint_full_path=f"{footprint_path}/{footprint_filename}",
                model_3d_path=model_3d_path,
            )

            logging.info(
                f"Created Kicad footprint for ID: {component_id}\n"
                f"       Footprint name: {easyeda_footprint.info.name}\n"
                f"       Footprint path: {os.path.join(footprint_path, footprint_filename)}"
            )

    # ---------------- 3D MODEL ----------------
    if arguments["3d"]:
        exporter = Exporter3dModelKicad(
            model_3d=Easyeda3dModelImporter(
                easyeda_cp_cad_data=cad_data, download_raw_3d_model=True
            ).output
        )
        exporter.export(lib_path=arguments["output"])
        if exporter.output or exporter.output_step:
            filename_wrl = f"{exporter.output.name}.wrl"
            filename_step = f"{exporter.output.name}.step"
            lib_path = f"{arguments['output']}"

            logging.info(
                f"Created 3D model for ID: {component_id}\n"
                f"       3D model name: {exporter.output.name}\n"
                + (
                    "       3D model path (wrl):"
                    f" {os.path.join(lib_path, filename_wrl)}\n"
                    if filename_wrl
                    else ""
                )
                + (
                    "       3D model path (step):"
                    f" {os.path.join(lib_path, filename_step)}\n"
                    if filename_step
                    else ""
                )
            )

        # logging.info(f"3D model: {os.path.join(lib_path, filename)}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

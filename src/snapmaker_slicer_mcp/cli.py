"""Installed CLI and MCP server share one native engine boundary."""
import argparse
import json
import sys
from pathlib import Path

from .engine import Engine
from .service import Slicer, write_json


def main(argv=None):
    parser = argparse.ArgumentParser(prog="snapmaker-slicer-mcp")
    parser.add_argument("--engine", type=Path, default=Path("engine.json"))
    parser.add_argument("--artifacts", type=Path)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--presets", type=Path, action="append", default=[],
                        help="Additional read-only preset root (repeatable); edits are artifact drafts only")
    commands = parser.add_subparsers(dest="command", required=True)
    bind = commands.add_parser("bind-engine", help="Record an explicitly selected official native main build")
    bind.add_argument("--binary", type=Path, required=True)
    bind.add_argument("--commit", required=True)
    bind.add_argument("--resources", type=Path, required=True)
    status = commands.add_parser("status")
    status.add_argument("--check-updates", action="store_true")
    sliced = commands.add_parser("slice")
    sliced.add_argument("input_3mf")
    sliced.add_argument("--plate", type=int, default=0)
    sliced.add_argument("--force", action="store_true")
    sliced.add_argument("--strict-file-version", action="store_true")
    recovery = commands.add_parser("recover")
    recovery.add_argument("gcode_file")
    serve = commands.add_parser("serve")
    serve.add_argument("--no-update-check", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "bind-engine":
            result = Engine.bind(args.binary, args.commit, args.resources)
            args.engine.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.engine, result)
        else:
            engine = Engine(json.loads(args.engine.read_text()))
            if args.command == "status":
                result = engine.status(check_remote=args.check_updates)
            else:
                slicer = Slicer(engine, args.artifacts or args.engine.resolve().parent / "artifacts", args.timeout)
                if args.command == "slice":
                    result = slicer.slice_3mf(args.input_3mf, args.plate, args.force, not args.strict_file_version)
                elif args.command == "recover":
                    result = slicer.recover_3mf(args.gcode_file)
                else:
                    from .server import build_server
                    if not args.no_update_check:
                        engine.status(check_remote=True)
                    build_server(slicer, args.presets).run(transport="stdio")
                    return
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr if args.command == "serve" else sys.stdout)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()

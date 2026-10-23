"""Installed CLI and MCP server share one native engine boundary."""
import argparse
from collections.abc import Sequence
import json
import sys
from pathlib import Path

from .engine import Engine
from .domain.types.json import json_object
from .service import write_json
from .bootstrap import build_slicer


def main(argv: Sequence[str] | None = None) -> None:
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
    serve.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.add_argument("--auth-token-file", type=Path)
    serve.add_argument("--allowed-host", action="append", default=[],
                       help="Exact HTTP Host header, including port (repeatable); defaults to loopbacks")
    serve.add_argument("--allowed-origin", action="append", default=[],
                       help="Exact allowed HTTP Origin (repeatable); absent Origin is accepted")
    args = parser.parse_args(argv)
    try:
        if args.command == "bind-engine":
            result = Engine.bind(args.binary, args.commit, args.resources)
            args.engine.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.engine, result)
        else:
            engine = Engine(json_object(json.loads(args.engine.read_text())))
            if args.command == "status":
                result = engine.status(check_remote=args.check_updates)
            else:
                slicer = build_slicer(engine, args.artifacts or args.engine.resolve().parent / "artifacts", args.timeout)
                if args.command == "slice":
                    result = slicer.slice_3mf(args.input_3mf, args.plate, args.force, not args.strict_file_version)
                elif args.command == "recover":
                    result = slicer.recover_3mf(args.gcode_file)
                else:
                    if args.transport == "streamable-http":
                        import uvicorn
                        from .http_server import build_http_app
                        if not 1 <= args.port <= 65535:
                            raise ValueError("HTTP port must be between 1 and 65535")
                        hosts = args.allowed_host or [f"127.0.0.1:{args.port}", f"localhost:{args.port}", f"[::1]:{args.port}"]
                        if not args.allowed_host and args.port == 80:
                            hosts += ["127.0.0.1", "localhost", "[::1]"]
                        app = build_http_app(slicer, args.presets, auth_token_file=args.auth_token_file,
                                             allowed_hosts=hosts, allowed_origins=args.allowed_origin)
                        if not args.no_update_check:
                            engine.status(check_remote=True)
                        uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False)
                    else:
                        from .server import build_server
                        if not args.no_update_check:
                            engine.status(check_remote=True)
                        build_server(slicer, args.presets).run(transport="stdio", show_banner=False)
                    return
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (OSError, ValueError, KeyError, RuntimeError, TimeoutError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr if args.command == "serve" else sys.stdout)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()

"""Small cross-platform CLI; no machine-specific interpreter or dataset paths."""

import argparse
import json
import logging
from pathlib import Path
import webbrowser

from . import __version__
from .dataset import create_demo, import_images
from .server import LocalServer
from .storage import Dataset


def main(argv=None):
    parser = argparse.ArgumentParser(prog="label-tools")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser(
        "init", help="Import a folder of 2D images without changing source pixels"
    )
    init.add_argument("images", type=Path)
    init.add_argument("--output", type=Path, required=True)
    init.add_argument("--resolution-nm", type=float)
    init.add_argument("--target", default="Object")
    init.add_argument("--language", choices=("en", "zh"), default="en")
    demo = sub.add_parser("demo", help="Create a small synthetic demonstration dataset")
    demo.add_argument("--output", type=Path, required=True)
    demo.add_argument("--language", choices=("en", "zh"), default="en")
    serve = sub.add_parser("serve", help="Open a dataset on this computer only")
    serve.add_argument("dataset", type=Path)
    serve.add_argument("--port", type=int, default=8766)
    serve.add_argument("--open", action="store_true")
    for name in ("verify", "export"):
        command = sub.add_parser(
            name, help="Verify images" if name == "verify" else "Export reviewed image-label pairs"
        )
        command.add_argument("dataset", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            result = import_images(
                args.images,
                args.output,
                resolution_nm=args.resolution_nm,
                target=args.target,
                language=args.language,
            )
            print(f"Imported {len(result['records'])} images into {args.output.resolve()}")
        elif args.command == "demo":
            create_demo(args.output, args.language)
            print(f"Synthetic dataset: {args.output.resolve()}")
        elif args.command == "verify":
            print(json.dumps(Dataset(args.dataset).verify(), indent=2))
        elif args.command == "export":
            print(json.dumps(Dataset(args.dataset).export(), indent=2))
        elif args.command == "serve":
            if not 0 <= args.port <= 65535:
                raise ValueError("Port must be between 0 and 65535")
            logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
            with LocalServer(Dataset(args.dataset), args.port) as server:
                print(f"Label-Tools {__version__}: {server.url}", flush=True)
                print("Local computer only. Press Ctrl+C to stop.", flush=True)
                if args.open:
                    webbrowser.open(server.url)
                server.serve_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        parser.exit(2, f"label-tools: {exc}\n")

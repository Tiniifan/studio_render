"""studio_render_cli: the console of studio_render (python cli.py ..., studio_render_cli.exe once built).

Commands:
* headless: renders of a technique without a window (PNG frames, .mp4), as JSON lines on stdout (see headless.py,
  python cli.py headless --help).

Exit codes: 0 ok, 2 usage, 3 refused or failed (the code in the JSON), 4 engine not installed, 10 internal error.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

EXIT_OK = 0
EXIT_USAGE = 2

BUILTINS = {
    "headless": "renders of a technique without a window (PNG frames, .mp4), as JSON lines on stdout",
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and not stream.isatty() and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if argv and argv[0] == "headless":
        import headless
        return headless.main(argv[1:])
    parser = argparse.ArgumentParser(prog="studio_render_cli", description="The console of studio_render.")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    for name, text in BUILTINS.items():
        commands.add_parser(name, help=text, add_help=False)
    args, _ = parser.parse_known_args(argv)
    if args.command is None:
        parser.print_help(sys.stderr)
        return EXIT_USAGE
    return EXIT_OK


if __name__ == "__main__":
    # The archives are read in worker processes (model.preload), the built exe starts them as copies of itself
    import multiprocessing
    multiprocessing.freeze_support()
    sys.exit(main())

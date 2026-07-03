"""Launch the local Streamlit Dashboard for RAG observability.

This script is the Phase F operator entry point for the visual management
platform. It validates that the Dashboard app target is importable, constructs
the ``streamlit run`` command, and delegates process execution to an injectable
runner so tests can verify behavior without opening a browser.

The launcher owns only command-line concerns. It does not render individual
pages, open PostgreSQL pools, mutate indexed documents, or run evaluations.
Those responsibilities stay inside Dashboard page modules and services.
"""

from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from dotenv import find_dotenv, load_dotenv

from src.core.config import RAG_ROOT

DEFAULT_DASHBOARD_PORT = 8501
APP_MODULE = "src.observability.dashboard.app"
APP_RELATIVE_PATH = Path("src") / "observability" / "dashboard" / "app.py"

CommandRunner = Callable[[list[str]], int]
MessageWriter = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class DashboardLaunchConfig:
    """Capture resolved launch settings for one Dashboard process.

    Attributes:
        app_path: Absolute path to the Streamlit app file.
        port: Local HTTP port passed to Streamlit.
        headless: Whether Streamlit should avoid browser startup.
        extra_args: Additional raw arguments appended after managed options.
    """

    app_path: Path
    port: int
    headless: bool = True
    extra_args: tuple[str, ...] = ()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse Dashboard launcher command-line options.

    Args:
        argv: Optional argument sequence excluding the executable name. ``None``
            delegates to ``sys.argv`` for normal script execution.

    Returns:
        Namespace containing the local port, browser behavior, dry-run flag,
        and optional extra Streamlit arguments.

    Raises:
        SystemExit: If the port is not a positive integer or arguments are
            malformed.
    """

    parser = argparse.ArgumentParser(
        description="Start the local AImodel RAG Streamlit Dashboard."
    )
    parser.add_argument(
        "--port",
        type=_positive_integer,
        default=DEFAULT_DASHBOARD_PORT,
        help=f"Local Streamlit port (default: {DEFAULT_DASHBOARD_PORT}).",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Allow Streamlit to open a browser tab when supported.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate app loading and print the command without executing it.",
    )
    parser.add_argument(
        "extra_args",
        nargs=argparse.REMAINDER,
        help="Additional arguments forwarded to Streamlit after '--'.",
    )
    return parser.parse_args(argv)


def run_dashboard(
    argv: Sequence[str] | None = None,
    *,
    command_runner: CommandRunner | None = None,
    output: MessageWriter = print,
    error_output: MessageWriter | None = None,
) -> int:
    """Validate and launch the local Streamlit Dashboard.

    Args:
        argv: Optional CLI arguments excluding the executable name.
        command_runner: Injectable command executor. Production uses
            ``subprocess.call``; tests pass a recorder to avoid starting
            Streamlit.
        output: Writer receiving the dry-run JSON payload or startup message.
        error_output: Writer receiving a readable failure message. ``None``
            writes to standard error.

    Returns:
        ``0`` when validation succeeds and Streamlit exits successfully, or
        ``1`` when the app cannot be loaded or the command runner fails.

    Side Effects:
        Loads the nearest local ``.env`` file without overriding existing
        process variables. In non-dry mode, starts a Streamlit subprocess
        through the provided command runner.
    """

    args = parse_args(argv)
    write_error = error_output or _print_error
    runner = command_runner or subprocess.call
    try:
        _load_local_environment()
        app_path = resolve_dashboard_app_path()
        app_module = load_dashboard_app()
        loaded_pages = tuple(app_module.load_dashboard_pages())
        config = DashboardLaunchConfig(
            app_path=app_path,
            port=args.port,
            headless=not args.no_headless,
            extra_args=tuple(_normalize_extra_args(args.extra_args)),
        )
        command = build_streamlit_command(config)
        if args.dry_run:
            output(
                json.dumps(
                    {
                        "app_path": app_path.as_posix(),
                        "command": command,
                        "loaded_pages": list(loaded_pages),
                    },
                    ensure_ascii=False,
                )
            )
            return 0

        output(f"Starting AImodel RAG Dashboard on port {args.port}.")
        return runner(command)
    except Exception as error:
        write_error(f"Dashboard launch failed: {error}")
        return 1


def resolve_dashboard_app_path() -> Path:
    """Resolve the Streamlit app path relative to the RAG project root.

    Returns:
        Absolute path to ``src/observability/dashboard/app.py``.

    Raises:
        FileNotFoundError: If the Dashboard app target is missing.
    """

    app_path = (RAG_ROOT / APP_RELATIVE_PATH).resolve()
    if not app_path.is_file():
        raise FileNotFoundError(f"Dashboard app file does not exist: {app_path}")
    return app_path


def load_dashboard_app() -> ModuleType:
    """Import the Dashboard app module and validate its public contract.

    Returns:
        Imported Dashboard app module.

    Raises:
        AttributeError: If the module does not expose the expected ``main`` and
            ``load_dashboard_pages`` callables.
    """

    module = importlib.import_module(APP_MODULE)
    for attribute_name in ("main", "load_dashboard_pages"):
        attribute = getattr(module, attribute_name, None)
        if not callable(attribute):
            raise AttributeError(
                f"Dashboard app module must expose callable {attribute_name}()"
            )
    return module


def build_streamlit_command(config: DashboardLaunchConfig) -> list[str]:
    """Build the exact Streamlit command for the resolved launch config.

    Args:
        config: Resolved app path, port, browser behavior, and optional
            forwarded arguments.

    Returns:
        Command vector suitable for ``subprocess.call`` without shell parsing.
    """

    command = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(config.app_path),
        "--server.port",
        str(config.port),
        "--server.headless",
        "true" if config.headless else "false",
        "--browser.gatherUsageStats",
        "false",
    ]
    command.extend(config.extra_args)
    return command


def main() -> int:
    """Run the Dashboard launcher with process arguments.

    Returns:
        Process exit code returned by ``run_dashboard``.
    """

    return run_dashboard()


def _load_local_environment() -> None:
    """Load the nearest parent ``.env`` file for local Dashboard execution.

    Existing shell, Docker, or CI variables remain authoritative because this
    helper never overwrites already-defined environment values.
    """

    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)


def _normalize_extra_args(extra_args: Sequence[str]) -> list[str]:
    """Remove argparse's optional ``--`` separator from forwarded arguments."""

    if extra_args and extra_args[0] == "--":
        return list(extra_args[1:])
    return list(extra_args)


def _positive_integer(value: str) -> int:
    """Convert one CLI value to a strictly positive integer.

    Args:
        value: Raw command-line token provided to ``--port``.

    Returns:
        Parsed integer greater than zero.

    Raises:
        argparse.ArgumentTypeError: If the value is not an integer or is less
            than one.
    """

    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "--port must be an integer"
        ) from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("--port must be greater than zero")
    return parsed


def _print_error(message: str) -> None:
    """Write one launcher error message to standard error."""

    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())

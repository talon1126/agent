"""Compose installed dataset processors with the site-independent CLI.

This module is the data-ops application boundary. Concrete processors are
registered here, while data_ops.cli and the generic registry remain free of
site imports and can still be tested with synthetic processors.
"""

from __future__ import annotations

from collections.abc import Sequence

from data_ops.cli import main as run_cli
from data_ops.processors.jd_product import register_jd_product_processor


def main(argv: Sequence[str] | None = None) -> int:
    """Register shipped processors and run the generic command-line workflow.

    Args:
        argv: Optional CLI arguments. None delegates to process arguments.

    Returns:
        The generic CLI exit code.

    Side Effects:
        Registers the JD processor in the current process and may process,
        archive, or quarantine the input selected by CLI arguments.
    """

    register_jd_product_processor()
    return run_cli(argv)


def entrypoint() -> None:
    """Run the installed console command and expose its exit status."""

    raise SystemExit(main())


__all__ = ["entrypoint", "main"]

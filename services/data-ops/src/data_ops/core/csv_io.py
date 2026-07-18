"""Read immutable source files and write deterministic canonical CSV outputs.

All raw columns are loaded as pandas string values so identifiers, leading
zeros, and empty text survive the handoff. Canonical writes use a temporary
file in the destination directory followed by os.replace; this module does not
archive, quarantine, or mutate the source file.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pandas as pd

type SheetName = str | int


class SourceFileError(ValueError):
    """Raised when an input file cannot be decoded or represented as one table."""


class CanonicalWriteError(ValueError):
    """Raised when a table cannot satisfy the requested canonical column order."""


def read_source_file(
    source_path: str | Path,
    *,
    encoding: str = "utf-8-sig",
    delimiter: str = ",",
    sheet_name: SheetName = 0,
) -> pd.DataFrame:
    """Read CSV or XLSX source data without inferring business value types.

    Args:
        source_path: Existing CSV or XLSX file supplied to data-ops.
        encoding: Explicit CSV text encoding.
        delimiter: Explicit one-character CSV delimiter.
        sheet_name: XLSX sheet name or zero-based position.

    Returns:
        A DataFrame whose columns use pandas string dtype and whose blank cells
        are represented by empty strings.

    Raises:
        SourceFileError: If the file is missing, empty, unsupported, undecodable,
            or the requested workbook sheet cannot be read.
    """

    path = Path(source_path)
    if not path.is_file():
        raise SourceFileError(f"source file does not exist: {path}")
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            if not delimiter or len(delimiter) != 1:
                raise SourceFileError("delimiter must be exactly one character")
            frame = pd.read_csv(
                path,
                encoding=encoding,
                sep=delimiter,
                dtype="string",
                keep_default_na=False,
            )
        elif suffix == ".xlsx":
            frame = pd.read_excel(
                path,
                sheet_name=sheet_name,
                dtype="string",
                keep_default_na=False,
                engine="openpyxl",
            )
        else:
            raise SourceFileError(
                f"unsupported source file extension: {suffix or '<none>'}"
            )
    except UnicodeDecodeError as exc:
        raise SourceFileError(
            f"source file encoding does not match {encoding}: {path}"
        ) from exc
    except pd.errors.EmptyDataError as exc:
        raise SourceFileError(f"source file is empty: {path}") from exc
    except (OSError, ValueError) as exc:
        if isinstance(exc, SourceFileError):
            raise
        raise SourceFileError(f"cannot read source file {path}: {exc}") from exc

    if not isinstance(frame, pd.DataFrame):
        raise SourceFileError("source file must resolve to exactly one worksheet")
    if not len(frame.columns):
        raise SourceFileError(f"source file is empty: {path}")
    return frame.fillna("").astype("string")


def write_canonical_csv(
    frame: pd.DataFrame,
    destination: str | Path,
    *,
    columns: tuple[str, ...],
) -> Path:
    """Write deterministic UTF-8 CSV bytes using an atomic file replacement.

    Args:
        frame: Table to write.
        destination: Final CSV path.
        columns: Exact output column order.

    Returns:
        The normalized destination path.

    Raises:
        CanonicalWriteError: If required columns are missing or destination is
            not a CSV path.

    Side Effects:
        Creates the destination directory, writes a temporary sibling file, and
        atomically replaces the destination after serialization succeeds.
    """

    path = Path(destination)
    if path.suffix.lower() != ".csv":
        raise CanonicalWriteError("canonical output path must end with .csv")
    missing = tuple(column for column in columns if column not in frame)
    if missing:
        raise CanonicalWriteError(
            "canonical output is missing columns: " + ", ".join(missing)
        )
    if len(columns) != len(set(columns)):
        raise CanonicalWriteError("canonical output columns must be unique")

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}-",
        suffix=".tmp",
        dir=path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        frame.loc[:, list(columns)].to_csv(
            temporary_path,
            index=False,
            encoding="utf-8",
            lineterminator="\n",
        )
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


__all__ = [
    "CanonicalWriteError",
    "SourceFileError",
    "read_source_file",
    "write_canonical_csv",
]

"""Route generic dataset types to processor factories.

The registry validates the processor protocol and its contract at resolution
time. It intentionally contains no imports from concrete processor modules, so
new datasets are added through registration rather than generic-core branches.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from data_ops.core.contracts import DatasetProcessor

ProcessorFactory = Callable[[], DatasetProcessor[Any]]


class ProcessorRegistryError(LookupError):
    """Base class for stable processor routing failures."""


class DuplicateProcessorError(ProcessorRegistryError):
    """Raised when registration would silently replace an existing factory."""


class UnknownDatasetTypeError(ProcessorRegistryError):
    """Raised when no processor factory is registered for a routing key."""


class InvalidProcessorError(ProcessorRegistryError):
    """Raised when a factory returns an object that violates its registration."""


_PROCESSOR_FACTORIES: dict[str, ProcessorFactory] = {}


def register_processor(dataset_type: str, factory: ProcessorFactory) -> None:
    """Register one processor factory without importing concrete implementations.

    Args:
        dataset_type: Lowercase snake-case routing key.
        factory: Zero-argument callable that creates a processor instance.

    Raises:
        ValueError: If dataset_type is not lowercase snake case.
        DuplicateProcessorError: If the routing key is already registered.
    """

    if not dataset_type or not dataset_type.replace("_", "").isalnum():
        raise ValueError("dataset_type must use lowercase snake_case")
    if dataset_type != dataset_type.lower() or not dataset_type[0].isalpha():
        raise ValueError("dataset_type must use lowercase snake_case")
    if dataset_type in _PROCESSOR_FACTORIES:
        raise DuplicateProcessorError(f"processor already registered: {dataset_type}")
    _PROCESSOR_FACTORIES[dataset_type] = factory


def get_processor(dataset_type: str) -> DatasetProcessor[Any]:
    """Create and validate the processor registered for dataset_type.

    Args:
        dataset_type: Explicit CLI or API routing key.

    Returns:
        A newly created processor instance.

    Raises:
        UnknownDatasetTypeError: If no factory is registered.
        InvalidProcessorError: If the factory result or contract does not match
            the registered routing key.
    """

    try:
        factory = _PROCESSOR_FACTORIES[dataset_type]
    except KeyError as exc:
        raise UnknownDatasetTypeError(
            f"no processor registered for dataset_type: {dataset_type}"
        ) from exc
    processor = factory()
    if not isinstance(processor, DatasetProcessor):
        raise InvalidProcessorError(
            f"processor for {dataset_type} does not implement DatasetProcessor"
        )
    if processor.dataset_type != dataset_type or processor.contract.dataset_type != dataset_type:
        raise InvalidProcessorError(
            f"processor contract does not match registered dataset_type: {dataset_type}"
        )
    return processor


__all__ = [
    "DuplicateProcessorError",
    "InvalidProcessorError",
    "ProcessorRegistryError",
    "UnknownDatasetTypeError",
    "get_processor",
    "register_processor",
]

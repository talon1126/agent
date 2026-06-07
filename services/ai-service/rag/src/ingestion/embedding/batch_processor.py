"""Run ingestion indexing work in bounded batches with failure isolation.

``BatchProcessor`` is the C8 batching boundary shared by Dense encoding and
BM25 indexing orchestration. It knows how to split ordered inputs, execute a
caller-supplied batch function, optionally pause between top-level batches,
isolate failed items when a batch fails, and retry isolated failures a limited
number of times. It deliberately does not know about embedding providers, BM25
internals, storage, traces, or pipeline lifecycle state.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from pydantic import BaseModel, ConfigDict, Field

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class BatchFailure(BaseModel):
    """Describe one item that could not be processed after retry attempts."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    item_index: int = Field(ge=0)
    item: Any
    attempts: int = Field(ge=1)
    error_type: str = Field(min_length=1)
    error_message: str = Field(min_length=1)


class BatchSuccess(BaseModel):
    """Describe one successfully processed item and its output value."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    item_index: int = Field(ge=0)
    item: Any
    value: Any


class BatchRunResult(BaseModel):
    """Collect ordered batch successes, failures, and execution counters."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    successes: list[BatchSuccess] = Field(default_factory=list)
    failures: list[BatchFailure] = Field(default_factory=list)
    batches_processed: int = Field(ge=0)

    def successful_values(self) -> list[Any]:
        """Return successful output values in original input order.

        Returns:
            Output values sorted by the source ``item_index`` tracked by the
            processor. Failed items are omitted because they have no safe output
            value to pass to later ingestion stages.
        """

        return [
            success.value
            for success in sorted(self.successes, key=lambda item: item.item_index)
        ]


class BatchProcessor:
    """Execute ordered items through a reusable batch-processing contract."""

    def __init__(
        self,
        *,
        batch_size: int,
        max_retries: int = 0,
        throttle_seconds: float = 0.0,
        sleeper: Callable[[float], None] | None = None,
    ) -> None:
        """Configure batch sizing and retry policy.

        Args:
            batch_size: Maximum number of items sent to one ``process_batch``
                call during normal execution.
            max_retries: Number of additional attempts for isolated failed
                items after the first individual failure.
            throttle_seconds: Optional pause inserted between configured
                top-level batches. The default keeps unit tests and local
                ingestion fast while allowing production wiring to slow request
                pressure against external providers.
            sleeper: Injectable sleep function used by tests to verify
                throttling without waiting in real time.

        Raises:
            ValueError: If ``batch_size`` is not positive or retries are
                negative, or if ``throttle_seconds`` is negative.
        """

        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must be greater than or equal to zero")
        if throttle_seconds < 0:
            raise ValueError("throttle_seconds must be greater than or equal to zero")
        self.batch_size = batch_size
        self.max_retries = max_retries
        self.throttle_seconds = throttle_seconds
        self._sleeper = sleeper or time.sleep

    def run(
        self,
        items: Sequence[InputT],
        *,
        process_batch: Callable[[list[InputT]], Sequence[OutputT]],
    ) -> BatchRunResult:
        """Process items in configured batches and isolate item failures.

        Args:
            items: Ordered input items. The processor never mutates this
                sequence.
            process_batch: Callable that accepts one non-empty list of items and
                returns one output per input item in the same order.

        Returns:
            A ``BatchRunResult`` with successful outputs sorted by original
            input order, permanent failures, and the number of calls made to
            ``process_batch``.

        Raises:
            ValueError: If ``process_batch`` returns a result count different
                from the number of items it received.
        """

        indexed_items = list(enumerate(items))
        successes: list[BatchSuccess] = []
        failures: list[BatchFailure] = []
        batches_processed = 0

        top_level_batches = [
            indexed_items[batch_start : batch_start + self.batch_size]
            for batch_start in range(0, len(indexed_items), self.batch_size)
        ]
        for batch_position, batch in enumerate(top_level_batches):
            batches_processed += 1
            try:
                successes.extend(self._process_indexed_batch(batch, process_batch))
            except Exception:
                isolation_result = self._isolate_failed_batch(batch, process_batch)
                batches_processed += isolation_result.batches_processed
                successes.extend(isolation_result.successes)
                failures.extend(isolation_result.failures)
            if batch_position < len(top_level_batches) - 1:
                self._throttle()

        if failures and self.max_retries:
            retry_result = self.retry_failed(failures, process_batch=process_batch)
            batches_processed += retry_result.batches_processed
            successes.extend(retry_result.successes)
            failures = retry_result.failures

        return BatchRunResult(
            successes=sorted(successes, key=lambda item: item.item_index),
            failures=sorted(failures, key=lambda item: item.item_index),
            batches_processed=batches_processed,
        )

    def retry_failed(
        self,
        failures: Sequence[BatchFailure],
        *,
        process_batch: Callable[[list[Any]], Sequence[Any]],
    ) -> BatchRunResult:
        """Retry isolated failures according to the configured retry limit.

        Args:
            failures: Failed item records from the initial run or isolation
                phase.
            process_batch: Same batch callable used by ``run``. Retries always
                use one item per call to avoid reintroducing mixed-batch failure
                coupling.

        Returns:
            A ``BatchRunResult`` containing retry successes and any failures
            that still remain after ``max_retries`` additional attempts.
        """

        successes: list[BatchSuccess] = []
        remaining_failures: list[BatchFailure] = []
        batches_processed = 0

        for failure in failures:
            current_failure = failure
            for attempt_number in range(
                failure.attempts + 1,
                failure.attempts + self.max_retries + 1,
            ):
                batches_processed += 1
                try:
                    values = self._call_process_batch(
                        [failure.item],
                        process_batch,
                    )
                    successes.append(
                        BatchSuccess(
                            item_index=failure.item_index,
                            item=failure.item,
                            value=values[0],
                        )
                    )
                    break
                except Exception as error:
                    current_failure = _failure_from_error(
                        item_index=failure.item_index,
                        item=failure.item,
                        attempts=attempt_number,
                        error=error,
                    )
            else:
                remaining_failures.append(current_failure)

        return BatchRunResult(
            successes=successes,
            failures=remaining_failures,
            batches_processed=batches_processed,
        )

    def _isolate_failed_batch(
        self,
        batch: list[tuple[int, InputT]],
        process_batch: Callable[[list[InputT]], Sequence[OutputT]],
    ) -> BatchRunResult:
        """Retry each item from one failed batch independently.

        Args:
            batch: Indexed items from the failed batch.
            process_batch: Caller-supplied batch function.

        Returns:
            Per-item successes and failures after one isolated attempt each.
        """

        successes: list[BatchSuccess] = []
        failures: list[BatchFailure] = []
        batches_processed = 0

        for item_index, item in batch:
            batches_processed += 1
            try:
                values = self._call_process_batch([item], process_batch)
                successes.append(
                    BatchSuccess(
                        item_index=item_index,
                        item=item,
                        value=values[0],
                    )
                )
            except Exception as error:
                failures.append(
                    _failure_from_error(
                        item_index=item_index,
                        item=item,
                        attempts=1,
                        error=error,
                    )
                )

        return BatchRunResult(
            successes=successes,
            failures=failures,
            batches_processed=batches_processed,
        )

    def _process_indexed_batch(
        self,
        batch: list[tuple[int, InputT]],
        process_batch: Callable[[list[InputT]], Sequence[OutputT]],
    ) -> list[BatchSuccess]:
        """Process one indexed batch and attach source indexes to outputs.

        Args:
            batch: Input items paired with their original indexes.
            process_batch: Caller-supplied batch function.

        Returns:
            One ``BatchSuccess`` per processed item, preserving source indexes.

        Raises:
            ValueError: If output cardinality does not match input cardinality.
            Exception: Any exception raised by ``process_batch`` is intentionally
                allowed to bubble to ``run`` so the failed batch can be isolated.
        """

        items = [item for _, item in batch]
        values = self._call_process_batch(items, process_batch)
        return [
            BatchSuccess(item_index=item_index, item=item, value=value)
            for (item_index, item), value in zip(batch, values, strict=True)
        ]

    @staticmethod
    def _call_process_batch(
        items: list[InputT],
        process_batch: Callable[[list[InputT]], Sequence[OutputT]],
    ) -> list[OutputT]:
        """Call ``process_batch`` and validate one output per input.

        Args:
            items: Non-empty batch items.
            process_batch: Caller-supplied batch function.

        Returns:
            A list copy of the returned outputs.

        Raises:
            ValueError: If output cardinality does not match input cardinality.
        """

        values = list(process_batch(items))
        if len(values) != len(items):
            raise ValueError(
                "process_batch must return exactly one output per input item; "
                f"inputs={len(items)}, outputs={len(values)}"
            )
        return values

    def _throttle(self) -> None:
        """Pause between top-level batches when throttling is configured.

        Side Effects:
            Calls the configured sleeper only when ``throttle_seconds`` is
            greater than zero. Tests inject a fake sleeper so this method can be
            verified without real waiting.
        """

        if self.throttle_seconds > 0:
            self._sleeper(self.throttle_seconds)


def _failure_from_error(
    *,
    item_index: int,
    item: Any,
    attempts: int,
    error: Exception,
) -> BatchFailure:
    """Convert an exception raised by processing into a stable failure record.

    Args:
        item_index: Original index of the failed item.
        item: Failed source item.
        attempts: Number of individual attempts made for this item.
        error: Exception raised by the batch callable.

    Returns:
        A serializable ``BatchFailure`` with stable error type and message
        fields for trace and later dashboard display.
    """

    return BatchFailure(
        item_index=item_index,
        item=item,
        attempts=attempts,
        error_type=error.__class__.__name__,
        error_message=str(error),
    )

# SPDX-License-Identifier: MIT OR Apache-2.0
"""DB-free schema checking and the single corrective attempt for generation stages."""

from collections.abc import Callable, Sequence

from bonafides import local_model
from bonafides.problems import Problem


class OutputMismatch(Problem):
    """Model output that does not match its schema; the summary carries no owner data."""

    def __init__(self, summary: list[str]) -> None:
        super().__init__(422, "invalid_model_output")
        self.summary = summary[:12]


def checked_call[T](
    call: Callable[[Sequence[str] | None], local_model.ModelResult],
    validate: Callable[[dict[str, object]], T],
    before_correction: Callable[[], None] | None = None,
) -> tuple[T, local_model.ModelResult, bool]:
    """Validate one output, retrying only a schema mismatch and at most once.

    ``call`` owns transport and interruption checks; truncated, refused and otherwise
    failed calls propagate unchanged. Production reserves its corrective run through
    ``before_correction``; qualification needs no database reservation.
    """
    correction: list[str] | None = None
    while True:
        try:
            result = call(correction)
            return validate(result.output), result, correction is not None
        except OutputMismatch as exc:
            summary = exc.summary
        except local_model.ModelFailure as exc:
            if exc.code != "model_output_schema_mismatch":
                raise
            summary = ["output: json_or_schema_version_invalid"]
        if correction is not None:
            raise Problem(422, "model_output_invalid_after_correction")
        correction = summary
        if before_correction is not None:
            before_correction()

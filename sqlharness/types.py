from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class BirdExample:
    example_id: str
    db_id: str
    question: str
    evidence: str
    gold_sql: str
    difficulty: str = ""


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class Generation:
    sql: str
    model: str
    latency_ms: float
    usage: TokenUsage = field(default_factory=TokenUsage)
    raw_output: str = ""


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    normalized_sql: str | None
    errors: tuple[str, ...] = ()
    referenced_tables: tuple[str, ...] = ()
    referenced_columns: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionResult:
    succeeded: bool
    rows: tuple[tuple[Any, ...], ...] = ()
    columns: tuple[str, ...] = ()
    latency_ms: float = 0.0
    truncated: bool = False
    error: str | None = None


@dataclass(frozen=True)
class BenchmarkRecord:
    example: BirdExample
    generation: Generation
    validation: ValidationResult
    candidate_execution: ExecutionResult | None
    gold_execution: ExecutionResult
    execution_correct: bool | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

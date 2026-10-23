"""Immutable values shared by document and native geometry operations."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ObjectId:
    value: int

    def __post_init__(self) -> None:
        if type(self.value) is not int or self.value <= 0:
            raise ValueError('Object IDs must be positive integers')


@dataclass(frozen=True)
class Sha256:
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not re.fullmatch('[0-9a-f]{64}', self.value):
            raise ValueError('SHA256 must contain 64 lowercase hexadecimal characters')

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class XYZ:
    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (self.x, self.y, self.z)):
            raise ValueError('Coordinates must contain three finite XYZ numbers')

    def as_list(self) -> list[float]:
        return [float(self.x), float(self.y), float(self.z)]


@dataclass(frozen=True)
class BoxDimensions(XYZ):
    def __post_init__(self) -> None:
        super().__post_init__()
        if any(v <= 0 for v in (self.x, self.y, self.z)):
            raise ValueError('dimensions must be strictly positive')


@dataclass(frozen=True)
class LineRange:
    first: int
    last: int

    def __post_init__(self) -> None:
        if type(self.first) is not int or type(self.last) is not int or self.first < 1 or self.last < self.first:
            raise ValueError('Line ranges must be ordered positive integers')


@dataclass(frozen=True)
class HeightBand:
    low: float
    high: float
    height: float

    def __post_init__(self) -> None:
        if not all(math.isfinite(v) for v in (self.low, self.high, self.height)) or self.low < 0 or self.high <= self.low or self.height <= 0:
            raise ValueError('A band needs 0 <= min_z < max_z and positive layer_height')

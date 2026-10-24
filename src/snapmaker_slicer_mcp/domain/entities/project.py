"""Typed project descriptions; independent of storage and serializers."""
from typing import TypedDict


class MembershipRecord(TypedDict):
    object_id: int
    instance_id: int


class PlateRecord(TypedDict):
    plate: int
    name: str
    locked: bool
    objects: list[MembershipRecord]


class InstanceRecord(TypedDict):
    instance_id: int
    transform: list[float]
    plate: int | None
    printable: bool


class PartRecord(TypedDict):
    part_id: int
    name: str
    subtype: str
    config: dict[str, str]


class ObjectRecord(TypedDict):
    object_id: int
    name: str
    config: dict[str, str]
    instances: list[InstanceRecord]
    parts: list[PartRecord]


class ProjectDescription(TypedDict):
    objects: list[ObjectRecord]
    plates: list[PlateRecord]


class ModifierRecord(TypedDict):
    object_id: int
    part_id: int
    subtype: str


class DeletePlateRecord(TypedDict):
    deleted: bool
    plate: int
    count: int
    objects_moved_off_plate: int


class LayoutResult(TypedDict):
    plate_count: int
    changed_instances: int


class LayoutMembership(MembershipRecord):
    plate: int


class NativeLayout(TypedDict):
    plate_count: int
    instances: list[LayoutMembership]

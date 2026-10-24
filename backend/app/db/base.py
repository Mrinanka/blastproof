"""The declarative base for Blastproof's own tables (backend plan §5)."""

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, MetaData, Text
from sqlalchemy.orm import DeclarativeBase

# Every constraint gets a predictable name, so later migrations can find it. Indexes are named
# by hand in the models (some are on expressions, which have no column name to build one from).
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    # Plain `str` is Postgres `text` (no arbitrary lengths); every timestamp keeps its time zone.
    type_annotation_map = {str: Text(), datetime: DateTime(timezone=True)}


def enum_check(column: str, enum: type[StrEnum]) -> CheckConstraint:
    """A named CHECK that the column holds one of the enum's values (NULL passes; use nullable).

    Text plus CHECK instead of a Postgres enum type: adding a value later is a small migration.
    """
    values = ", ".join(f"'{member.value}'" for member in enum)
    return CheckConstraint(f"{column} IN ({values})", name=column)

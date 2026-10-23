"""Shared SQLAlchemy declarative base."""
from sqlalchemy import BigInteger, Integer
from sqlalchemy.orm import declarative_base

Base = declarative_base()

# SQLite (unit tests) only auto-numbers INTEGER primary keys, so BigInteger
# PKs carry an INTEGER variant to stay insertable there. PostgreSQL is
# unaffected.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")

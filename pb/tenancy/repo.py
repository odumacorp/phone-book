"""Tenant isolation in the data layer.

Tenant-owned tables are read and written only through TenantRepo, which adds `tenant_id = ?` to every
statement. Code can't forget the filter, and a test (tests/test_tenant_isolation.py) fails if any module
outside the allowlist queries a tenant table directly.
"""
import re

from pb.db import fetch_all, fetch_one, get_db, insert_id, sql

TENANT_TABLES = {
    "tenant_modules", "tenant_settings", "memberships", "departments", "member_groups", "group_members",
    "department_members", "ussd_code_allocations", "org_contacts", "notices", "reminders",
}
IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


class TenantRepo:
    def __init__(self, tenant_id):
        if not isinstance(tenant_id, int) or isinstance(tenant_id, bool) or tenant_id <= 0:
            raise ValueError(f"invalid tenant_id {tenant_id!r}")
        self.tenant_id = tenant_id

    @staticmethod
    def _table(table):
        if table not in TENANT_TABLES:
            raise ValueError(f"{table} is not a tenant table")
        return table

    @staticmethod
    def _columns(values):
        for column in values:
            if not IDENT.match(column) or column == "tenant_id":
                raise ValueError(f"bad column {column!r}")
        return list(values)

    def _where(self, where):
        return "WHERE tenant_id = ?" + (f" AND ({where})" if where else "")

    def all(self, table, where="", params=(), order="", columns="*", limit=None):
        query = f"SELECT {columns} FROM {self._table(table)} {self._where(where)}"
        if order:
            query += f" ORDER BY {order}"
        if limit:
            query += f" LIMIT {int(limit)}"
        return fetch_all(query, (self.tenant_id, *params))

    def one(self, table, where="", params=(), columns="*"):
        return fetch_one(f"SELECT {columns} FROM {self._table(table)} {self._where(where)}", (self.tenant_id, *params))

    def count(self, table, where="", params=()):
        return self.one(table, where, params, columns="COUNT(*) AS n")["n"]

    def insert(self, table, returning_id=True, **values):
        columns = self._columns(values)
        names = ", ".join(["tenant_id", *columns])
        marks = ", ".join("?" for _ in range(len(columns) + 1))
        query = f"INSERT INTO {self._table(table)} ({names}) VALUES ({marks})"
        params = (self.tenant_id, *values.values())
        if returning_id:
            return insert_id(query, params)
        db = get_db()
        db.execute(sql(query), params)
        db.commit()
        return None

    def upsert(self, table, key_columns, **values):
        """Insert, or update the non-key columns when a row with the same (tenant_id, key columns) exists."""
        columns = self._columns(values)
        updates = [c for c in columns if c not in key_columns] or columns[:1]
        names = ", ".join(["tenant_id", *columns])
        marks = ", ".join("?" for _ in range(len(columns) + 1))
        conflict = ", ".join(["tenant_id", *key_columns])
        sets = ", ".join(f"{c} = excluded.{c}" for c in updates)
        db = get_db()
        db.execute(
            sql(f"INSERT INTO {self._table(table)} ({names}) VALUES ({marks}) ON CONFLICT ({conflict}) DO UPDATE SET {sets}"),
            (self.tenant_id, *values.values()),
        )
        db.commit()

    def update(self, table, where, params=(), **values):
        if not where:
            raise ValueError("update needs a where clause")
        columns = self._columns(values)
        sets = ", ".join(f"{c} = ?" for c in columns)
        db = get_db()
        cur = db.execute(
            sql(f"UPDATE {self._table(table)} SET {sets} {self._where(where)}"),
            (*values.values(), self.tenant_id, *params),
        )
        db.commit()
        return cur.rowcount

    def delete(self, table, where, params=()):
        if not where:
            raise ValueError("delete needs a where clause")
        db = get_db()
        cur = db.execute(sql(f"DELETE FROM {self._table(table)} {self._where(where)}"), (self.tenant_id, *params))
        db.commit()
        return cur.rowcount

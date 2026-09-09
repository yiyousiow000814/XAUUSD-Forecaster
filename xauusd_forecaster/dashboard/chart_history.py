"""Exact derived chart rows: one producer, bounded incremental export."""
from __future__ import annotations

import json
import sqlite3

CONTRACT = "exact-chart-history-v1"


def install_chart_history(connection: sqlite3.Connection) -> None:
    connection.execute("""CREATE TABLE IF NOT EXISTS dashboard_chart_records_v1 (
        resource TEXT NOT NULL, record_key TEXT NOT NULL, sort_epoch INTEGER NOT NULL,
        payload_hash TEXT NOT NULL, payload TEXT NOT NULL, revision INTEGER NOT NULL,
        PRIMARY KEY(resource,record_key))""")
    connection.execute("""CREATE INDEX IF NOT EXISTS dashboard_chart_export_v1
        ON dashboard_chart_records_v1(revision,resource,record_key)""")
    connection.execute("""CREATE TABLE IF NOT EXISTS dashboard_chart_state_v1 (
        id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL,
        generated_at TEXT NOT NULL, record_count INTEGER NOT NULL)""")


def publish_chart_history(connection, records, revision, generated_at):
    """Participates in the existing read-model publication transaction."""
    if len({(row["resource"], row["record_key"]) for row in records}) != len(records):
        raise ValueError("Chart source contains duplicate record identities")
    install_chart_history(connection)
    connection.executemany("""INSERT INTO dashboard_chart_records_v1
        (resource,record_key,sort_epoch,payload_hash,payload,revision) VALUES (?,?,?,?,?,?)
        ON CONFLICT(resource,record_key) DO UPDATE SET sort_epoch=excluded.sort_epoch,
        payload_hash=excluded.payload_hash,payload=excluded.payload,revision=excluded.revision
        WHERE dashboard_chart_records_v1.payload_hash IS NOT excluded.payload_hash""",
        [(r["resource"],r["record_key"],r["sort_epoch"],r["payload_hash"],
          json.dumps(r["payload"],ensure_ascii=False,separators=(",",":")),revision)
         for r in records])
    connection.execute("""INSERT INTO dashboard_chart_state_v1 VALUES (1,?,?,?)
        ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,
        generated_at=excluded.generated_at,record_count=excluded.record_count""",
        (revision,generated_at,len(records)))


def chart_history_page(connection, cursor=None, limit=200):
    """Read at most one 60KB page without holding a snapshot during transport."""
    position = tuple(json.loads(cursor)) if cursor else (-1,"","")
    if (len(position)!=3 or not isinstance(position[0],int)
            or not all(isinstance(x,str) for x in position[1:])):
        raise ValueError("Invalid chart export cursor")
    connection.execute("BEGIN")
    try:
        state=connection.execute("SELECT revision,generated_at,record_count FROM dashboard_chart_state_v1 WHERE id=1").fetchone()
        if state is None: raise ValueError("Chart history has not been built")
        rows=connection.execute("""SELECT resource,record_key,sort_epoch,payload_hash,payload,revision
            FROM dashboard_chart_records_v1 WHERE (revision,resource,record_key)>(?,?,?)
            ORDER BY revision,resource,record_key LIMIT ?""",(*position,min(200,max(1,limit)))).fetchall()
        records=[]; size=0; last=position
        for resource,key,epoch,digest,payload,revision in rows:
            record=dict(resource=resource,record_key=key,sort_epoch=epoch,payload_hash=digest,payload=json.loads(payload))
            encoded=json.dumps(record,ensure_ascii=False,separators=(",",":")).encode()
            if len(encoded)>55000: raise ValueError("Chart record exceeds export bound")
            if size+len(encoded)>55000: break
            records.append(record);size+=len(encoded);last=(revision,resource,key)
        return dict(contract=CONTRACT,records=records,cursor=json.dumps(last,separators=(",",":")),
                    complete=not rows,source_revision=state[0],generated_at=state[1],record_count=state[2])
    finally:
        connection.rollback()

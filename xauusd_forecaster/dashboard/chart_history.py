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
        generated_at TEXT NOT NULL, record_count INTEGER NOT NULL, chart_format TEXT NOT NULL DEFAULT 'exact-v1')""")
    if "chart_format" not in {row[1] for row in connection.execute("PRAGMA table_info(dashboard_chart_state_v1)")}:
        connection.execute("ALTER TABLE dashboard_chart_state_v1 ADD COLUMN chart_format TEXT NOT NULL DEFAULT 'exact-v1'")


CHART_FORMAT = "pyramid-v2"


def _upsert_record(connection, row, revision, *, force=False):
    return connection.execute("""INSERT INTO dashboard_chart_records_v1
        (resource,record_key,sort_epoch,payload_hash,payload,revision) VALUES (?,?,?,?,?,?)
        ON CONFLICT(resource,record_key) DO UPDATE SET sort_epoch=excluded.sort_epoch,
        payload_hash=excluded.payload_hash,payload=excluded.payload,revision=excluded.revision
        WHERE dashboard_chart_records_v1.payload_hash IS NOT excluded.payload_hash OR ?
        RETURNING record_key""", (row["resource"],row["record_key"],row["sort_epoch"],
        row["payload_hash"],json.dumps(row["payload"],ensure_ascii=False,separators=(",",":")),
        revision,force)).fetchone() is not None


def publish_chart_history(connection, records, revision, generated_at):
    """Publish exact rows and only changed extrema blocks in the owner transaction."""
    from .resource_contracts import _learning_record
    if len({(row["resource"], row["record_key"]) for row in records}) != len(records):
        raise ValueError("Chart source contains duplicate record identities")
    install_chart_history(connection)
    previous = connection.execute("SELECT chart_format,revision FROM dashboard_chart_state_v1 WHERE id=1").fetchone()
    rebuild = previous is None or previous[0] != CHART_FORMAT
    # Source revisions can remain unchanged across format rebuilds. Export order
    # belongs to this publication owner, never to the source ledger clock.
    revision = max(revision, previous[1] + 1 if previous else 0)
    groups = {}
    for row in records:
        if row["resource"] in {"curve-5m", "curve-30m"}:
            groups.setdefault((row["resource"],row["payload"]["model_identity"]),[]).append(row)
        else:
            _upsert_record(connection,row,revision)
    for (resource,identity), source in groups.items():
        source.sort(key=lambda row:(row["sort_epoch"],row["record_key"]))
        points=[]; changed=[]
        for ordinal,row in enumerate(source):
            payload={**row["payload"],"chart_ordinal":ordinal,
                "chart_anchor":bool(row["payload"].get("model_version")
                    or row["payload"].get("source_gap_before")
                    or ordinal+1<len(source) and source[ordinal+1]["payload"].get("source_gap_before"))}
            normalized=_learning_record(resource,row["record_key"],row["sort_epoch"],payload)
            if _upsert_record(connection,normalized,revision,force=rebuild): changed.append(ordinal)
            points.append(payload)
        size=16
        while points:
            for bucket in sorted({ordinal//size for ordinal in changed}):
                chunk=points[bucket*size:(bucket+1)*size]
                chosen={0,len(chunk)-1,
                    min(range(len(chunk)),key=lambda n:chunk[n]["cumulative_quote_return"]),
                    max(range(len(chunk)),key=lambda n:chunk[n]["cumulative_quote_return"])}
                payload={"model_identity":identity,"block_size":size,
                    "first_ordinal":bucket*size,"source_point_count":len(chunk),
                    "points":[chunk[n] for n in sorted(chosen)]}
                block=_learning_record(resource.replace("curve-","curve-tile-"),
                    f"{identity}\0{size:016d}\0{bucket:016d}",source[bucket*size]["sort_epoch"],payload)
                _upsert_record(connection,block,revision,force=rebuild)
            if size>=len(points): break
            size*=4
    raw_count=connection.execute("SELECT count(*) FROM dashboard_chart_records_v1 WHERE resource NOT IN ('curve-tile-5m','curve-tile-30m')").fetchone()[0]
    if raw_count!=len(records): raise ValueError("Chart source must preserve previously published history")
    count=connection.execute("SELECT count(*) FROM dashboard_chart_records_v1").fetchone()[0]
    connection.execute("""INSERT INTO dashboard_chart_state_v1
        (id,revision,generated_at,record_count,chart_format) VALUES (1,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,
        generated_at=excluded.generated_at,record_count=excluded.record_count,
        chart_format=excluded.chart_format""",(revision,generated_at,count,CHART_FORMAT))


def chart_history_page(connection, cursor=None, limit=200):
    """Read at most one 60KB page without holding a snapshot during transport."""
    position = tuple(json.loads(cursor)) if cursor else (-1,"","")
    if (len(position)!=3 or not isinstance(position[0],int)
            or not all(isinstance(x,str) for x in position[1:])):
        raise ValueError("Invalid chart export cursor")
    connection.execute("BEGIN")
    try:
        state=connection.execute("SELECT revision,generated_at,record_count,chart_format FROM dashboard_chart_state_v1 WHERE id=1").fetchone()
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
                    complete=not rows,source_revision=state[0],generated_at=state[1],record_count=state[2],chart_format=state[3])
    finally:
        connection.rollback()

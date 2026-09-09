from datetime import datetime, UTC
import json
import sqlite3

from xauusd_forecaster.dashboard.chart_history import publish_chart_history, chart_history_page
from xauusd_forecaster.dashboard.resource_contracts import _learning_record


def test_exact_export_is_bounded_resumable_and_preserves_old_rows(tmp_path):
    connection=sqlite3.connect(tmp_path / "derived.sqlite3")
    rows=[_learning_record("curve-5m",str(i),i,{"decision_time":str(i),"model_identity":"FULL","v":i}) for i in range(501)]
    publish_chart_history(connection,rows,1,datetime.now(UTC).isoformat());connection.commit()
    first=chart_history_page(connection)
    assert len(first["records"])==200
    assert first==chart_history_page(connection)
    cursor=first["cursor"];seen=list(first["records"])
    # Updates behind the cursor acquire a later revision and are not lost.
    rows[0]=_learning_record("curve-5m","0",0,{"decision_time":"0","model_identity":"FULL","v":999})
    publish_chart_history(connection,rows,2,datetime.now(UTC).isoformat());connection.commit()
    while True:
        page=chart_history_page(connection,cursor)
        assert len(json.dumps(page).encode())<60000
        if page["complete"]:break
        assert page["cursor"]!=cursor
        seen.extend(page["records"]);cursor=page["cursor"]
    latest={row["record_key"]:row for row in seen}
    assert len(latest)==501
    assert latest["0"]["payload"]["v"]==999
    assert page["record_count"]==501
    connection.close()


def test_production_export_route_and_sync_url_resume_without_skipping(tmp_path, monkeypatch):
    import threading
    from tests.dashboard.test_dashboard_api import _dashboard_module
    from xauusd_forecaster.dashboard.sync import resources
    database = tmp_path / "derived.sqlite3"
    connection = sqlite3.connect(database)
    records = [_learning_record("curve-5m", str(i), i,
               {"decision_time": str(i), "model_identity": "FULL"}) for i in range(401)]
    publish_chart_history(connection, records, 1, datetime.now(UTC).isoformat())
    connection.commit(); connection.close()
    module = _dashboard_module()
    monkeypatch.setattr(module.Handler, "database", database, raising=False)
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    sent = {}
    completions = []
    def post(url, data, config):
        body = json.loads(data)
        if "records" in body:
            for row in body["records"]:
                sent[row["record_key"]] = row
            return {"accepted": len(body["records"])}
        completions.append(body["chart_completion"])
        return {"status": "OK"}
    monkeypatch.setattr(resources, "_post_json", post)
    config = {"local_status_url": f"http://127.0.0.1:{server.server_port}/api/status",
              "remote_ingest_url": "https://worker.example/api/ingest",
              "learning_history_state_file": str(tmp_path / "cursor.json"),
              resources.RUNTIME_STATE_ROOT_KEY: str(tmp_path)}
    try:
        for _ in range(4):
            resources._sync_learning_history({}, config)
        assert len(sent) == 401
        assert completions[0]["record_count"] == 401
        assert all(row["resource"] == "exact-curve-5m" for row in sent.values())
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_learning_owner_publishes_exact_rows_and_metrics_atomically(tmp_path, monkeypatch):
    from xauusd_forecaster.evidence.ledger import ForwardLedger
    from xauusd_forecaster.dashboard.read_models import DashboardReadModelOwner
    from xauusd_forecaster.dashboard import status_resources
    database = tmp_path / "forward.sqlite3"
    ledger = ForwardLedger(database)
    ledger.connection.close()
    source = {"learning_curves": {"identity_curves": [{"model_identity": "FULL",
        "points": [{"decision_time": f"2026-08-01T{i:02d}:00:00Z",
                    "cumulative_quote_return": i/100} for i in range(24)]}]}}
    monkeypatch.setattr(status_resources, "_dashboard_payload", lambda *a, **kw:
                        {**source, "generated_at": kw["clock"]().isoformat()})
    owner = DashboardReadModelOwner(database, {"learning": lambda snapshot:
        status_resources._optional_resource_payload(snapshot, "learning")})
    assert owner.refresh_resource("learning") == 1
    assert owner.refresh_resource("learning") == 0
    with sqlite3.connect(database) as connection:
        summary = json.loads(connection.execute(
            "SELECT payload_json FROM dashboard_optional_read_models_v1 WHERE resource='learning'").fetchone()[0])
        assert "_chart_records" not in summary
        assert summary["learning_curves"]["identity_curves"] == []
        page = chart_history_page(connection)
        assert len(page["records"]) == 24
        assert page["records"][0]["payload"]["decision_time"] == "2026-08-01T00:00:00Z"
    connection.close()

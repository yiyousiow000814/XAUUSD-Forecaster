"""A malformed record cannot erase subsequent executable quote evidence."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from xauusd_forecaster.market import JsonlMarketProvider


@pytest.mark.parametrize("compressed", [False, True])
@pytest.mark.parametrize("bad", [b"\n", b'{"recei{"schema":"quote"}\n', b'\xff\n'])
def test_recovery_preserves_bytes_and_observations(tmp_path, caplog, compressed, bad):
    now = datetime(2026, 9, 14, 4, 30, tzinfo=UTC)
    row = {"symbol": "XAUUSD", "event_time": now.isoformat(),
           "received_time": now.isoformat(), "bid": 2400, "ask": 2400.2}
    good = json.dumps(row).encode() + b"\n"
    raw = good + bad + json.dumps({**row, "bid": 2400.1}).encode() + b"\n"
    path = tmp_path / ("xauusd-quotes-20260914.jsonl" + (".gz" if compressed else ""))
    path.write_bytes(gzip.compress(raw) if compressed else raw)
    original = path.read_bytes()
    provider = JsonlMarketProvider(tmp_path)
    for _ in range(2):
        assert [v.bid for v in provider.observations(now)] == [2400, 2400.1]
    assert caplog.text.count("QUOTE_RECORD_MALFORMED") == 1
    assert f"offset={len(good)}" in caplog.text
    assert hashlib.sha256(bad).hexdigest() in caplog.text
    assert [v.bid for v in JsonlMarketProvider(tmp_path).observations(now)] == [2400, 2400.1]
    assert path.read_bytes() == original


def test_partial_tail_waits_for_delimiter_and_schema_errors_remain_fail_closed(tmp_path):
    now = datetime(2026, 9, 14, 4, 30, tzinfo=UTC)
    path = tmp_path / "xauusd-quotes-20260914.jsonl"
    path.write_bytes(b'{"partial":')
    provider = JsonlMarketProvider(tmp_path)
    assert provider.observations(now) == []
    row = {"symbol": "XAUUSD", "event_time": now.isoformat(),
           "received_time": now.isoformat(), "bid": 2400, "ask": 2400.2}
    with path.open("ab") as handle:
        handle.write(b"\n" + json.dumps(row).encode() + b"\n")
    assert len(provider.observations(now)) == 1
    with path.open("ab") as handle:
        handle.write(json.dumps({**row, "symbol": "EURUSD"}).encode() + b"\n")
    with pytest.raises(ValueError):
        provider.observations(now)


def test_real_csharp_writer_restarts_without_concatenating_records(tmp_path):
    dotnet = shutil.which("dotnet")
    if not dotnet:
        pytest.skip("real writer filesystem rehearsal requires a .NET SDK")
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    sdks = subprocess.run([dotnet, "--list-sdks"], capture_output=True, text=True, check=True, **flags).stdout
    versions = [int(line.split(".")[0]) for line in sdks.splitlines() if line[:1].isdigit()]
    if not versions:
        pytest.skip("no .NET SDK")
    root = Path(__file__).resolve().parents[2]
    source = (root / "ctrader/XauusdForwardQuoteBridge/XauusdForwardQuoteBridge.cs").read_text(encoding="utf-8")
    # Compile the exact production IO methods. cTrader lifecycle/market APIs
    # are outside this filesystem boundary and no trading runtime is launched.
    methods = source[source.index("        private void OpenDailyFile("):source.index("        private string EscapeJson(")]
    program = '''using System; using System.IO; using System.Text; using System.Globalization;
class Program {
 DateTime activeDateUtc; string activePath; StreamWriter writer;
 string SymbolName = "XAUUSD"; string OutputDirectory;
 static void Main(string[] args) {
  var p = new Program { OutputDirectory = args[0] };
  var date = new DateTime(2026,9,14);
  var path = Path.Combine(args[0], "xauusd-quotes-20260914.jsonl");
  foreach(var tail in new [] { "", "{\\"partial\\":", "{}", "{}\\n", "{}\\r\\n" }) {
   File.WriteAllText(path, tail, new UTF8Encoding(false));
   p.OpenDailyFile(date); p.writer.Write("{\\"valid\\":true}\\n"); p.writer.Dispose();
   string expected = tail + (tail.Length>0 && !tail.EndsWith("\\n") ? "\\n" : "") + "{\\"valid\\":true}\\n";
   if(File.ReadAllText(path) != expected) throw new Exception("restart framing changed source evidence");
  }
  Console.WriteLine("writer restart framing: 5 cases passed");
 }
''' + methods + "\n}"
    (tmp_path / "Program.cs").write_text(program, encoding="utf-8")
    (tmp_path / "Harness.csproj").write_text(
        f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType>'
        f'<TargetFramework>net{max(versions)}.0</TargetFramework></PropertyGroup></Project>', encoding="utf-8")
    (tmp_path / "NuGet.Config").write_text('<configuration><packageSources><clear /></packageSources></configuration>')
    result = subprocess.run([dotnet, "run", "--project", str(tmp_path / "Harness.csproj"), "--", str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=60, **flags)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "5 cases passed" in result.stdout

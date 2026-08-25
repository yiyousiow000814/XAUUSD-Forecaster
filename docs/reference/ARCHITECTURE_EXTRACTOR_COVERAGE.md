# Architecture Extractor Coverage Reference

| Surface | Automatic evidence | Deliberately unresolved |
|---|---|---|
| Python | packages, modules, imports, `__all__`, top-level class/function spans, main guards, known thread/process/executor/subprocess construction, SQLite connection sites, literal SQL operation/table | dynamic imports, computed SQL, indirect call graphs, runtime ownership |
| TypeScript/Web | ESM imports, page/API filesystem routes, literal D1 calls and statically visible dispatch literals | computed route tables, dynamic imports, runtime execution frequency |
| PowerShell | files, functions, dot-source and process-launch literals; `windows-evidence.json` records exact Windows parser spans separately | computed paths/actions on the neutral fallback; stale Windows digest never satisfies current evidence |
| cTrader C# | file, namespace, and class inventory | method call graph and dynamic runtime relationships |

`UNRESOLVED`, `FALLBACK`, and `BOUNDED` are evidence limits, not failures to be
silently promoted to exact static matches.


# Bundled tools — `mdb-export` (mdbtools)

Manager mode (Meet Manager `.mdb`) reads the database by shelling out to
**`mdb-export`** from [mdbtools](https://github.com/mdbtools/mdbtools). This is
the same tool the makosmeets server uses (`convert.mjs`, `dump_hy_tek_mdb.py`),
and crucially it reads the **raw Jet file**, so it ignores the Hy-Tek database
password that blocks the ACE/ODBC driver, and it reads both Meet Manager and
Team Manager databases.

This folder is bundled into `MakoSync.exe` (PyInstaller `--add-data
src\makosync\tools;makosync\tools`). At runtime `mdb_reader._resolve_mdb_export()`
looks for `mdb-export.exe` here first (then the `MAKOSYNC_MDB_EXPORT` env var,
then `PATH`).

## Populate it

On the build machine, run from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File build\fetch_mdbtools.ps1
```

That copies `mdb-export.exe` **and its dependent MinGW DLLs** here (via MSYS2).
Verify it runs standalone:

```powershell
& src\makosync\tools\mdb-export.exe --help
```

> The actual binaries are intentionally **not committed** (platform-specific,
> sizable). CI runs `fetch_mdbtools.ps1` before building. For local Dolphin-only
> work you don't need them; Manager mode will raise a clear error if `mdb-export`
> can't be found.

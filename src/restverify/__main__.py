"""``python -m restverify`` — the same entry point as the ``restverify`` script.

``cli._cron_binary`` falls back to ``<python> -m restverify`` when the console
script is not on PATH (the running-from-a-checkout case), so this module is the
load-bearing half of that contract: without it the printed cron line dies with
"No module named restverify.__main__" on every platform.
"""
from restverify.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

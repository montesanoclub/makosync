"""PyInstaller entry shim — turns ``from .watcher import …`` (relative) into
package-imports by routing through ``makosync.__main__:main``.

Run by PyInstaller in onefile mode as a top-level script, so relative imports
inside makosync.__main__ would otherwise fail with "no known parent
package". This file is the entry point passed to pyinstaller instead.
"""

from __future__ import annotations

import sys

from makosync.__main__ import main


if __name__ == "__main__":
    sys.exit(main())

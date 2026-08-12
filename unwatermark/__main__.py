"""Entry point so the CLI runs with no installation: python3 -m unwatermark"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

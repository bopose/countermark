"""Entry point so the CLI runs with no installation: python3 -m countermark"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

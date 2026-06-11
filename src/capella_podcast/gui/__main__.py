"""`python -m capella_podcast.gui` — launch the GUI without the console script."""

import sys

from ..cli import main

if __name__ == "__main__":
    main(["gui", *sys.argv[1:]])

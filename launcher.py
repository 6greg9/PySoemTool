"""Entry script for PyInstaller (the package's __main__ uses relative imports)."""
import sys

from pysoem_tool.__main__ import main

sys.exit(main())

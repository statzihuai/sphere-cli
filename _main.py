# PyInstaller entry point — must be a top-level file so the package's
# relative imports resolve correctly inside the frozen binary.
import multiprocessing
multiprocessing.freeze_support()  # must be first for PyInstaller + spawn

from sphere_cli.cli import main
main()

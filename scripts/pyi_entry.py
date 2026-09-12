"""PyInstaller entry point — deliberately OUTSIDE the spokengo package.

A frozen build runs its entry script as top-level ``__main__`` with no parent
package. Pointing PyInstaller at ``src/spokengo/__main__.py`` therefore made
every ``from .`` inside it fail with

    ImportError: attempted relative import with no known parent package

before the app could draw a single window — the built .exe never once started.
Importing the package from a script that lives outside it keeps ``spokengo`` a
real package, so its relative imports resolve normally.
"""
from spokengo.__main__ import gui_entry

if __name__ == "__main__":
    raise SystemExit(gui_entry())

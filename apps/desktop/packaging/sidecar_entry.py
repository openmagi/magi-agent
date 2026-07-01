"""PyInstaller entrypoint for the bundled `magi` serve runtime.

PyInstaller freezes THIS module as the program entry. It calls the serve
entrypoint (`magi_agent.main:main`, a plain argparse), which the desktop shell
invokes as `<bin> --host 127.0.0.1 --port <port>`. There is no `serve`
subcommand; this is the same `main:main` entry the `magi-agent` console script
uses, only frozen into a standalone onedir named `magi`.
"""

from magi_agent.main import main

if __name__ == "__main__":
    main()

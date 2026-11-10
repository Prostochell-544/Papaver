"""PyCharm Run entry point: forwards to the `papaver` Typer app.

Set the Run configuration's "Parameters" to e.g. `check 10.1016/S0140-6736(97)11096-0`.
"""

from papaver.cli import app

if __name__ == "__main__":
    app()

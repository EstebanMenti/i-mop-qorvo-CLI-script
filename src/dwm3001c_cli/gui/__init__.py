"""GUI de escritorio PySide6 (fase F9, rama ``hardware/ble-bridge-nrf52840``).

Capa de presentación alternativa a ``app`` (CLI Typer), en el mismo nivel de
capas: puede importar de ``core``, ``validation``, ``calibration`` y
``transport``, nunca de ``app``; nada por debajo la importa a ella.
"""

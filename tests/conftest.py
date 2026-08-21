"""Configuración compartida de pytest.

``QT_QPA_PLATFORM=offscreen`` antes de que se importe PySide6 en cualquier
test: permite correr los tests de la GUI (F9) sin una pantalla real, en CI o
en una sesión sin entorno gráfico.
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

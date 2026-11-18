import sys
from pathlib import Path

if not __package__ and not getattr(sys, "frozen", False):
    # Lanzado por ruta (el inicio con Windows, sin carpeta de trabajo fija):
    # la raíz de importación es la carpeta del proyecto, no la del paquete.
    # En el ejecutable empaquetado no se toca: sus rutas las fija PyInstaller.
    _here = Path(__file__).resolve().parent
    sys.path[:] = [p for p in sys.path if Path(p or ".").resolve() != _here]
    sys.path.insert(0, str(_here.parent))

from ytm_native.app import main  # noqa: E402 - después de fijar la raíz

sys.exit(main())

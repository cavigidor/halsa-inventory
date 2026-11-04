# services paketi — kök dizini (config.py, dashboard_builder.py) import yoluna ekler
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# M8-CP2: HALSA_DATA_ROOT (one mounted directory) fills DATA_FOLDER / UPLOAD_DIR / AGENT_DB / DASHBOARD_PATH before
# any service module reads them (services/paths.py; validated at app startup)
from services import paths as _paths  # noqa: E402
_paths.apply()

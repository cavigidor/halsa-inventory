# services paketi — kök dizini (config.py, dashboard_builder.py) import yoluna ekler
import os, sys
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

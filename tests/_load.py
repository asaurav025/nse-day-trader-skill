"""Load a script from a skill folder (the folder names have hyphens)."""

import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIX = os.path.join(ROOT, "tests", "fixtures")

# trader_common importable from any test; and the tests never read the user's own trader/config.json
# (its "holidays" override would change the calendar under them).
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.environ["TRADER_CONFIG"] = os.path.join(FIX, "no-such-config.json")


def load(folder, name):
    path = os.path.join(ROOT, folder, name + ".py")
    spec = importlib.util.spec_from_file_location("trader_" + name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

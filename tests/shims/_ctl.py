# Shared control/status for the simulated D1 Mini (tests only).
import json, os, threading
CTL = os.environ.get('SIM_CTL', 'control.json')
_lock = threading.Lock()
PINS = {}

def ctl():
    try:
        with open(CTL) as f:
            return json.load(f)
    except Exception:
        return {}

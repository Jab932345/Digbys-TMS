# Control/status files for the MicroPython simulation (tests only).
import json
import os

CTL = os.getenv('SIM_CTL')
STATUS = os.getenv('SIM_STATUS')
PINS = {}
DISPLAY = ['']
REV = {0x3F: '0', 0x06: '1', 0x5B: '2', 0x4F: '3', 0x66: '4', 0x6D: '5', 0x7D: '6', 0x07: '7',
       0x7F: '8', 0x6F: '9', 0x00: ' ', 0x40: '-', 0x77: 'A', 0x7C: 'b', 0x39: 'C', 0x58: 'c',
       0x5E: 'd', 0x79: 'E', 0x71: 'F', 0x74: 'h', 0x76: 'H', 0x10: 'i', 0x38: 'L', 0x54: 'n',
       0x5C: 'o', 0x73: 'P', 0x50: 'r', 0x78: 't', 0x3E: 'U', 0x1C: 'u', 0x6E: 'y', 0x08: '_', 0x63: '*'}


def ctl():
    try:
        with open(CTL) as f:
            return json.load(f)
    except Exception:
        return {}


def seg_text(segs):
    out = ''
    for s in segs:
        out += REV.get(s & 0x7F, '?') + ('.' if s & 0x80 else '')
    return out


def write_status(final=False):
    import config
    relay = PINS.get(config.PIN_RELAY)
    on = None if relay is None else (relay.value() == (0 if config.RELAY_ACTIVE_LOW else 1))
    with open(STATUS + '.tmp', 'w') as f:
        json.dump({'siren': on, 'display': DISPLAY[0], 'final': final}, f)
    os.rename(STATUS + '.tmp', STATUS)

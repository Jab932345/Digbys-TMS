#!/usr/bin/env python3
"""Precompile the app modules to .mpy (the ESP8266 has too little memory to
compile them itself). Output goes to firmware/build/.

    python3 tools/build_mpy.py          # prints the output folder

Needs mpy-cross 1.29.0 to match the MicroPython on the boards:
    pip3 install "mpy-cross==1.29.0.*"
"""
import os
import py_compile
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FW = os.path.join(ROOT, 'firmware')
OUT = os.path.join(FW, 'build')
# Updated over the air. ota is compiled too but only ever installed by USB.
APP = ['node', 'link', 'net', 'disp', 'probe']
USB_ONLY = ['ota']
WANT = 'v1.29.0'


def mpy_cross():
    exe = os.environ.get('MPY_CROSS') or shutil.which('mpy-cross')
    if not exe:
        sys.exit('mpy-cross not found: pip3 install "mpy-cross==1.29.0.*"')
    v = subprocess.run([exe, '--version'], capture_output=True, text=True).stdout.strip()
    if WANT not in v or 'mpy v6' not in v:
        sys.exit('mpy-cross is "%s"; need %s (pip3 install "mpy-cross==1.29.0.*")' % (v, WANT))
    return exe


def build():
    exe = mpy_cross()
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    for m in APP + USB_ONLY:
        src = os.path.join(FW, m + '.py')
        py_compile.compile(src, doraise=True)
        r = subprocess.run([exe, '-o', os.path.join(OUT, m + '.mpy'), src], capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit('mpy-cross failed on %s:\n%s' % (m, r.stderr))
    return OUT


if __name__ == '__main__':
    print(build())

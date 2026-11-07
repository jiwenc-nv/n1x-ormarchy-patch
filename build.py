#!/usr/bin/env python3
"""Compatibility entry point: build the registered patch set."""
import os
from pathlib import Path
import sys

os.execv(sys.executable, [sys.executable, str(Path(__file__).with_name('patchctl.py')),
                         'build', *sys.argv[1:]])

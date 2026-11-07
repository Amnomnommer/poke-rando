"""Host-prerequisite checks, aggregated so every missing item is reported before any work starts."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

ARM_TOOLS = ("arm-none-eabi-gcc", "arm-none-eabi-g++", "arm-none-eabi-as", "arm-none-eabi-ld",
             "arm-none-eabi-objcopy", "arm-none-eabi-nm", "arm-none-eabi-readelf")
PYTHON_MODULES = {"PIL": "Pillow", "ndspy": "ndspy", "yaml": "PyYAML"}


@dataclass(frozen=True)
class Missing:
    item: str
    hint: str


def check_w2u_host(python: str = sys.executable, java: str | None = None) -> list[Missing]:
    missing: list[Missing] = []
    if sys.version_info < (3, 11):
        missing.append(Missing("Python 3.11+", "install Python 3.11 or newer"))
    for tool in ("git", "ninja", *ARM_TOOLS):
        if shutil.which(tool) is None:
            hint = "install the Arm GNU toolchain (arm-none-eabi)" if tool.startswith("arm-") else f"install {tool}"
            missing.append(Missing(tool, hint))
    java = java or os.environ.get("JAVA") or shutil.which("java")
    if not java or subprocess.run([java, "-version"], capture_output=True).returncode != 0:
        missing.append(Missing("Java (JDK)", "install a JDK and put java on PATH, or set JAVA=/path/to/java"))
    for module, package in PYTHON_MODULES.items():
        probe = subprocess.run([python, "-c", f"import {module}"], capture_output=True)
        if probe.returncode != 0:
            missing.append(Missing(f"Python module {module}", f"python3 -m pip install {package}"))
    return missing

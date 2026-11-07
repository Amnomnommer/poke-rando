"""melonDS launch, boot-log smoke capture, and optional GDB fault capture (local only).

Automated smoke results are boot/log evidence only. They never count as
gameplay or runtime acceptance, which Amelie performs manually.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

FLATPAK_ID = "net.kuribo64.melonDS"
FAULT_PATTERNS = (
    re.compile(r"data abort", re.I),
    re.compile(r"prefetch abort", re.I),
    re.compile(r"undefined instruction", re.I),
    re.compile(r"ARM9[^\n]*abort", re.I),
)
GDB_PORT = 3333


def emulator_command(override: str | None = None) -> list[str] | None:
    if override:
        return override.split()
    if shutil.which("flatpak"):
        probe = subprocess.run(["flatpak", "info", FLATPAK_ID], capture_output=True)
        if probe.returncode == 0:
            return ["flatpak", "run", FLATPAK_ID]
    for name in ("melonDS", "melonds"):
        if shutil.which(name):
            return [name]
    return None


def scan_log(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        if any(p.search(line) for p in FAULT_PATTERNS):
            hits.append(line.strip())
    return hits


@dataclass
class SmokeResult:
    status: str  # "no-fault-observed", "fault", "emulator-exited"
    seconds: float
    faults: list[str]
    log: Path

    def summary(self) -> str:
        note = "automated boot-log smoke only; NOT gameplay or runtime acceptance"
        return f"smoke: {self.status} after {self.seconds:.0f}s ({len(self.faults)} fault lines) — {note}; log: {self.log}"


def gdb_script(port: int = GDB_PORT, breakpoints: tuple[int, ...] = ()) -> str:
    lines = ["set pagination off", "set confirm off", "set architecture armv5te", f"target remote localhost:{port}"]
    for address in breakpoints:
        lines += [f"break *0x{address:08x}", "commands", "  silent", f"  echo \\n=== breakpoint 0x{address:08x} ===\\n",
                  "  info registers", "  bt 8", "  continue", "end"]
    return "\n".join(lines + [
        "continue",
        "echo \\n=== stopped ===\\n",
        "info registers",
        "x/12i $pc",
        "bt 16",
        "detach",
        "quit",
    ]) + "\n"


def launch(rom: Path, log_dir: Path, *, smoke_seconds: int | None = None, gdb: bool = False,
           emulator: str | None = None, breakpoints: tuple[int, ...] = ()) -> SmokeResult | int:
    rom = Path(rom).resolve()
    if not rom.is_file():
        raise FileNotFoundError(rom)
    command = emulator_command(emulator)
    if command is None:
        raise RuntimeError(f"melonDS not found: install the {FLATPAK_ID} Flatpak or pass --emulator")
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log = log_dir / f"{rom.stem}-{stamp}.log"
    with log.open("wb") as handle:
        process = subprocess.Popen([*command, str(rom)], stdout=handle, stderr=subprocess.STDOUT)
        gdb_process = None
        if gdb:
            script = log_dir / f"{rom.stem}-{stamp}.gdb"
            script.write_text(gdb_script(breakpoints=breakpoints), encoding="utf-8")
            if shutil.which("gdb-multiarch") is None:
                handle.write(b"[launcher] gdb-multiarch not found; GDB capture skipped\n")
            else:
                time.sleep(3)
                gdb_log = log_dir / f"{rom.stem}-{stamp}.gdb.log"
                gdb_process = subprocess.Popen(["gdb-multiarch", "-q", "-batch", "-x", str(script)],
                                               stdout=gdb_log.open("wb"), stderr=subprocess.STDOUT)
        if smoke_seconds is None:
            code = process.wait()
            if gdb_process:
                gdb_process.wait()
            return code
        started = time.monotonic()
        status = "no-fault-observed"
        while time.monotonic() - started < smoke_seconds:
            if process.poll() is not None:
                status = "emulator-exited"
                break
            time.sleep(0.5)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if gdb_process and gdb_process.poll() is None:
            gdb_process.terminate()
    faults = scan_log(log.read_text(encoding="utf-8", errors="replace"))
    if faults:
        status = "fault"
    return SmokeResult(status, time.monotonic() - started, faults, log)

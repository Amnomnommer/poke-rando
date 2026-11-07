"""Read the Generation V script-command description from the pinned local CTRMap jar at build time.

The description is never copied into this repository; it is parsed in memory
from the user's verified W2U checkout (``tools/CTRMap/CTRMapV-dirty.jar`` is
SHA-256 pinned in pins/upstream.json).
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from ..gamedata import scripts
from . import BuildError

JAR = "tools/CTRMap/CTRMapV-dirty.jar"
LIB_MEMBER = "ctrmap/resources/scripting/cm_ide/sdk/EV_GEN_V/SDK5-B2W2-Generated.lib"
BASE_MEMBER = "yml/Base.yml"


def load_table(w2u_root: Path) -> dict[int, scripts.Opcode]:
    jar = Path(w2u_root) / JAR
    try:
        with zipfile.ZipFile(jar) as outer:
            inner_bytes = outer.read(LIB_MEMBER)
        with zipfile.ZipFile(io.BytesIO(inner_bytes)) as inner:
            text = inner.read(BASE_MEMBER).decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile) as error:
        raise BuildError(f"cannot read the script-command description from the CTRMap jar: {error}") from None
    table = scripts.load_opcode_table(text)
    if len(table) < 100:
        raise BuildError(f"script-command description is implausibly small ({len(table)} commands)")
    return table

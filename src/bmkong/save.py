"""Statistics of a Black Myth: Wukong save (ArchiveSaveFile.N.sav).

The archive has no schema in the save or the game exe; field meanings were worked out by comparing 29
timestamped saves of one playthrough (see AGENTS.md). Paths are protobuf field numbers from the
decrypted payload (container.py):
  1.1.1.4                         player level (read only)
  1.1.2.1{1:1}.2                  Will (灵韵): the currency entry with type 1
  6.1.5.1                         total deaths, always the sum of the per-chapter counts
  6.1.5.2{1:<chapter>}.2          deaths per chapter (chapter ids 10, 20 ... 60)
  1.2.1.1.2, 1.2.1.2.2            play time in seconds (float32, the live counter)
  6.2.2.1, 6.2.2.2                play time in seconds (int, checkpoint copies)
Writing recomputes the header MD5 the game verifies on load (container.py).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import protobuf as pb
from .container import Archive, SaveError

APP_DIR = Path(os.environ.get("BMKONG_HOME") or Path(os.environ.get("LOCALAPPDATA", Path.home())) / "BMKong")
GAME_EXE = "b1-Win64-Shipping.exe"
INT_MAX = 2_147_483_647
TIME_MAX = 100 * 365 * 86400  # play time is stored as float32 seconds

LEVEL = (1, 1, 1, 4)
WILL = (1, 1, 2, (1, {1: 1}), 2)
WILL_PARENT = (1, 1, 2)
DEATHS = (6, 1, 5)
TIME_FLOAT = ((1, 2, 1, 1, 2), (1, 2, 1, 2, 2))
TIME_INT = ((6, 2, 2, 1), (6, 2, 2, 2))
CHAPTERS = {
    10: ("第一回 火照黑云", "Chapter 1"),
    20: ("第二回 风起黄昏", "Chapter 2"),
    30: ("第三回 夜生白露", "Chapter 3"),
    40: ("第四回 曲度紫鸳", "Chapter 4"),
    50: ("第五回 日落红尘", "Chapter 5"),
    60: ("第六回 未竟", "Chapter 6"),
}
LABELS = {"will": ("灵韵", "Will"), "deaths": ("死亡次数", "Deaths"), "play_time": ("游戏时长", "Play time")}


def find_saves() -> list[Path]:
    """Look for saves in the Steam libraries listed in libraryfolders.vdf."""
    roots = set()
    for steam in (Path(r"C:\Program Files (x86)\Steam"), Path(r"C:\Program Files\Steam")):
        vdf = steam / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            for line in vdf.read_text(encoding="utf-8", errors="ignore").splitlines():
                if '"path"' in line:
                    roots.add(Path(line.split('"')[3].replace("\\\\", "\\")))
    for drive in "CDEFGH":
        for guess in (rf"{drive}:\SteamLibrary", rf"{drive}:\Steam", rf"{drive}:\G\Steam"):
            roots.add(Path(guess))
    found = []
    for root in roots:
        base = root / "steamapps" / "common" / "BlackMythWukong" / "b1" / "Saved" / "SaveGames"
        if base.is_dir():
            found.extend(sorted(base.glob("*/ArchiveSaveFile.*.sav")))
    return found


def game_running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {GAME_EXE}", "/NH"], capture_output=True,
                             text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except OSError:
        return False
    return GAME_EXE.lower() in out.lower()


def _int(value, field: str, who: tuple[str, str] | None = None, top: int = INT_MAX) -> int:
    """who = (zh, en) name of the chapter the value belongs to."""
    zh, en = LABELS[field]
    if who:
        zh, en = f"{who[0]} {zh}", f"{who[1]}: {en.lower()}"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value):
        raise SaveError(f"{zh} 必须是整数。", f"{en} must be a whole number.")
    value = int(value)
    if not 0 <= value <= top:
        raise SaveError(f"{zh} 超出范围。", f"{en} is out of range.")
    return value


def chapter_name(cid: int) -> tuple[str, str]:
    return CHAPTERS.get(cid) or (f"章节 {cid}", f"Chapter id {cid}")


class SaveFile:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.source = self.path.read_bytes()
        self.archive = Archive(self.source)
        self.data = self.archive.data
        if pb.get(self.data, DEATHS) is None or pb.get(self.data, WILL_PARENT) is None:
            raise SaveError("存档中找不到统计数据，可能来自不支持的游戏版本。",
                            "No statistics found in the save; it may come from an unsupported game version.")

    def chapters(self, data: bytes | None = None) -> dict[int, int]:
        data = self.data if data is None else data
        out = {}
        for f in pb.parse(pb.get(data, DEATHS).value):
            if f.number == 2 and f.wire == pb.LEN:
                entry = pb.ints(pb.parse(f.value))
                out[entry.get(1, 0)] = entry.get(2, 0)
        return out

    def values(self, data: bytes | None = None) -> dict:
        data = self.data if data is None else data
        return {
            "will": pb.get_int(data, WILL) or 0,
            "deaths": pb.get_int(data, DEATHS + (1,)) or 0,
            "chapters": self.chapters(data),
            "play_time": pb.get_float(data, TIME_FLOAT[0]),
        }

    def summary(self) -> dict:
        v = self.values()
        cids = sorted(set(CHAPTERS) | set(v["chapters"]))
        saved = self.archive.head(5)
        return {
            "path": str(self.path),
            "sha256": hashlib.sha256(self.source).hexdigest(),
            "slot": self.path.stem.rsplit(".", 1)[-1],
            "level": pb.get_int(self.data, LEVEL),
            "saved_at": dt.datetime.fromtimestamp(saved).strftime("%Y-%m-%d %H:%M") if saved else "",
            "save_count": self.archive.head(4),
            "steam_id": self.archive.head(12),
            "will": v["will"],
            "deaths": v["deaths"],
            "chapters": [{"id": c, "zh": chapter_name(c)[0], "en": chapter_name(c)[1],
                          "deaths": v["chapters"].get(c, 0), "present": c in v["chapters"]} for c in cids],
            "play_time": v["play_time"],
            "has_time": v["play_time"] is not None,
            "checksum_ok": self.archive.md5_ok,
        }

    def build(self, changes: dict) -> bytes:
        """changes = {"will": int, "chapters": {chapter id: deaths}, "play_time": seconds} (all optional).

        With no changes, a save whose checksum doesn't match is rewritten with a correct one (repair)."""
        data = self.data
        touched = not self.archive.md5_ok
        if "will" in changes:
            will = _int(changes["will"], "will")
            if pb.get(data, WILL[:-1]) is None:  # no Will entry yet: add {1: 1, 2: will}
                data = pb.append(data, WILL_PARENT, 1, pb.encode(1, pb.VARINT, 1) + pb.encode(2, pb.VARINT, will))
            else:
                data = pb.set_int(data, WILL, will)
            touched = True

        if changes.get("chapters"):
            current = self.chapters(data)
            for key, value in changes["chapters"].items():
                cid = int(key)
                if cid not in current and cid not in CHAPTERS:
                    raise SaveError(f"未知章节 {cid}。", f"Unknown chapter {cid}.")
                n = _int(value, "deaths", chapter_name(cid))
                if cid in current:
                    data = pb.set_int(data, DEATHS + ((2, {1: cid}), 2), n)
                elif n:
                    data = pb.append(data, DEATHS, 2, pb.encode(1, pb.VARINT, cid) + pb.encode(2, pb.VARINT, n))
                current[cid] = n
            total = sum(current.values())
            if total > INT_MAX:
                raise SaveError("死亡次数总和超出范围。", "Total deaths is out of range.")
            data = pb.set_int(data, DEATHS + (1,), total)
            touched = True

        if "play_time" in changes:
            secs = _int(changes["play_time"], "play_time", top=TIME_MAX)
            old = pb.get_float(data, TIME_FLOAT[0])
            if old is None:
                raise SaveError("存档中没有游戏时长。", "The save has no play time.")
            delta = secs - old
            for path in TIME_FLOAT:
                if pb.get(data, path[:-1]) is not None:
                    data = pb.set_float(data, path, max(0.0, pb.get_float(data, path) + delta))
            # the int copies lag the live counter; shift them by the same amount
            for path in TIME_INT:
                if pb.get(data, path[:-1]) is not None:
                    data = pb.set_int(data, path, max(0, round(pb.get_int(data, path) + delta)))
            touched = True

        if not touched:
            raise SaveError("没有需要保存的修改。", "Nothing to save.")
        return self.archive.encode(data)

    def write(self, changes: dict) -> dict:
        if game_running():
            raise SaveError("游戏正在运行，请先退出游戏再保存。", "The game is running. Quit it before saving.")
        if self.path.read_bytes() != self.source:
            raise SaveError("存档在打开后被其他程序修改过，请重新打开。", "The save changed on disk after it was opened. Reopen it.")
        data = self.build(changes)
        if not Archive(data).md5_ok:  # the written file must decode and verify before it replaces anything
            raise SaveError("写入校验失败。", "The written file failed verification.")
        backup = self.backup()
        fd, tmp = tempfile.mkstemp(prefix=".bmkong-", suffix=".sav", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
        return {"backup": str(backup), "size": len(data)}

    def backup(self) -> Path:
        folder = APP_DIR / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        target = folder / f"{stamp}-{self.path.name}"
        shutil.copy2(self.path, target)
        return target

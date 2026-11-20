"""Exclusive locks on open files, shared between processes.

macOS and Linux use fcntl.flock, exactly as before. Windows has no flock; there msvcrt.locking locks one byte
far past the end of the file. Windows locks are mandatory (a locked byte can't be read by anyone else), so a lock
on real data would make every other reader fail while it is held. A byte no one reads or writes still makes the
writers take turns, and leaves the readers alone.
"""

from __future__ import annotations

import errno
import os
from typing import IO

WINDOWS = os.name == "nt"
if WINDOWS:
    import msvcrt
else:
    import fcntl

# Where the Windows lock sits: past the end of any file these locks guard (all far smaller than 2 GiB), and
# below 2 GiB so a C runtime with 32-bit file offsets can still lock it.
FAR = 0x7FFFFFFE
# What msvcrt.locking raises when the byte stays locked through its ten one-second tries.
BUSY = {errno.EACCES, getattr(errno, "EDEADLOCK", errno.EDEADLK), errno.EDEADLK}


def _far_byte(fh: IO, mode: int) -> None:
    """Lock or unlock the far byte of fh's file, putting the file position back afterwards."""
    fd = fh.fileno()
    here = os.lseek(fd, 0, os.SEEK_CUR)
    os.lseek(fd, FAR, os.SEEK_SET)
    try:
        msvcrt.locking(fd, mode, 1)
    finally:
        os.lseek(fd, here, os.SEEK_SET)


def lock(fh: IO) -> None:
    """Wait until this process holds the exclusive lock on the open file fh."""
    if not WINDOWS:
        fcntl.flock(fh, fcntl.LOCK_EX)
        return
    while True:
        try:
            _far_byte(fh, msvcrt.LK_LOCK)
            return
        except OSError as exc:  # still held by another process after ten seconds: keep waiting, as flock does
            if exc.errno not in BUSY:
                raise


def try_lock(fh: IO) -> None:
    """Take the exclusive lock on fh now, or raise BlockingIOError when another process holds it."""
    if not WINDOWS:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return
    try:
        _far_byte(fh, msvcrt.LK_NBLCK)
    except OSError as exc:
        if exc.errno in BUSY:
            raise BlockingIOError(exc.errno, f"{fh.name} is locked by another process") from None
        raise


def unlock(fh: IO) -> None:
    """Release the lock taken with lock() or try_lock()."""
    if not WINDOWS:
        fcntl.flock(fh, fcntl.LOCK_UN)
        return
    _far_byte(fh, msvcrt.LK_UNLCK)

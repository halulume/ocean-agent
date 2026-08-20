# -*- coding: utf-8 -*-
"""Print stdin to the screen and append it to a file, in UTF-8.

The console window is where the operator watches the bot, and the file is
what remains when that window is gone. PowerShell's Tee-Object cannot do
the second half here: on Windows PowerShell 5.1 it writes UTF-16 and offers
no encoding switch, so every Korean line lands as mojibake.

Each line is flushed on arrival. A log that stops one line short of the
reason is the same as no log at all.
"""
import io
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print("사용법: python tee.py <로그파일>", file=sys.stderr)
        return 2
    path = sys.argv[1]
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8",
                             errors="replace")
    out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                           errors="replace")
    with open(path, "a", encoding="utf-8", errors="replace") as f:
        for line in stdin:
            out.write(line)
            out.flush()
            f.write(line)
            f.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

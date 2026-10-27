"""Align `=` in Terraform files the way `terraform fmt` does (hclwrite's formatCells), for machines without the
terraform binary. CI still runs the real `terraform fmt -check`; this keeps local edits close to it.

Rule: consecutive lines that assign a complete single-line value form a chain; a line whose value opens more
brackets than it closes (a multi-line value) is not part of a chain and ends it, as does any line without
an assignment. In a chain every `=` sits one space after the longest left-hand side.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ASSIGN = re.compile(r'^(\s*)("[^"]*"|[A-Za-z_][A-Za-z0-9_\-]*)(\s*)=(?!=)(.*)$')


def _net_brackets(rhs: str) -> int:
    n = 0
    for ch in rhs.split(" #")[0]:
        if ch in "([{":
            n += 1
        elif ch in ")]}":
            n -= 1
    return n


def align(text: str) -> str:
    lines = text.split("\n")
    chain: list[int] = []
    out = list(lines)

    def close() -> None:
        if not chain:
            return
        width = max(len(ASSIGN.match(lines[i]).group(1)) + len(ASSIGN.match(lines[i]).group(2)) for i in chain)  # type: ignore[union-attr]
        for i in chain:
            m = ASSIGN.match(lines[i])
            assert m
            indent, lhs, _sp, rhs = m.groups()
            out[i] = f"{indent}{lhs}{' ' * (width - len(indent) - len(lhs) + 1)}={rhs}"
        chain.clear()

    for i, line in enumerate(lines):
        m = ASSIGN.match(line)
        if m and _net_brackets(m.group(4)) == 0 and not line.strip().startswith("#"):
            chain.append(i)
        else:
            close()
    close()
    return "\n".join(out)


def main(paths: list[str], check: bool) -> int:
    bad = 0
    for p in paths:
        for f in sorted(Path(p).rglob("*.tf")) if Path(p).is_dir() else [Path(p)]:
            text = f.read_text()
            new = align(text)
            if new != text:
                bad += 1
                if check:
                    print(f"would reformat {f}")
                else:
                    f.write_text(new)
                    print(f"aligned {f}")
    return 1 if (check and bad) else 0


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--check"]
    sys.exit(main(args or ["infra/terraform"], "--check" in sys.argv))

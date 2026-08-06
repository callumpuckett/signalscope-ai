"""Fail when requirement files contain unpinned or duplicate dependencies."""

from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "requirements.in",
    "requirements.txt",
    "requirements-dev.in",
    "requirements-dev.txt",
)
PIN = re.compile(r"^[A-Za-z0-9_.-]+(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+$")


def canonical_name(line):
    return re.split(r"\[|==", line, maxsplit=1)[0].lower().replace("_", "-")


def main():
    failed = False
    direct_pins = set()
    production_pins = set()
    development_direct_pins = set()
    development_pins = set()
    for filename in FILES:
        seen = set()
        for number, raw_line in enumerate(
            (ROOT / filename).read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if filename == "requirements-dev.in" and line == "-r requirements.txt":
                continue
            if not PIN.fullmatch(line):
                print(f"{filename}:{number}: dependency is not exactly pinned", file=sys.stderr)
                failed = True
                continue
            name = canonical_name(line)
            if name in seen:
                print(f"{filename}:{number}: duplicate dependency {name}", file=sys.stderr)
                failed = True
            seen.add(name)
        if filename == "requirements.in":
            direct_pins = seen
        elif filename == "requirements.txt":
            production_pins = seen
        elif filename == "requirements-dev.in":
            development_direct_pins = seen
        elif filename == "requirements-dev.txt":
            development_pins = seen

    missing = direct_pins - production_pins
    if missing:
        print(
            "requirements.txt is missing direct pins: " + ", ".join(sorted(missing)),
            file=sys.stderr,
        )
        failed = True
    missing_dev = development_direct_pins - development_pins
    if missing_dev:
        print(
            "requirements-dev.txt is missing direct pins: "
            + ", ".join(sorted(missing_dev)),
            file=sys.stderr,
        )
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

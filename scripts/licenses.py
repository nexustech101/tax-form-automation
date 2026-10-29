"""licenses.py

Print every installed package with its version and declared license, to
refresh THIRD_PARTY_LICENSES.md after a dependency change.

*Usage:*
    uv run python -m scripts.licenses
"""

from importlib.metadata import distributions


def main() -> None:
    for d in sorted(distributions(), key=lambda d: (d.metadata["Name"] or "").lower()):
        m = d.metadata
        declared = m.get("License-Expression") or (m.get("License") or "").strip().splitlines()
        if isinstance(declared, list):
            declared = declared[0][:60] if declared else ""
        classifiers = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
        print(f"{m['Name']:34} {d.version:14} {declared or ', '.join(classifiers) or '-'}")


if __name__ == "__main__":
    main()

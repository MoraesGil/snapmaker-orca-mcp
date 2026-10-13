"""Check public release contents without requiring a native engine or private files."""
from pathlib import Path
import json
import tarfile
import zipfile


def main():
    dist = Path("dist")
    wheel = next(dist.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        schema = json.loads(archive.read("snapmaker_slicer_mcp/data/settings.json"))
        assert len(schema) - 1 == 699
        assert "snapmaker_slicer_mcp/THIRD_PARTY.md" in names
        assert any("LICENSE" in name for name in names)
        assert not any(name.lower().endswith((".3mf", ".gcode", ".env", ".appimage")) for name in names)
        assert not any("/runtime/" in name or "/artifacts/" in name or "/secrets/" in name for name in names)
    source = next(dist.glob("*.tar.gz"))
    with tarfile.open(source) as archive:
        names = archive.getnames()
        assert not any(name.lower().endswith((".3mf", ".gcode", ".env", ".appimage")) for name in names)
        assert not any("/.temp/" in name or "/runtime/" in name or "/artifacts/" in name or "/secrets/" in name for name in names)
    print("Wheel and source checked: 699 settings, license and attribution; no models, G-code, runtime or secrets.")


if __name__ == "__main__":
    main()

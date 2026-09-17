"""Build a native wheel and standalone binary archive for the host platform."""

import hashlib
import io
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "dist/q-lint-rs"


def main() -> None:
    # A dedicated wheel directory avoids accidentally packaging a stale wheel
    # for another platform/version that happens to be in the output directory.
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        subprocess.run(
            ["uv", "build", "--package", "q-lint-rs", "--wheel", "--out-dir", temporary],
            cwd=ROOT,
            check=True,
        )
        [wheel] = Path(temporary).glob("*.whl")
        with zipfile.ZipFile(wheel) as package:
            [entry] = [
                n
                for n in package.namelist()
                if ".data/scripts/" in n and Path(n).name in {"qlinter", "qlinter.exe"}
            ]
            binary = package.read(entry)
        name = Path(entry).name
        platform = wheel.stem.rsplit("-", 1)[1]
        version = wheel.name.split("-")[1]
        executable = OUTPUT / platform / name
        executable.parent.mkdir(exist_ok=True)
        executable.write_bytes(binary)
        executable.chmod(0o755)
        copied_wheel = OUTPUT / wheel.name
        copied_wheel.write_bytes(wheel.read_bytes())
        stem = f"qlinter-{version}-{platform}"
        if name.endswith(".exe"):
            archive = OUTPUT / f"{stem}.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                bundle.write(executable, name)
        else:
            archive = OUTPUT / f"{stem}.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                info = tarfile.TarInfo(name)
                info.size = len(binary)
                info.mode = 0o755
                bundle.addfile(info, io.BytesIO(binary))
        checksum = OUTPUT / f"{stem}.sha256"
        checksum.write_text(
            "".join(
                f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                for p in (copied_wheel, archive)
            )
        )
        for path in (copied_wheel, executable, archive, checksum):
            print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()

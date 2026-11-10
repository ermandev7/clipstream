import zipfile

from app.core import pylib


def test_extract_wheel_moves_data_purelib_to_root(tmp_path):
    whl = tmp_path / "pkg-1.0-py3-none-any.whl"
    with zipfile.ZipFile(whl, "w") as z:
        z.writestr("pkg/__init__.py", "X = 1\n")
        z.writestr("pkg-1.0.dist-info/METADATA", "Name: pkg\nVersion: 1.0\n")
        z.writestr("pkg-1.0.data/purelib/extra_mod.py", "Y = 2\n")
        z.writestr("pkg-1.0.data/scripts/tool.exe", "binario")
    dest = tmp_path / "pylib"
    pylib._extract(whl, dest)
    assert (dest / "pkg" / "__init__.py").exists() and (dest / "extra_mod.py").exists()
    assert (dest / "pkg-1.0.dist-info" / "METADATA").exists() and not list(dest.rglob("tool.exe"))


def test_manifest_lists_torch_and_no_shared_packages():
    m = pylib.manifest()
    names = {w["name"].lower() for w in m["wheels"]}
    assert {"torch", "diffusers", "transformers"} <= names
    assert not names & {"numpy", "pillow", "huggingface-hub", "tokenizers"}  # esos van dentro del .exe
    assert all(w["sha256"] for w in m["wheels"] if w["name"].lower() != "torch")

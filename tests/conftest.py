import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "packs"


@pytest.fixture(scope="session")
def packs_root(tmp_path_factory):
    """A throwaway packs/ dir holding the fleet fixture, generated once per session."""
    import os

    root = tmp_path_factory.mktemp("packs")
    shutil.copytree(FIXTURES / "fleet-example", root / "fleet-example")
    os.environ["CHURNWATCH_PACKS_DIR"] = str(root)
    from churnwatch.generate import generate
    from churnwatch.pack import load_pack

    generate(load_pack("fleet-example"))
    return root


@pytest.fixture(scope="session")
def pack(packs_root):
    from churnwatch.pack import load_pack

    return load_pack("fleet-example")

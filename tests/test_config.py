from weki.config import find_zim, load


def write_config(root, body=""):
    (root / "data").mkdir()
    (root / "config.toml").write_text(body)


def test_defaults_and_relative_paths(tmp_path):
    write_config(tmp_path, 'overlay_path = "data/o.db"\n[sync]\ncap = 5\n')
    config = load(tmp_path)
    assert config.overlay_path == tmp_path / "data/o.db"
    assert config.sync.cap == 5
    assert config.zim_path is None


def test_explicit_zim_path(tmp_path):
    write_config(tmp_path, 'zim_path = "elsewhere/x.zim"\n')
    assert load(tmp_path).zim_path == tmp_path / "elsewhere/x.zim"


def test_find_zim_takes_newest_and_ignores_macos_sidecars(tmp_path):
    write_config(tmp_path)
    for name in ("w_2026-06.zim", "w_2026-09.zim", "._w_2026-09.zim", "._z.zim"):
        (tmp_path / "data" / name).touch()
    assert find_zim(tmp_path).name == "w_2026-09.zim"


def test_shipped_config_loads():
    assert load().wiki_domain == "simple.wikipedia.org"

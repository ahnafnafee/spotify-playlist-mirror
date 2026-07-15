"""SettingsStore: json + managed env file; wizard saves survive engine reload."""

import os
import stat

import pytest
from dotenv import load_dotenv

from songmirror.services.settings import SettingsStore


def test_saved_credential_survives_dotenv_reload(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLE_BEARER_TOKEN", raising=False)
    store = SettingsStore(dir=tmp_path)
    store.save({"APPLE_BEARER_TOKEN": "NEW"})
    # The engine reloads the managed file each pass; it must win — this is the
    # regression guard for load_dotenv(override=True) clobbering wizard saves.
    load_dotenv(store.env_path, override=True)
    assert os.environ["APPLE_BEARER_TOKEN"] == "NEW"


def test_roundtrip_persists(tmp_path):
    SettingsStore(dir=tmp_path).save({"SYNC_INTERVAL": "30m", "SPOTIFY_CLIENT_ID": "abc"})
    reopened = SettingsStore(dir=tmp_path)
    assert reopened.get("SYNC_INTERVAL") == "30m"
    assert reopened.get("SPOTIFY_CLIENT_ID") == "abc"


def test_none_values_ignored(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save({"A": "1", "B": None})
    assert store.get("A") == "1"
    assert "B" not in store.load()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes are ignored on Windows")
def test_credential_files_owner_only(tmp_path):
    store = SettingsStore(dir=tmp_path)
    store.save({"APPLE_BEARER_TOKEN": "secret"})
    for p in (store._json, store.env_path):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


def test_env_file_quotes_spaces(tmp_path, monkeypatch):
    monkeypatch.delenv("APPLE_STOREFRONT", raising=False)
    store = SettingsStore(dir=tmp_path)
    store.save({"NOTE": "two words"})
    load_dotenv(store.env_path, override=True)
    assert os.environ["NOTE"] == "two words"

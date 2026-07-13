"""Connector protocol — how the UI connects each service.

The engine knows how to *use* a service once tokens exist; connectors know how
to *obtain* them. One connector per service, keyed to the targets registry, so
"connect any service" is uniform in the wizard regardless of auth style.
"""

from dataclasses import dataclass
from typing import Literal

AuthKind = Literal["oauth_redirect", "oauth_device", "token_paste", "api_key"]


@dataclass
class Field:
    """One input the wizard renders for a connector's app config."""

    key: str
    label: str
    secret: bool = False
    help: str = ""
    required: bool = True


@dataclass
class ConnStatus:
    state: Literal["connected", "expired", "unconfigured", "error"]
    detail: str = ""


@dataclass
class DeviceCode:
    """What the browser shows during an OAuth device flow."""

    user_code: str
    verification_url: str
    device_code: str
    interval: int = 5


class Connector:
    """Base class. Each service implements status() plus the methods for its
    auth_kind. Takes a SettingsStore so every connector reads/writes config the
    same way."""

    id: str = ""
    name: str = ""
    auth_kind: AuthKind = "api_key"
    config_fields: list[Field] = []

    def __init__(self, store):
        self._store = store

    def status(self) -> ConnStatus:
        raise NotImplementedError

    # oauth_redirect
    def begin_redirect(self, redirect_uri: str) -> str:
        raise NotImplementedError

    def complete_redirect(self, params: dict) -> ConnStatus:
        raise NotImplementedError

    # oauth_device
    def begin_device(self) -> DeviceCode:
        raise NotImplementedError

    def poll_device(self, dc: DeviceCode) -> ConnStatus:
        raise NotImplementedError

    # token_paste / api_key
    def submit(self, values: dict) -> ConnStatus:
        raise NotImplementedError

    def _configured(self, *keys) -> bool:
        return all(self._store.get(k) for k in keys)

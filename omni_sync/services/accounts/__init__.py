"""Account connectors — one per service, keyed to the targets registry.

Adding a service: write a Connector subclass and add one line here (mirrors the
targets registry, where the same service also gets a MirrorTarget).
"""

from .apple import AppleConnector
from .base import ConnStatus, Connector, DeviceCode, Field
from .jellyfin import JellyfinConnector
from .spotify import SpotifyConnector
from .ytmusic import YTMusicConnector

__all__ = ["CONNECTORS", "Connector", "ConnStatus", "DeviceCode", "Field"]

CONNECTORS = {
    "spotify": SpotifyConnector,
    "apple": AppleConnector,
    "ytmusic": YTMusicConnector,
    "jellyfin": JellyfinConnector,
}

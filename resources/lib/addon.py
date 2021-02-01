# coding: utf-8
#
# Copyright © 2021 melmorabity
#
# This program is free software; you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free Software
# Foundation; either version 2 of the License, or (at your option) any later
# version.
#
# This program is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
# FOR A PARTICULAR PURPOSE. See the GNU General Public License for more
# details.
#
# You should have received a copy of the GNU General Public License along with
# this program; if not, write to the Free Software Foundation, Inc., 51
# Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

from __future__ import unicode_literals
import logging
import os
import re

try:
    from typing import Dict
    from typing import Optional
    from typing import Text
except ImportError:
    pass

try:
    from urllib.parse import parse_qsl
    from urllib.parse import quote
except ImportError:
    from urlparse import parse_qsl
    from urllib import quote

from inputstreamhelper import Helper  # pylint: disable=import-error
import xbmc  # pylint: disable=import-error
from xbmcaddon import Addon  # pylint: disable=import-error
from xbmcgui import Dialog  # pylint: disable=import-error
from xbmcgui import ListItem  # pylint: disable=import-error
import xbmcplugin  # pylint: disable=import-error

from resources.lib.api import ArteTV
from resources.lib.api import ParsedItem
import resources.lib.kodilogging
from resources.lib.utils import update_url_params


resources.lib.kodilogging.config()

_LOGGER = logging.getLogger(__name__)

_KODI_VERSION = int(xbmc.getInfoLabel("System.BuildVersion").split(".")[0])

_ADDON = Addon()
_ADDON_DIR = xbmc.translatePath(_ADDON.getAddonInfo("path"))
_ADDON_MEDIA_DIR = os.path.join(_ADDON_DIR, "resources", "media")
_ADDON_FANART = Addon().getAddonInfo("fanart")


# pylint: disable=too-few-public-methods
class ArteTVAddon:
    _ADDON = Addon()
    _ADDON_ID = "plugin.video.artetv"
    _ADDON_DIR = xbmc.translatePath(_ADDON.getAddonInfo("path"))
    _ADDON_MEDIA_DIR = os.path.join(_ADDON_DIR, "resources", "media")
    _ADDON_FANART = Addon().getAddonInfo("fanart")

    def __init__(self, base_url, handle, params):
        # type: (Text, int, Text) -> None

        self._base_url = base_url
        self._handle = handle
        self._params = self._params_to_dict(params)

        self._language = self._addon_language()
        self._api = ArteTV(self._language)

    @staticmethod
    def _params_to_dict(params):
        # type: (Optional[Text]) -> Dict[Text, Text]

        # Parameter string starts with a '?'
        return dict(parse_qsl(params[1:])) if params else {}

    @classmethod
    def _addon_language(cls):
        # type: () -> Text

        language = cls._ADDON.getSetting("language")

        if not language:
            language = xbmc.getLanguage(xbmc.ISO_639_1)
            if language not in ArteTV.LANGUAGES:
                language = "en"

            _ADDON.setSetting("language", language)

        return language

    def _localize(self, label):
        # type: (Text) -> Text

        return re.sub(
            r"\$LOCALIZE\[(\d+)\]",
            lambda m: self._ADDON.getLocalizedString(int(m.group(1))),
            label,
        )

    def _add_video_context_menu(self, listitem, video_id):
        # type: (ListItem, Text) -> None

        streams = self._api.get_video_streams(video_id)

        # Don't display alternate streams if there is only one or less
        if len(streams) <= 1:
            return

        listitem.addContextMenuItems(
            [
                (
                    _ADDON.getLocalizedString(30203).format(v.label),
                    "XBMC.PlayMedia({})".format(
                        update_url_params(
                            self._base_url,
                            mode="watch",
                            id=video_id,
                            version=k,
                        )
                    ),
                )
                for k, v in self._api.get_video_streams(str(video_id)).items()
            ]
        )

    def _add_listitem(self, parsed_item):
        # type: (ParsedItem) -> None

        is_folder = parsed_item.url.get("mode") == "collection"

        _LOGGER.debug("Add ListItem %s", parsed_item)
        listitem = ListItem(
            label=self._localize(parsed_item.label), offscreen=True
        )
        if not parsed_item.info.get("plot"):
            parsed_item.info["plot"] = listitem.getLabel()

        listitem.setInfo("video", parsed_item.info)

        # Set fallback fanart
        parsed_item.art.setdefault("fanart", self._ADDON_FANART)
        listitem.setArt(parsed_item.art)

        # Add context menu for alternate stream versions
        if parsed_item.url.get("mode") == "watch":
            self._add_video_context_menu(listitem, str(parsed_item.url["id"]))

        for key, value in list(parsed_item.properties.items()):
            listitem.setProperty(key, value)

        xbmcplugin.addDirectoryItem(
            self._handle,
            update_url_params(self._base_url, **parsed_item.url),
            listitem,
            isFolder=is_folder,
        )

    def _mode_collection(self, path):
        # type: (Text) -> None

        xbmcplugin.setContent(self._handle, "movies")

        level = None  # type: Optional[int]
        if self._params.get("level"):
            try:
                level = int(self._params["level"])
            except ValueError:
                pass

        for item in self._api.get_collection(path, level):
            self._add_listitem(item)

    def _mode_watch(self, video_id, version=None):
        # type: (Text, Optional[Text]) -> None

        is_helper = Helper("mpd")

        streams = self._api.get_video_streams(video_id)
        if not version:
            # First stream is default
            version = next(iter(streams), None)
            if not version:
                _LOGGER.error("No stream available for video %s", video_id)
                return
            video_url = streams[version].url
        else:
            if version not in streams:
                _LOGGER.error(
                    'No stream "%s" available for video %s', version, video_id
                )
                return
            video_url = streams[version].url

        logging.debug("Stream URL for video %s: %s", video_id, video_url)

        listitem = ListItem(path=video_url, offscreen=True)

        # Use inpoutstream.adaptive for better HLS stream management
        if is_helper.check_inputstream():
            listitem.setMimeType("application/vnd.apple.mpegurl")
            listitem.setProperty("inputstream.adaptive.manifest_type", "hls")

            if _KODI_VERSION >= 19:
                listitem.setProperty(
                    "inputstream", is_helper.inputstream_addon
                )
            else:
                listitem.setProperty(
                    "inputstreamaddon", is_helper.inputstream_addon
                )

        xbmcplugin.setResolvedUrl(self._handle, True, listitem)

    def _mode_search(self):
        # type: () -> None

        search = Dialog().input(self._ADDON.getLocalizedString(30005))

        self._mode_collection(
            "data/SEARCH_LISTING/?query={}".format(quote(search, safe=""))
        )

    def _mode_default(self):
        # type: () -> None

        # Live is only available in German and French
        if self._language in ["de", "fr"]:
            self._add_listitem(
                ParsedItem(
                    _ADDON.getLocalizedString(30001),
                    {"mode": "watch", "id": "LIVE"},
                    # Don't mark live streams as read once played
                    {"playcount": 0},
                    {"icon": os.path.join(_ADDON_MEDIA_DIR, "live-tv.png")},
                    {"isPlayable": "true"},
                )
            )

        self._add_listitem(
            ParsedItem(
                _ADDON.getLocalizedString(30002),
                {"mode": "collection", "path": "CATEGORIES"},
                {},
                {"icon": os.path.join(_ADDON_MEDIA_DIR, "categories.png")},
                {"isPlayable": "false"},
            )
        )

        self._add_listitem(
            ParsedItem(
                _ADDON.getLocalizedString(30003),
                {"mode": "collection", "path": "MAGAZINES", "level": 0},
                {},
                {"icon": os.path.join(_ADDON_MEDIA_DIR, "tv-shows.png")},
                {"isPlayable": "false"},
            )
        )

        self._add_listitem(
            ParsedItem(
                _ADDON.getLocalizedString(30004),
                {"mode": "collection", "path": "MOST_VIEWED", "level": 0},
                {},
                {"icon": os.path.join(_ADDON_MEDIA_DIR, "trending.png")},
                {"isPlayable": "false"},
            )
        )

        self._add_listitem(
            ParsedItem(
                _ADDON.getLocalizedString(30005),
                {"mode": "collection", "path": "MOST_RECENT", "level": 0},
                {},
                {"icon": os.path.join(_ADDON_MEDIA_DIR, "most-recent.png")},
                {"isPlayable": "false"},
            )
        )

        self._add_listitem(
            ParsedItem(
                _ADDON.getLocalizedString(30006),
                {"mode": "collection", "path": "LAST_CHANCE", "level": 0},
                {},
                {"icon": os.path.join(_ADDON_MEDIA_DIR, "last-chance.png")},
                {"isPlayable": "false"},
            )
        )

        self._add_listitem(
            ParsedItem(
                _ADDON.getLocalizedString(30007),
                {"mode": "search"},
                {},
                {"icon": os.path.join(_ADDON_MEDIA_DIR, "search.png")},
                {"isPlayable": "false"},
            )
        )

    def run(self):
        # type: () -> None

        mode = self._params.get("mode")
        _LOGGER.debug("Addon params = %s", self._params)
        succeeded = True

        try:
            if mode == "collection" and self._params.get("path"):
                self._mode_collection(self._params["path"])
            elif mode == "watch" and self._params.get("id"):
                self._mode_watch(
                    self._params["id"], self._params.get("version")
                )
            elif mode == "search":
                self._mode_search()
            else:
                self._mode_default()
        finally:
            xbmcplugin.endOfDirectory(self._handle, succeeded=succeeded)

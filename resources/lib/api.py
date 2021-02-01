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
from collections import OrderedDict
import logging
from os.path import dirname
from os.path import join

try:
    from typing import Any
    from typing import Dict
    from typing import Generator
    from typing import NamedTuple
    from typing import Optional
    from typing import Text
    from typing import Union

    Item = Dict[Text, Any]

    Art = Dict[Text, Optional[Text]]  # pylint: disable=unsubscriptable-object

    Url = Dict[Text, Union[int, Text]]

    ParsedItem = NamedTuple(
        "ParsedItem",
        [
            ("label", Text),
            ("url", Url),
            ("info", Dict[Text, Any]),
            ("art", Art),
            ("properties", Dict[Text, Text]),
        ],
    )

    Stream = NamedTuple("StreamVersion", [("label", Text), ("url", Text)])
except ImportError:
    from collections import namedtuple  # pylint: disable=ungrouped-imports

    ParsedItem = namedtuple(  # type: ignore
        "ParsedItem", ["label", "url", "info", "art", "properties"]
    )

    Stream = namedtuple("StreamVersion", ["label", "url"])  # type: ignore

from dateutil.parser import isoparse
from requests import Response
from requests import Session
from requests.exceptions import HTTPError


_LOGGER = logging.getLogger(__name__)

_IMAGE_TYPE_MAPPING = {
    "banner": "banner",
    "landscape": "fanart",
    "portrait": "poster",
    "square": "thumb",
}

_MEDIA_DIR = join(dirname(__file__), "..", "media")
_NEXT_PAGE_ICON = join(_MEDIA_DIR, "next-page.png")


class ArteTVException(Exception):
    pass


class ArteTV:
    LANGUAGES = ["de", "en", "es", "fr", "it", "pl"]

    _API_V3_BASE_URL = "https://api.arte.tv/api/emac/v3"
    _API_V3_BEARER = (
        "MWZmZjk5NjE1ODgxM2E0MTI2NzY4MzQ5MTZkOWVkYTA1M2U4YjM3NDM2MjEwMDllODRhM"
        "jIzZjQwNjBiNGYxYw"
    )

    _API_V2_URL = "https://api.arte.tv/api/player/v2/config"
    _API_V2_BEARER = (
        "ZWU0ZWU0NDlmNTNkODcwNWZhNTYzOTc5MjExZTc4NjE4NzExYjE1OTM3YjFhOTQxMTJhN"
        "WJlNzYxNmM3MTdjYQ"
    )

    def __init__(self, language):
        # type: (Text) -> None

        self._language = language
        self._api_v3_url = "{}/{}/app".format(self._API_V3_BASE_URL, language)
        self._session = Session()
        self._session.headers.update(
            {"User-Agent": "arte/214402054"}  # type: ignore
        )
        self._session.hooks = {"response": [self._requests_raise_status]}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._session:
            self._session.close()

    @staticmethod
    def _requests_raise_status(response, *_args, **_kwargs):
        # type: (Response, Any, Any) -> None

        try:
            response.raise_for_status()
        except HTTPError as ex:
            try:
                raise ArteTVException(ex, ex.response.json().get("error"))
            except ValueError:
                raise ex

    def _query_api(self, url, bearer, params=None):
        # type: (Text, Text, Optional[Dict[Text, Text]]) -> Dict[Text, Any]

        return self._session.get(
            url,
            headers={"Authorization": "Bearer {}".format(bearer)},
            params=params,
        ).json()

    def _query_app_api(self, path):
        # type: (Text) -> Dict[Text, Any]

        # path = update_url_params(
        #     path, imageFormats="banner,landscape,portrait,square"
        # )

        return self._session.get(
            "{}/{}".format(self._api_v3_url, path.strip("/")),
            headers={"Authorization": "Bearer {}".format(self._API_V3_BEARER)},
        ).json()

    def _query_player_api(self, video_id):
        return self._query_api(
            "{}/{}/{}".format(self._API_V2_URL, self._language, video_id),
            self._API_V2_BEARER,
        )

    @staticmethod
    def _parse_item_art(item):
        # type: (Item) -> Art

        art = {}  # type: Art

        for image_type, image in list((item.get("images") or {}).items()):
            kodi_image_type = _IMAGE_TYPE_MAPPING.get(image_type)

            if not kodi_image_type:
                raise Exception("TYPE INCONNU")

            if not image or not image.get("resolutions"):
                continue

            # Sort images by quality
            image_urls = sorted(
                image["resolutions"], key=lambda i: i.get("w") or 0
            )

            image_url = image_urls[-1].get("url")
            art.setdefault(kodi_image_type, image_url)

        art.setdefault("icon", art.get("fanart"))

        return art

    @staticmethod
    def _get_item_url(
        item,  # type: Item
        path,  # type: Text
        level,  # type: int
    ):
        # type: (...) -> Optional[Url]

        program_id = item.get("programId")

        if (item.get("kind") or {}).get("isCollection") and program_id:
            return {"mode": "collection", "path": program_id}

        next_path = (item.get("link") or {}).get("page")
        if next_path and next_path != path:
            return {"mode": "collection", "path": next_path}

        if item.get("data"):
            return {"mode": "collection", "path": path, "level": level}

        if program_id:
            return {"mode": "watch", "id": program_id}

        _LOGGER.warning("Item %s in path %s is unmanaged", item, path)
        return None

    # pylint: disable=too-many-branches
    @staticmethod
    def _parse_item(
        item,  # type: Item
        url,  # type: Url
    ):
        # type: (...) -> Optional[ParsedItem]

        info = {}  # type: Dict[Text, Any]
        art = ArteTV._parse_item_art(item)
        properties = {}  # type: Dict[Text, Text]

        title = item.get("title") or ""
        if not title:
            _LOGGER.warning("No title in item %s in path %s", item, url)

        result = ParsedItem(title, url, info, art, properties)

        short_description = item.get("shortDescription")
        info["plot"] = (
            item.get("fullDescription")
            or item.get("description")
            or short_description
        )

        # No need to parse more item metadata for collections
        if url["mode"] == "collection":
            properties["isPlayable"] = "false"
            return result

        if item.get("ageRating"):
            info["mpaa"] = item["ageRating"]

        if short_description and short_description != info["plot"]:
            info["plotoutline"] = short_description

        info["duration"] = item.get("duration")
        info["tagline"] = item.get("subtitle") or item.get("teaserText")

        date_added = (item.get("availability") or {}).get("start")
        if date_added:
            info["dateadded"] = isoparse(date_added).strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        info["mediatype"] = "movie"

        properties["isPlayable"] = "true"

        return result

    def _get_program_item(self, program_id):
        # type: (str) -> Optional[Item]

        data = self._query_app_api(program_id)
        program_content = next(
            (
                p
                for p in (data.get("zones") or [])
                if (p.get("code") or {}).get("name") == "program_content"
            ),
            None,
        )
        if not program_content:
            return None

        return next(iter(program_content.get("data") or []), None)

    def get_collection(self, path, level):
        # type: (Text, Optional[int]) -> Generator[ParsedItem, None, None]

        data = self._query_app_api(path)
        collection = data.get("zones") or data.get("data") or []

        if level is not None and level < len(collection):
            data = collection[level]
            collection = data.get("data") or []

        for index, item in enumerate(collection):
            # Skip collection content descriptor items
            code_name = (item.get("code") or {}).get("name")
            if code_name in [
                "banner_1",
                "collection_associated",
                "collection_content",
                "collection_partner",
                "collection_upcoming",
            ]:
                continue

            # Skip items pointing to external resources (only displayed on
            # the French website)
            kind = (item.get("kind") or {}).get("code")
            if kind == "EXTERNAL" or item.get("title") in [
                "ARTE Boutique",
                "ARTE Radio",
            ]:
                continue

            url = self._get_item_url(item, path, index)
            if not url:
                continue

            parsed_item = self._parse_item(item, url)
            if parsed_item:
                yield parsed_item

        # Add "next page" item
        if data.get("nextPage"):
            yield ParsedItem(
                "$LOCALIZE[30201]",
                {
                    "mode": "collection",
                    "path": data["nextPage"].replace(self._api_v3_url, ""),
                },
                {"plot": ""},
                {"icon": _NEXT_PAGE_ICON},
                {"SpecialSort": "bottom"},
            )

    def get_video_streams(self, video_id):
        # type: (Text) -> Dict[Text, Stream]

        data = self._query_player_api(video_id).get("data") or {}

        streams = (data.get("attributes") or {}).get("streams") or []
        print(streams)
        result = OrderedDict()  # type: Dict[Text, Stream]

        for stream in streams:
            if not stream.get("url") or not stream.get("versions"):
                continue

            stream_data = stream["versions"][0]
            if not stream_data.get("shortLabel") or not stream_data.get(
                "label"
            ):
                continue

            result[stream_data["shortLabel"]] = Stream(
                stream_data["label"], stream["url"]
            )

        return result

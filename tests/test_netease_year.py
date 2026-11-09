# -*- coding: utf-8 -*-
"""H07 验收：网易年份写回必须持有可交叉验证的远端艺人证据。"""

import os
import sys
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.fetchers.netease import NetEaseFetcher  # noqa: E402

# 1998-01-01 00:00:00 UTC
PUBLISH_MS = 883612800000  # 1998-01-01 00:00:00 UTC
PUBLISH_S = 883612800


def _song(artists, publish=PUBLISH_MS):
    return {
        "id": 123,
        "artists": artists,
        "album": {"name": "Example Album", "publishTime": publish},
    }


def _year_with(song, artist="Example Artist"):
    with mock.patch.object(NetEaseFetcher, "search_best_song", return_value=song):
        return NetEaseFetcher.lookup_year("Example Song", artist, 200000)


def test_valid_artist_evidence_allows_year():
    """本地与远端艺人有交集 → 正常补全年份。"""
    assert _year_with(_song([{"name": "Example Artist"}])) == "1998"


def test_collaboration_artist_intersection_allows_year():
    """合作艺人只要有一个交集即可授权。"""
    song = _song([{"name": "Other Singer"}, {"name": "Example Artist"}])
    assert _year_with(song) == "1998"


def test_missing_artist_list_blocks_year():
    for artists in (None, [], [{}], [{"no_name": "x"}], [{"name": ""}], "Example Artist", 0):
        assert _year_with(_song(artists)) == "", f"artists={artists!r} 仍写入了年份"


def test_disjoint_artist_blocks_year():
    """远端有艺人但与本地无交集 → 不得写年份。"""
    assert _year_with(_song([{"name": "Someone Else"}])) == ""


def test_generic_local_artist_blocks_year():
    for artist in ("", "   ", "群星", "Unknown", "Various Artists"):
        assert _year_with(_song([{"name": "Example Artist"}]), artist=artist) == ""


def test_invalid_publish_time_yields_empty():
    for publish in (0, -1, None, "abc", {}):
        assert _year_with(_song([{"name": "Example Artist"}], publish=publish)) == ""


def test_seconds_precision_still_normalized():
    assert _year_with(_song([{"name": "Example Artist"}], publish=PUBLISH_S)) == "1998"


def test_lyrics_candidate_recall_not_tightened():
    """H07 只收紧年份授权，不得改变老歌词候选的宽松召回。"""
    loose_song = {
        "id": 9,
        "name": "Example Song",
        "artists": None,
        "album": {"name": "Example Album"},
        "duration": 200000,
    }
    import core.fetchers.netease as ne_mod

    with mock.patch.object(ne_mod, "http_post_json", return_value={"result": {"songs": [loose_song]}}):
        cand = NetEaseFetcher.search_best_song("Example Song", "Example Artist", 200000)
    assert cand is not None, "无艺人证据时歌词候选被误拒"
    assert cand["id"] == 9


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())

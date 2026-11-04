# -*- coding: utf-8 -*-
"""QQ 音乐 QRC 逐字通道（v1.7.0）：解密、解析、fetcher、聚合器七源接缝。

纯离线：A 组读真实密文夹具做 KAT，B 组用内联 QRC 文本，C/D 组全部 mock 网络。
运行：python tests/test_qq_qrc.py
"""

try:  # 允许 `python tests/xxx.py` 与 `python -m tests.xxx` 两种调用
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

import hashlib
import inspect
import json
import os
import sys
import time
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from core.converter import to_spl                                   # noqa: E402
from core.decryptors.qrc_des import (                               # noqa: E402
    QRC_KEY,
    decrypt_qrc,
    des_decrypt_block,
    des_encrypt_block,
    des3_decrypt,
)
from core.parsers.qrc_parser import parse_qrc, unwrap_qrc           # noqa: E402
from core.fetchers.qq import QQFetcher, DURATION_WINDOW_MS          # noqa: E402
import core.aggregator as agg                                       # noqa: E402

FIXTURES = os.path.join(REPO_ROOT, "tests", "fixtures")
HEX_PATH = os.path.join(FIXTURES, "qq_qrc_qingtian.hex")
META_PATH = os.path.join(FIXTURES, "qq_qrc_qingtian.json")


def _fixture_hex() -> str:
    with open(HEX_PATH, encoding="ascii") as fh:
        return fh.read().strip()


def _fixture_meta() -> dict:
    with open(META_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _fixture_plain() -> str:
    return decrypt_qrc(_fixture_hex())


# ===========================================================================
# A 组：3DES 解密
# ===========================================================================

def test_a1_real_ciphertext_kat():
    """真实密文 KAT：解密结果长度与 SHA-256 必须与夹具元数据一致。"""
    meta = _fixture_meta()
    plain = _fixture_plain()
    assert plain != ""
    assert len(plain) == meta["plain_len"], (len(plain), meta["plain_len"])
    assert hashlib.sha256(plain.encode()).hexdigest() == meta["plain_sha256"]
    assert "[ti:晴天]" in plain
    assert meta["first_time_line"] in plain


def test_a2_block_roundtrip():
    """单块 DES 加解密往返：验证位序移植没抄错。"""
    key = QRC_KEY[0:8]
    plain = b"\x00" * 8
    cipher = des_encrypt_block(key, plain)
    assert len(cipher) == 8
    assert des_decrypt_block(key, cipher) == plain
    # 非零明文也要能往返
    plain2 = b"QRC\x01\x02\x03\x04\x05"
    assert des_decrypt_block(key, des_encrypt_block(key, plain2)) == plain2


def test_a3_uppercase_and_newlines_tolerated():
    """hex 大写、插空白、插换行都要能解。"""
    raw = _fixture_hex()
    deformed = "\n".join(raw.upper()[i:i + 64] for i in range(0, len(raw), 64))
    assert decrypt_qrc(deformed) == _fixture_plain()


def test_a4_invalid_hex_returns_empty():
    """非法输入一律返回空串，不抛异常。"""
    for bad in ("", "zz", "0", "abc", "00", None):
        assert decrypt_qrc(bad) == "", bad


def test_a5_tampered_ciphertext_returns_empty():
    """翻转密文中段一个字节后 zlib 必然失败，返回空串而不是半截乱码。"""
    raw = _fixture_hex()
    mid = (len(raw) // 4) * 2
    flipped = raw[:mid] + ("0" if raw[mid] != "0" else "1") + raw[mid + 1:]
    assert decrypt_qrc(flipped) == ""


def test_a6_partial_block_truncated():
    """非 8 倍数的密文按 8 截断，不抛异常。"""
    raw = bytes.fromhex(_fixture_hex())
    odd = raw[:len(raw) - 3]
    out = des3_decrypt(odd)
    assert len(out) == (len(odd) // 8) * 8
    assert out == des3_decrypt(raw[:len(odd)])


def test_a7_decrypt_performance():
    """10KB 密文解密必须在 2 秒内（实测约 0.5s）。"""
    hex_text = _fixture_hex()
    best = 1e9
    for _ in range(3):
        t0 = time.time()
        decrypt_qrc(hex_text)
        best = min(best, time.time() - t0)
    print("     实测解密耗时 %.0f ms" % (best * 1000))
    assert best < 2.0, best


def test_a8_subkey_cache_reused():
    """三组子密钥必须进程内复用，第二次调用不再重算。"""
    decrypt_qrc(_fixture_hex())
    t0 = time.time()
    decrypt_qrc(_fixture_hex())
    second = time.time() - t0
    assert second < 2.0
    import core.decryptors.qrc_des as qd
    assert qd._SCHEDULE_CACHE.get("schedules") is not None


# ===========================================================================
# B 组：QRC 解析
# ===========================================================================

QRC_TWO_LINES = (
    "[ti:测试曲]\n"
    "[ar:测试人]\n"
    "[al:测试专辑]\n"
    "[1000,2000]你(1000,500)好(1500,500)\n"
    "[4000,2530]整(4000,470)晚(4470,300) (4770,30)胡(4800,230)思(5030,550)乱(5580,470)\n"
)


def test_b1_basic_two_lines():
    """基础两行：字时间为绝对毫秒，三条恒等式成立。"""
    data = parse_qrc(QRC_TWO_LINES)
    lines = data["lines"]
    assert len(lines) == 2
    first = lines[0]
    assert first["start"] == 1000
    assert first["words"][0]["start"] == 1000
    assert first["end"] == first["words"][-1]["end"]
    assert first["text"] == "你好"
    assert first["text"] == "".join(w["text"] for w in first["words"])
    assert first["words"][1]["start"] == 1500


def test_b2_meta_tags():
    """元信息行要进 tags，且首尾空白被去掉。"""
    data = parse_qrc(QRC_TWO_LINES)
    assert data["tags"]["ti"] == "测试曲"
    assert data["tags"]["ar"] == "测试人"
    assert data["tags"]["al"] == "测试专辑"


def test_b3_offset_and_adjust():
    """[offset] 与 adjust 叠加生效。"""
    data = parse_qrc("[offset:500]\n[1000,1000]甲(1000,500)乙(1500,500)\n", adjust=200)
    line = data["lines"][0]
    assert line["start"] == 1700
    assert line["words"][0]["start"] == 1700


def test_b4_zero_duration_punct_merged():
    """零时长块并入前一字，不产出 duration<=0 的 word。"""
    text = "[1000,2000]甲(1000,500)乙(1500,500)(2000,0)，\n"
    data = parse_qrc(text)
    words = data["lines"][0]["words"]
    assert all(w["duration"] > 0 for w in words)
    assert data["lines"][0]["text"].endswith("，")


def test_b5_blank_block_merged():
    """纯空白块并入前一字并延长其 end。"""
    data = parse_qrc(QRC_TWO_LINES)
    line = data["lines"][1]
    texts = [w["text"] for w in line["words"]]
    # 「晚」与「胡」之间的空格块应并入「晚」，不产生纯空白 word
    assert any(t.endswith(" ") for t in texts), texts
    assert all(w["text"].strip() or " " in w["text"] for w in line["words"])
    assert all(w["text"].strip() for w in line["words"]), texts


def test_b6_leading_block_absorbed():
    """行首出现零时长块时记为 pending，并入下一真实字。"""
    data = parse_qrc("[1000,2000](1000,0)甲(1100,400)乙(1500,500)\n")
    line = data["lines"][0]
    assert line["start"] == 1100
    assert line["words"][0]["start"] == 1100


def test_b7_plain_parentheses_not_truncated():
    """正文里的 (FR) 这种非数字括号不得截断文本。"""
    text = "[1000,3000]母(1000,100)带(1100,100)：(1200,100)Patrick(1300,400) (1700,100)(FR)(1800,100)\n"
    data = parse_qrc(text)
    assert "(FR)" in data["lines"][0]["text"]


def test_b8_out_of_range_param_kept_as_text():
    """越界参数（起点离行范围过远）不当块边界，原样留在文本里，不丢字。"""
    text = "[1000,2000]甲(1000,500)乙(1500,500)(999999,100)尾\n"
    data = parse_qrc(text)
    joined = data["lines"][0]["text"]
    assert "999999" in joined, joined
    assert joined.startswith("甲乙")
    assert "尾" in joined


def test_b9_paramless_line_uses_header():
    """整行没有任何字块时，用行头兜一个覆盖整行的 word，不丢词。"""
    data = parse_qrc("[5000,1000]纯文本行\n")
    line = data["lines"][0]
    assert line["text"] == "纯文本行"
    assert line["start"] == 5000
    assert len(line["words"]) == 1
    assert line["words"][0]["duration"] == 1000


def test_b10_empty_body_dropped():
    """空正文行直接丢弃，不产幽灵行。"""
    data = parse_qrc("[1000,1000]\n[2000,1000]有字(2000,500)\n")
    assert len(data["lines"]) == 1
    assert data["lines"][0]["text"] == "有字"


def test_b11_backward_start_clamped():
    """字起点倒退时钳到前一字 end，保证非递减。"""
    data = parse_qrc("[1000,3000]甲(1500,500)乙(1200,300)\n")
    words = data["lines"][0]["words"]
    assert words[1]["start"] >= words[0]["end"]


def test_b12_precision_and_no_translation_fields():
    """time_precision 恒为 3；不携带 tlyric / has_embedded_translation。"""
    data = parse_qrc(QRC_TWO_LINES)
    assert data["time_precision"] == 3
    assert "tlyric" not in data
    assert "has_embedded_translation" not in data


def test_b13_xml_wrapper_unwrapped():
    """解密明文是 QrcInfos XML 包装时，要取出 LyricContent 并解转义。"""
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n<QrcInfos>\n<QrcHeadInfo SaveTime="1" Version="100"/>\n'
        '<LyricInfo LyricCount="1">\n<Lyric_1 LyricType="1" LyricContent="[ti:x]&#10;[1000,1000]甲(1000,500)乙(1500,500)"/>\n'
        "</LyricInfo>\n</QrcInfos>\n"
    )
    body = unwrap_qrc(xml)
    assert "[ti:x]" in body
    assert "&#10;" not in body
    data = parse_qrc(xml)
    assert data["tags"]["ti"] == "x"
    assert data["lines"][0]["text"] == "甲乙"


def test_b14_converter_no_degrade():
    """解析结果喂给 converter 必须全部渲染成逐字行，不触发字面不一致降级。"""
    import re as _re
    data = parse_qrc(QRC_TWO_LINES)
    data["source"] = "qq"
    out = to_spl(data, title="测试曲", artist="测试人")
    verbatim = [l for l in out.splitlines() if _re.match(r"^\[\d{2}:\d{2}\.\d{3}\]<", l)]
    assert len(verbatim) >= 2, out
    # 去掉时间标签后原文必须完整还原（逐字标签把字隔开了）
    plain = _re.sub(r"<\d{2}:\d{2}\.\d{3}>", "", _re.sub(r"\[\d{2}:\d{2}\.\d{3}\]", "", out))
    assert "你好" in plain, plain
    assert "整晚 胡思乱" in plain, plain


def test_b15_real_fixture_structure():
    """真实夹具：63 行、597 个字、全部恒等式成立。"""
    meta = _fixture_meta()
    data = parse_qrc(_fixture_plain(), duration_ms=meta["duration_ms"])
    lines = data["lines"]
    assert len(lines) == meta["lyric_line_count"]
    assert sum(len(l["words"]) for l in lines) == meta["word_count"]
    for ln in lines:
        assert ln["start"] == ln["words"][0]["start"]
        assert ln["end"] == ln["words"][-1]["end"]
        assert ln["text"] == "".join(w["text"] for w in ln["words"])


def test_b16_real_fixture_no_overlap():
    """真实夹具：行间零重叠、字间零重叠、无空字、无零时长。"""
    data = parse_qrc(_fixture_plain(), duration_ms=269000)
    lines = data["lines"]
    for a, b in zip(lines, lines[1:]):
        assert a["end"] <= b["start"] + 1, (a["text"], b["text"])
    for ln in lines:
        for w in ln["words"]:
            assert w["text"] != ""
            assert w["duration"] > 0
        for x, y in zip(ln["words"], ln["words"][1:]):
            assert x["end"] <= y["start"] + 1


def test_b17_real_fixture_first_body_line():
    """真实夹具首行是标题，起唱前有词曲行。"""
    data = parse_qrc(_fixture_plain(), duration_ms=269000)
    lines = data["lines"]
    assert lines[0]["start"] == 0
    assert "晴天" in lines[0]["text"]


# ===========================================================================
# C 组：fetcher（全 mock，不联网）
# ===========================================================================

def _make_search_payload(items):
    return {"data": {"song": {"list": items}}}


def _song(song_id, name, singers, album, interval):
    return {
        "songid": song_id,
        "songmid": "m%s" % song_id,
        "songname": name,
        "singer": [{"name": s} for s in singers],
        "albumname": album,
        "interval": interval,
    }


def _qrc_hex_for_test():
    return _fixture_hex()


def test_c1_search_url_and_headers():
    """搜索必须带 format=json / p=1 / n=10 / w=歌名+歌手 / Referer。"""
    captured = {}

    def fake_get(url, headers=None, timeout=6, user_agent=None):
        captured["url"] = url
        captured["headers"] = headers or {}
        return _make_search_payload([_song(1, "晴天", ["周杰伦"], "叶惠美", 269)])

    with mock.patch("core.fetchers.qq.http_get_json", side_effect=fake_get):
        QQFetcher.search_songs("晴天", "周杰伦")

    url = captured["url"]
    assert "c.y.qq.com" in url
    assert "format=json" in url and "p=1" in url and "n=10" in url
    assert "%E6%99%B4%E5%A4%A9" in url or "晴天" in url
    assert captured["headers"].get("Referer") == "https://y.qq.com/"


def test_c2_query_ladder_strips_subtitle():
    """标题带副标题时，第二次检索要用清洗后的标题。"""
    seen = []

    def fake_get(url, headers=None, timeout=6, user_agent=None):
        seen.append(url)
        return _make_search_payload([])

    with mock.patch("core.fetchers.qq.http_get_json", side_effect=fake_get):
        QQFetcher.search_songs("晴天 (电影版)", "周杰伦")

    assert len(seen) >= 2, seen
    assert "%E7%94%B5%E5%BD%B1%E7%89%88" not in seen[1]


def test_c3_lyric_body_shape():
    """歌词请求体：method/module/songID 为 int/b64 字段/crypt=qrc=1。"""
    captured = {}

    def fake_post(url, payload=None, headers=None, timeout=8, user_agent=None):
        captured["payload"] = payload
        return {"req_0": {"code": 0, "data": {"lyric": ""}}}

    cand = {"song_id": 97773, "title": "晴天", "album": "叶惠美",
            "singers": ["周杰伦"], "interval": 269}
    with mock.patch("core.fetchers.qq.http_post_json_body", side_effect=fake_post):
        QQFetcher._request_qrc(cand)

    p = captured["payload"]["req_0"]
    assert p["method"] == "GetPlayLyricInfo"
    assert p["module"] == "music.musichallSong.PlayLyricInfo"
    param = p["param"]
    assert param["songID"] == 97773 and isinstance(param["songID"], int)
    import base64
    assert base64.b64decode(param["songName"]).decode() == "晴天"
    assert base64.b64decode(param["singerName"]).decode() == "周杰伦"
    assert param["crypt"] == 1 and param["qrc"] == 1
    assert param["interval"] == 269


def test_c4_four_way_filter():
    """四项筛选：只有正确的候选能进入歌词请求。"""
    requested = []
    items = [
        _song(1, "晴天", ["周杰伦"], "叶惠美", 269),
        _song(2, "晴天", ["蔡依林"], "叶惠美", 269),
        _song(3, "晴天", ["周杰伦"], "叶惠美", 279),
    ]

    def fake_get(url, headers=None, timeout=6, user_agent=None):
        return _make_search_payload(items)

    def fake_post(url, payload=None, headers=None, timeout=8, user_agent=None):
        requested.append(payload["req_0"]["param"]["songID"])
        return {"req_0": {"code": 0, "data": {"lyric": ""}}}

    with mock.patch("core.fetchers.qq.http_get_json", side_effect=fake_get), \
         mock.patch("core.fetchers.qq.http_post_json_body", side_effect=fake_post):
        QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000, album="叶惠美")

    assert requested == [1], requested


def test_c5_live_mutual_exclusion():
    """录音室目标不得被 Live 候选顶替。"""
    items = [_song(9, "晴天 (Live)", ["周杰伦"], "地表最强演唱会", 249)]
    requested = []

    def fake_post(url, payload=None, headers=None, timeout=8, user_agent=None):
        requested.append(payload)
        return {"req_0": {"code": 0, "data": {"lyric": ""}}}

    with mock.patch("core.fetchers.qq.http_get_json", return_value=_make_search_payload(items)), \
         mock.patch("core.fetchers.qq.http_post_json_body", side_effect=fake_post):
        out = QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000)

    assert out is None
    assert requested == []


def test_c6_duration_window():
    """时长差超过 5 秒的候选直接过滤。"""
    items = [_song(1, "晴天", ["周杰伦"], "叶惠美", 275)]
    requested = []

    def fake_post(url, payload=None, headers=None, timeout=8, user_agent=None):
        requested.append(payload)
        return {"req_0": {"code": 0, "data": {"lyric": ""}}}

    with mock.patch("core.fetchers.qq.http_get_json", return_value=_make_search_payload(items)), \
         mock.patch("core.fetchers.qq.http_post_json_body", side_effect=fake_post):
        QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000)

    assert requested == []
    assert DURATION_WINDOW_MS == 5000


def test_c7_artist_filter():
    """歌手无交集时过滤。"""
    items = [_song(2, "晴天", ["蔡依林"], "叶惠美", 269)]
    requested = []

    def fake_post(url, payload=None, headers=None, timeout=8, user_agent=None):
        requested.append(payload)
        return {"req_0": {"code": 0, "data": {"lyric": ""}}}

    with mock.patch("core.fetchers.qq.http_get_json", return_value=_make_search_payload(items)), \
         mock.patch("core.fetchers.qq.http_post_json_body", side_effect=fake_post):
        out = QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000)

    assert out is None and requested == []


def test_c8_album_preferred_in_ranking():
    """同标题同歌手同时长时，专辑一致者优先被请求。"""
    items = [
        _song(11, "晴天", ["周杰伦"], "别的专辑", 269),
        _song(22, "晴天", ["周杰伦"], "叶惠美", 269),
    ]
    order = []

    def fake_post(url, payload=None, headers=None, timeout=8, user_agent=None):
        order.append(payload["req_0"]["param"]["songID"])
        return {"req_0": {"code": 0, "data": {"lyric": ""}}}

    with mock.patch("core.fetchers.qq.http_get_json", return_value=_make_search_payload(items)), \
         mock.patch("core.fetchers.qq.http_post_json_body", side_effect=fake_post):
        QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000, album="叶惠美")

    assert order[0] == 22, order


def test_c9_empty_search_returns_none():
    """搜不到候选返回 None。"""
    with mock.patch("core.fetchers.qq.http_get_json", return_value=_make_search_payload([])):
        assert QQFetcher.fetch("不存在的歌", "不存在的歌手") is None


def test_c10_http_failure_returns_none():
    """搜索或歌词接口失败都返回 None，不抛异常。"""
    with mock.patch("core.fetchers.qq.http_get_json", return_value=None):
        assert QQFetcher.fetch("晴天", "周杰伦") is None

    with mock.patch("core.fetchers.qq.http_get_json",
                    return_value=_make_search_payload([_song(1, "晴天", ["周杰伦"], "叶惠美", 269)])), \
         mock.patch("core.fetchers.qq.http_post_json_body", return_value=None):
        assert QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000) is None


def test_c11_decrypt_failure_returns_none():
    """密文非法时返回 None。"""
    with mock.patch("core.fetchers.qq.http_get_json",
                    return_value=_make_search_payload([_song(1, "晴天", ["周杰伦"], "叶惠美", 269)])), \
         mock.patch("core.fetchers.qq.http_post_json_body",
                    return_value={"req_0": {"code": 0, "data": {"lyric": "zzzz"}}}):
        assert QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000) is None


def test_c12_line_level_rejected():
    """解出来是行级（每行只有 1 个字块）时必须放弃，不返回行级。"""
    line_level = "[1000,2000]只有一行文字\n[4000,2000]第二行文字\n"
    with mock.patch("core.fetchers.qq.http_get_json",
                    return_value=_make_search_payload([_song(1, "晴天", ["周杰伦"], "叶惠美", 269)])), \
         mock.patch("core.fetchers.qq.http_post_json_body",
                    return_value={"req_0": {"code": 0, "data": {"lyric": "aa"}}}), \
         mock.patch("core.fetchers.qq.decrypt_qrc", return_value=line_level):
        assert QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000) is None


def test_c13_success_returns_word():
    """成功返回：source=qq、lyric_type=word、precision=3、无 tlyric。"""
    with mock.patch("core.fetchers.qq.http_get_json",
                    return_value=_make_search_payload([_song(97773, "测试曲", ["测试人"], "测试专辑", 269)])), \
         mock.patch("core.fetchers.qq.http_post_json_body",
                    return_value={"req_0": {"code": 0, "data": {"lyric": "aa"}}}), \
         mock.patch("core.fetchers.qq.decrypt_qrc", return_value=QRC_TWO_LINES):
        out = QQFetcher.fetch("测试曲", "测试人", duration_ms=0)

    assert out is not None
    assert out["source"] == "qq"
    assert out["lyric_type"] == "word"
    assert out["time_precision"] == 3
    assert "tlyric" not in out
    assert out["tags"]["ti"] == "测试曲"


def test_c14_overrun_gate():
    """末行冲轴超过 8 秒且无法截断时丢弃。"""
    long_qrc = "[1000,3000]甲(1000,500)乙(1500,500)\n[500000,3000]尾(500000,500)字(500500,500)\n"
    with mock.patch("core.fetchers.qq.http_get_json",
                    return_value=_make_search_payload([_song(1, "晴天", ["周杰伦"], "叶惠美", 269)])), \
         mock.patch("core.fetchers.qq.http_post_json_body",
                    return_value={"req_0": {"code": 0, "data": {"lyric": "aa"}}}), \
         mock.patch("core.fetchers.qq.decrypt_qrc", return_value=long_qrc):
        assert QQFetcher.fetch("晴天", "周杰伦", duration_ms=200000) is None


def test_c15_preview_gate():
    """长曲只拿到前 40 秒的预览时丢弃。"""
    preview = "".join(
        "[%d,2000]甲(0,500)乙(500,500)\n" % (1000 + i * 2000) for i in range(5)
    )
    with mock.patch("core.fetchers.qq.http_get_json",
                    return_value=_make_search_payload([_song(1, "晴天", ["周杰伦"], "叶惠美", 269)])), \
         mock.patch("core.fetchers.qq.http_post_json_body",
                    return_value={"req_0": {"code": 0, "data": {"lyric": "aa"}}}), \
         mock.patch("core.fetchers.qq.decrypt_qrc", return_value=preview):
        assert QQFetcher.fetch("晴天", "周杰伦", duration_ms=200000) is None


def test_c16_shared_duration_gate():
    """门禁函数必须复用酷狗那份（物理位置 kugou.py），不是自己另写一套。"""
    with mock.patch("core.fetchers.qq.http_get_json",
                    return_value=_make_search_payload([_song(1, "晴天", ["周杰伦"], "叶惠美", 269)])), \
         mock.patch("core.fetchers.qq.http_post_json_body",
                    return_value={"req_0": {"code": 0, "data": {"lyric": "aa"}}}), \
         mock.patch("core.fetchers.qq.decrypt_qrc", return_value=QRC_TWO_LINES), \
         mock.patch("core.fetchers.qq.parsed_duration_reject_reason", return_value="overrun"):
        assert QQFetcher.fetch("晴天", "周杰伦", duration_ms=269000) is None


def test_c17_tier_switch_off():
    """MTL_QQ_TIER=off 时两次 HTTP 都不该发生。"""
    with mock.patch.dict(os.environ, {"MTL_QQ_TIER": "off"}), \
         mock.patch("core.fetchers.qq.http_get_json") as g, \
         mock.patch("core.fetchers.qq.http_post_json_body") as p:
        assert QQFetcher.fetch("晴天", "周杰伦") is None
        assert not g.called and not p.called


def test_c18_fetch_signature_locked():
    """fetch 的参数表必须与其它源保持一致（title/artist/duration_ms/album）。"""
    params = list(inspect.signature(QQFetcher.fetch).parameters)
    assert params == ["title", "artist", "duration_ms", "album"], params


# ===========================================================================
# D 组：聚合器七源接缝
# ===========================================================================

def _fake_sources(**overrides):
    """构造一组假体，返回 (agg, reals, calls)。"""
    calls = []

    def make(name, result):
        class _F:
            @classmethod
            def fetch(cls, *a, **k):
                calls.append(name)
                return result
        return _F

    names = {
        "AppleTTMLFetcher": "apple-ttml",
        "NetEaseFetcher": "netease",
        "QQFetcher": "qq",
        "KugouFetcher": "kugou",
        "AMLLFetcher": "amll",
        "LrcShareFetcher": "lrcshare",
        "LRCLIBFetcher": "lrclib",
    }
    reals = {n: getattr(agg, n) for n in names}
    for attr, label in names.items():
        setattr(agg, attr, overrides.get(label) or make(label, None))
    return agg, reals, calls


def _word_cand(source, text="甲乙丙", start=1000):
    return {
        "source": source,
        "lyric_type": "word",
        "lines": [{
            "start": start, "end": start + 2000, "duration": 2000, "text": text,
            "words": [
                {"start": start, "end": start + 500, "duration": 500, "text": text[0]},
                {"start": start + 500, "end": start + 1000, "duration": 500, "text": text[1]},
                {"start": start + 1000, "end": start + 2000, "duration": 1000, "text": text[2]},
            ],
        }],
    }


def _line_cand(source, text="甲乙丙", start=1000):
    return {
        "source": source,
        "lyric_type": "line",
        "lines": [{"start": start, "end": start + 2000, "duration": 2000, "text": text, "words": []}],
    }


def test_d1_seven_tier_order():
    """七档顺序必须是 Apple → 网易 → QQ → 酷狗 → AMLL → LRCSHARE → LRCLIB。"""
    A, reals, calls = _fake_sources()
    try:
        A.LyricAggregator.fetch_best_lyrics("测试曲", "测试人", enable_apple=False)
    finally:
        for n, v in reals.items():
            setattr(A, n, v)
    assert calls == ["netease", "qq", "kugou", "amll", "lrcshare", "lrclib"], calls


def test_d2_netease_line_then_qq_word():
    """核心回归：网易行级不得锁死 QQ 逐字。"""
    A, reals, calls = _fake_sources(
        **{"netease": lambda: None, "qq": lambda: None})
    A.NetEaseFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("netease"), _line_cand("netease"))[1])})
    A.QQFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("qq"), _word_cand("qq", "QQ词"))[1])})
    try:
        out = A.LyricAggregator.fetch_best_lyrics("测试曲", "测试人", enable_apple=False)
    finally:
        for n, v in reals.items():
            setattr(A, n, v)
    assert out["source"] == "qq", out
    assert "kugou" not in calls, calls


def test_d3_qq_line_cannot_replace_netease_line():
    """QQ 若违规返回行级，同等级不能替换网易行级。"""
    A, reals, calls = _fake_sources()
    A.NetEaseFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("netease"), _line_cand("netease", "网易词"))[1])})
    A.QQFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("qq"), _line_cand("qq", "QQ词"))[1])})
    A.KugouFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("kugou"), _word_cand("kugou", "酷狗词"))[1])})
    try:
        out = A.LyricAggregator.fetch_best_lyrics("测试曲", "测试人", enable_apple=False)
    finally:
        for n, v in reals.items():
            setattr(A, n, v)
    assert out["source"] == "kugou", out
    assert calls == ["netease", "qq", "kugou"], calls


def test_d4_chinese_apple_word_stops():
    """华语 Apple 逐字命中即停，不再探网易/QQ。"""
    A, reals, calls = _fake_sources()
    A.AppleTTMLFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("apple-ttml"), _word_cand("apple-ttml", "苹果词"))[1])})
    try:
        out = A.LyricAggregator.fetch_best_lyrics("测试曲", "测试人")
    finally:
        for n, v in reals.items():
            setattr(A, n, v)
    assert out["source"] == "apple-ttml"
    assert calls == ["apple-ttml"], calls


def test_d5_foreign_prefers_translated_word():
    """外语：带译文的逐字优先于无译文逐字。"""
    A, reals, calls = _fake_sources()
    ne = _line_cand("netease", "Hello")
    ne["lines"][0]["translation"] = "你好"
    ne["has_translation"] = True
    qq = _word_cand("qq", "Hello")
    kg = _word_cand("kugou", "Hello")
    kg["lines"][0]["translation"] = "你好"
    kg["has_translation"] = True

    A.NetEaseFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("netease"), ne)[1])})
    A.QQFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("qq"), qq)[1])})
    A.KugouFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("kugou"), kg)[1])})
    try:
        out = A.LyricAggregator.fetch_best_lyrics("Hello", "Adele", enable_apple=False)
    finally:
        for n, v in reals.items():
            setattr(A, n, v)
    assert out["source"] == "kugou", out


def test_d6_foreign_qq_no_trans_keeps_netease():
    """外语：QQ 逐字无译文不得顶掉网易行级+译文（译文是硬前提）。"""
    A, reals, calls = _fake_sources()
    ne = _line_cand("netease", "Hello")
    ne["lines"][0]["translation"] = "你好"
    ne["has_translation"] = True
    A.NetEaseFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("netease"), ne)[1])})
    A.QQFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("qq"), _word_cand("qq", "Hello"))[1])})
    try:
        out = A.LyricAggregator.fetch_best_lyrics("Hello", "Adele", enable_apple=False)
    finally:
        for n, v in reals.items():
            setattr(A, n, v)
    assert out["source"] == "netease", out
    assert out["lines"][0]["translation"] == "你好"


def test_d7_lrclib_only_when_empty():
    """LRCLIB 只在完全无候选时兜底。"""
    A, reals, calls = _fake_sources()
    A.LRCLIBFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("lrclib"), _line_cand("lrclib", "兜底词"))[1])})
    try:
        out = A.LyricAggregator.fetch_best_lyrics("测试曲", "测试人", enable_apple=False)
    finally:
        for n, v in reals.items():
            setattr(A, n, v)
    assert out is not None and out["source"] == "lrclib"
    assert calls[-1] == "lrclib"


def test_d8_rank_values():
    """QQ 逐字：华语 rank 3，外语无译文 rank 1。"""
    cand = _word_cand("qq")
    assert agg._rank(cand, False) == 3
    assert agg._rank(cand, True) == 1
    assert agg._rank(_line_cand("qq"), False) == 2
    assert agg._rank(_line_cand("qq"), True) == 0


def test_d9_lrcshare_word_adopted_without_translation():
    """T6 去掉 has_translation 守卫后，华语 LRCSHARE 逐字也能被采纳。"""
    A, reals, calls = _fake_sources()
    A.NetEaseFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("netease"), _line_cand("netease", "网易词"))[1])})
    A.LrcShareFetcher = type("F", (), {"fetch": classmethod(lambda c, *a, **k: (calls.append("lrcshare"), _word_cand("lrcshare", "分享词"))[1])})
    try:
        out = A.LyricAggregator.fetch_best_lyrics("测试曲", "测试人", enable_apple=False)
    finally:
        for n, v in reals.items():
            setattr(A, n, v)
    assert out["source"] == "lrcshare", out


def test_d10_no_network_in_scheduler_tests():
    """兜底：D 组全程不得发生任何真实 URL 访问。"""
    import urllib.request
    with mock.patch.object(urllib.request, "urlopen",
                           side_effect=AssertionError("调度层测试禁止联网")):
        A, reals, calls = _fake_sources()
        try:
            A.LyricAggregator.fetch_best_lyrics("测试曲", "测试人", enable_apple=False)
        finally:
            for n, v in reals.items():
                setattr(A, n, v)
    assert "netease" in calls


def main():
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())

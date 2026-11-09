# -*- coding: utf-8 -*-
"""泄漏守卫 H02 验收：三模式私有词表、fail-closed、占位符混行、脱敏输出。

全部使用**合成词表与虚构值**，公开 CI 不需要任何真实私有配置。
"""

import io
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import _testkit
except ImportError:  # pragma: no cover
    from tests import _testkit  # type: ignore

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

sys.path.insert(0, str(REPO_ROOT))
from scripts import check_no_leaks as guard  # noqa: E402

SCRIPT = REPO_ROOT / "scripts" / "check_no_leaks.py"

# 合成值：公开、可签发，与任何真实环境无关
SYNTH_SEG = "synthprivcorpus"          # 路径段型
SYNTH_LIT = "Synth Private Label"      # 高辨识度字面
SYNTH_TOK = "synth.acct.priv01"            # 标识型（非邮箱，避免与通用 EMAIL 规则重叠）
SYNTH_SHORT = "xy"                     # <3 字符，必须显式审核

# 永不命中的哨兵：运行时拼接，保证本文件自身不含任何泄漏形态的字面量
NEVER = "".join(["nvr", "sentinel", "9f3a"])
_BS2 = chr(92) * 2
# 合成 PII 样本（同样运行时拼接，避免本文件被自己的通用规则命中）
SYNTH_EMAIL = "someone.personal@" + "realmail" + "-host.test"
SYNTH_DOUBLE_BS_PATH = "C:" + chr(92) + "ffmpeg" + chr(92) + "bin"
SYNTH_SAFE_TOOL = (
    "C:" + chr(92) + "Program" + "Data" + chr(92) + "choco" + "la" + "tey" + chr(92) + "bin"
)


def _run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )


def _tmp_file(text: str, name: str = "fp.txt") -> Path:
    d = Path(tempfile.mkdtemp(prefix="mtl_guard_"))
    p = d / name
    p.write_text(text, encoding="utf-8", newline="\n")
    return p


def _tmp_zip(members: dict[str, str]) -> Path:
    d = Path(tempfile.mkdtemp(prefix="mtl_guard_zip_"))
    p = d / "probe.zip"
    with zipfile.ZipFile(p, "w") as zf:
        for name, body in members.items():
            zf.writestr(name, body)
    return p


# --------------------------------------------------------------------------- #
# 1. fail-closed：私有配置缺失 / 不存在 / 为空 / 语法非法 / 短词未审核
# --------------------------------------------------------------------------- #
def test_require_private_without_config_raises():
    """可信模式下完全没有配置必须抛错，不得静默跳过。"""
    with patch.object(guard, "_resolve_private_path", return_value=(None, "")):
        try:
            guard.load_private_rules("", require_private=True)
        except guard.PrivateConfigError as exc:
            assert "未配置私有词表" in str(exc), str(exc)
        else:
            raise AssertionError("缺配置未 fail-closed")


def test_private_file_missing_empty_invalid_fail():
    """不存在 / 空 / 语法非法的词表在可信模式下均非零退出。"""
    missing = _tmp_file("literal:placeholder\n").parent / "nope.txt"
    proc = _run("--require-private", "--private-file", str(missing))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "不存在" in proc.stderr

    empty = _tmp_file("# only comments\n\n")
    proc = _run("--require-private", "--private-file", str(empty))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "不含任何有效词条" in proc.stderr

    bad = _tmp_file("audited-nosuchmode:value\n")
    proc = _run("--require-private", "--private-file", str(bad))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "模式非法" in proc.stderr, proc.stderr


def test_short_entry_requires_explicit_audit():
    """<3 字符条目未显式审核时可信模式失败，非可信模式跳过并告警。"""
    short = _tmp_file(f"literal:{SYNTH_SHORT}\n")
    proc = _run("--require-private", "--private-file", str(short), "--zip", str(_tmp_zip({})))
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "未经审核的短词条" in proc.stderr

    audited = _tmp_file(f"audited-literal:{SYNTH_SHORT}\n")
    proc = _run("--require-private", "--private-file", str(audited), "--zip", str(_tmp_zip({})))
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# 2. 三种匹配模式各按各自边界工作
# --------------------------------------------------------------------------- #
def test_segment_mode_bounded_match():
    """segment 只在路径段边界命中，长标识内部不误报。"""
    fp = _tmp_file(f"segment:{SYNTH_SEG}\n")
    hit = _tmp_zip({"a.md": f"- 数据源 `<workspace>/{SYNTH_SEG}/music`\n"})
    proc = _run("--private-file", str(fp), "--zip", str(hit))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "PRIVATE_SEGMENT" in proc.stdout

    miss = _tmp_zip({"a.md": f"- prefix{SYNTH_SEG}suffix 不是独立段\n"})
    proc = _run("--private-file", str(fp), "--zip", str(miss))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_literal_mode_matches_non_path_context():
    """literal 必须能命中非路径上下文（正文里的专有名称）。"""
    fp = _tmp_file(f"literal:{SYNTH_LIT}\n")
    probe = _tmp_zip({"report.md": f"本次审计覆盖 {SYNTH_LIT} 的全部曲目。\n"})
    proc = _run("--private-file", str(fp), "--zip", str(probe))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "PRIVATE_LITERAL" in proc.stdout


def test_token_mode_bounded_match():
    """token 命中邮箱型标识，前后贴字母时不算独立标识。"""
    fp = _tmp_file(f"token:{SYNTH_TOK}\n")
    hit = _tmp_zip({"cfg.json": '{"contact": "' + SYNTH_TOK + '"}\n'})
    proc = _run("--private-file", str(fp), "--zip", str(hit))
    assert proc.returncode == 1, proc.stdout + proc.stderr

    miss = _tmp_zip({"cfg.json": '{"contact": "x' + SYNTH_TOK + '"}\n'})
    proc = _run("--private-file", str(fp), "--zip", str(miss))
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# 3. 占位符只遮蔽本身，不整行放行（原缺陷回归）
# --------------------------------------------------------------------------- #
def test_placeholder_line_still_scanned():
    """同一行同时含占位符与私有段时，必须仍然拦截。"""
    fp = _tmp_file(f"segment:{SYNTH_SEG}\n")
    probe = _tmp_zip({"doc.md": f"缓存位于 `<workspace>/{SYNTH_SEG}/hf-cache`。\n"})
    proc = _run("--private-file", str(fp), "--zip", str(probe))
    assert proc.returncode == 1, "占位符混行被整体放行了（原 H02 缺陷）"
    assert "PRIVATE_SEGMENT" in proc.stdout


def test_placeholder_only_line_allowed():
    """纯占位符行不应误报。"""
    fp = _tmp_file(f"segment:{SYNTH_SEG}\n")
    probe = _tmp_zip({"doc.md": "缓存位于 `<workspace>/hf-cache`，母盘 `<mother-library>/`。\n"})
    proc = _run("--private-file", str(fp), "--zip", str(probe))
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# 4. 通用规则仍然生效
# --------------------------------------------------------------------------- #
def test_generic_drive_path_and_email_detected():
    """通用盘符路径与个人邮箱在无私有词表时也必须被拦。"""
    probe = _tmp_zip(
        {
            "a.md": "路径 " + "Q:" + chr(92) + "somewhere" + chr(92) + "deep" + chr(92) + "f.flac\n",
            "b.md": "联系 " + SYNTH_EMAIL + "\n",
        }
    )
    proc = _run("--private-file", str(_tmp_file("literal:" + NEVER + "\n")), "--zip", str(probe))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "DRIVE_PATH" in proc.stdout
    assert "EMAIL" in proc.stdout


def test_safe_examples_not_flagged():
    """示例邮箱与白名单工具路径不应误报。"""
    probe = _tmp_zip(
        {
            "a.md": "contact " + chr(34) + "test@example.com" + chr(34) + "\n",
            "b.md": "探测顺序 " + SYNTH_SAFE_TOOL + "\n",
            "c.py": "root = " + SYNTH_DOUBLE_BS_PATH + "\n",
        }
    )
    proc = _run("--private-file", str(_tmp_file("literal:" + NEVER + "\n")), "--zip", str(probe))
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --------------------------------------------------------------------------- #
# 5. 输出脱敏：不回显私有值、不回显整行原文
# --------------------------------------------------------------------------- #
def test_output_redacts_private_values():
    """命中输出不得包含私有词条原文。"""
    fp = _tmp_file(f"literal:{SYNTH_LIT}\n")
    probe = _tmp_zip({"x.md": f"标题包含 {SYNTH_LIT} 字样\n"})
    proc = _run("--private-file", str(fp), "--zip", str(probe))
    assert proc.returncode == 1
    assert SYNTH_LIT not in proc.stdout, "输出回显了私有词条原文"
    assert "PRIVATE_LITERAL" in proc.stdout
    assert "标题包含" not in proc.stdout


def test_zip_guard_file_is_not_exempt():
    """同名脚本不享受 basename 例外，隐私规则必须覆盖它自己的内容。"""
    probe = _tmp_zip({
        "scripts/check_no_leaks.py": "payload = '" + SYNTH_LIT + "'\n",
        "deep/check_no_leaks.py": "payload = '" + SYNTH_LIT + "'\n",
    })
    proc = _run("--require-private", "--private-file", str(_tmp_file("literal:" + SYNTH_LIT + "\n")), "--zip", str(probe))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert proc.stdout.count("PRIVATE_LITERAL") >= 2, proc.stdout


def test_private_directory_in_zip_member_name_blocked():
    fp = _tmp_file("segment:" + SYNTH_SEG + "\n")
    z = _tmp_zip({"root/" + SYNTH_SEG + "/clean.md": "normal content\n"})
    result = _run("--require-private", "--private-file", str(fp), "--zip", str(z))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "PRIVATE_SEGMENT" in result.stdout
    assert SYNTH_SEG not in result.stdout


def test_private_filename_in_zip_member_name_blocked():
    fp = _tmp_file("literal:" + SYNTH_LIT + "\n")
    z = _tmp_zip({"normal/" + SYNTH_LIT + ".txt": "normal content\n"})
    result = _run("--require-private", "--private-file", str(fp), "--zip", str(z))
    assert result.returncode == 1, result.stdout + result.stderr
    assert SYNTH_LIT not in result.stdout


def test_private_name_in_tsv_content_blocked():
    fp = _tmp_file("literal:" + SYNTH_LIT + "\n")
    z = _tmp_zip({"core/data/sample.tsv": "item\t" + SYNTH_LIT + "\n"})
    result = _run("--require-private", "--private-file", str(fp), "--zip", str(z))
    assert result.returncode == 1, result.stdout + result.stderr


def test_private_name_in_unknown_utf8_content_blocked():
    fp = _tmp_file("literal:" + SYNTH_LIT + "\n")
    z = _tmp_zip({"core/data/sample.custom": "item=" + SYNTH_LIT + "\n"})
    result = _run("--require-private", "--private-file", str(fp), "--zip", str(z))
    assert result.returncode == 1, result.stdout + result.stderr


def test_sensitive_name_in_unknown_binary_fail_closed():
    fp = _tmp_file("literal:" + NEVER + "\n")
    z = _tmp_zip({"cache/data.anything": "xx\x00yy"})
    result = _run("--require-private", "--private-file", str(fp), "--zip", str(z))
    assert result.returncode == 1
    assert "UNKNOWN_BINARY_MEMBER" in result.stdout


def test_high_risk_file_type_rejected_even_if_content_empty():
    fp = _tmp_file("literal:" + NEVER + "\n")
    z = _tmp_zip({"secrets/private.pem": "not really secret\n"})
    result = _run("--require-private", "--private-file", str(fp), "--zip", str(z))
    assert result.returncode == 1
    assert "HIGH_RISK_FILENAME" in result.stdout


def test_clean_zip_and_no_raw_path_in_log():
    fp = _tmp_file("literal:" + SYNTH_LIT + "\n")
    z = _tmp_zip({"docs/safe.md": "safe\n", "data/normal.xyz": "hello\n"})
    result = _run("--require-private", "--private-file", str(fp), "--zip", str(z))
    assert result.returncode == 0, result.stdout + result.stderr


# --------------------------------------------------------------------------- #
# 6. 真实仓库在可信模式下干净
# --------------------------------------------------------------------------- #
def test_repo_scan_generic_clean():
    """真实仓库用合成词表做通用规则扫描必须干净。"""
    proc = _run("--private-file", str(_tmp_file("literal:" + NEVER + "\n")))
    assert proc.returncode == 0, proc.stdout[-2000:]


def main():
    """统一三态 runner。"""
    return _testkit.run(globals())


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""路径与个人信息泄漏守卫（H02）。

在公开仓库与发布包中拦截本机环境信息：绝对路径、用户名、私有目录名、个人邮箱。

用法：
    python scripts/check_no_leaks.py                      # 扫描仓库（git 索引优先）
    python scripts/check_no_leaks.py --zip dist/x.zip      # 扫描发布包成员路径与正文
    python scripts/check_no_leaks.py --verbose            # 打印被遮蔽/放行的命中
    python scripts/check_no_leaks.py --private-file F     # 显式指定私有指纹词表
    python scripts/check_no_leaks.py --require-private    # 可信模式：私有配置缺失/非法即失败

退出码：0 = 干净；1 = 发现泄漏；2 = 用法或私有配置错误。

私有词表（H02）：
  * **公开源码不携带任何可还原的真实指纹**，真实词表只存在于维护者本地非公开位置；
  * 定位顺序：`--private-file` → 环境变量 `MTL_PRIVATE_FINGERPRINTS_FILE`
    → `git config --local mtl.privateFingerprintsFile`；
  * 每行一种模式，`#` 开头为注释，空行忽略：
        segment:<值>          路径段（在路径分隔符/引号/空白/行界处分词）
        literal:<值>          高辨识度完整专有名称或明确泄漏片段的字面匹配
        token:<值>            邮箱/账户型边界匹配
        audited-<mode>:<值>   显式审核通过的短词（<3 字符）或高误报条目
    旧格式（不带前缀的行）按 `segment` 处理并给出一次迁移提示；
  * `--require-private` 模式下：未设置、不可读、无有效词条、语法非法、
    存在未审核的短词，一律非零退出，绝不静默跳过；
  * 输出只含规则类别、成员名摘要和行号，不打印私有词条、路径或上下文。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO_ROOT = Path(__file__).resolve().parent.parent
SELF_REL = "scripts/check_no_leaks.py"

# ---- 通用规则（公开、不含任何真实私有值） ---------------------------------
# 文档占位符：**只遮蔽占位符本身**，不再整行放行（H02 第 4 条）
PLACEHOLDER_RE = re.compile(
    r"<(?:workspace|repo|hf-cache|user-home|mother-library|test-library|process-dir)>"
)
PLACEHOLDER_MASK = "\u0001PH\u0001"

# 盘符绝对路径（前面不能紧跟字母数字，避免误伤 "Source:\\s*(" 这类正则）
DRIVE_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z]:(?:\\|/)[^\s`'\"<>|*?]{2,}")

# 用户目录
USERPROFILE_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9_])[A-Za-z]:[\\/]{1,2}Users[\\/]{1,2}([A-Za-z0-9._-]+)"
)

# 邮箱（通用 PII）
EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# 仓库内仅允许的公共工具安装路径（与 core/acoustic_aligner.py 的 ffmpeg 探测一致）
SAFE_PREFIXES = (
    "c:" + chr(92) + "programdata" + chr(92) + "chocolatey" + chr(92) + "bin",
    "c:" + chr(92) + "ffmpeg" + chr(92) + "bin",
)
# 虚构盘符（测试用）
SAFE_DRIVES = {"z"}
# 用户目录白名单
SAFE_USERNAMES = {"fake", "example", "user", "public", "runner", "username"}
# 邮箱白名单（文档示例/占位）
SAFE_EMAIL_DOMAINS = {"example.com", "example.invalid", "example.org", "test.invalid"}

TEXT_EXTS = {
    ".py", ".md", ".json", ".yml", ".yaml", ".txt", ".csv", ".lrc", ".ttml",
    ".jsonl", ".ini", ".cfg", ".toml", ".sh", ".bat", ".ps1", ".svg", ".hex",
    ".tsv", ".xml", ".html", ".rst", ".properties", ".env", ".log", ".m3u", ".cue",
    ".gitignore", ".gitattributes",
}

# 私有词表定位失败时提示使用的配置键
PRIVATE_NAME_HINT = "mtl.privateFingerprintsFile"
PRIVATE_ENV_HINT = "MTL_PRIVATE_FINGERPRINTS_FILE"

# 路径段内的「词内字符」：字母数字（含中文）、下划线、连字符、点、@、% 、+
# 命中值两侧若仍是这些字符，说明只是某个更长标识的一部分，不算独立路径段
SEGMENT_WORD_EXTRA = set("_-.@%+")
# 嵌入型标识（邮箱/账号）的词内字符
TOKEN_WORD_EXTRA = set("._%+-@")

MIN_SAFE_ENTRY_LEN = 3

MODES = ("segment", "literal", "token")


class PrivateConfigError(RuntimeError):
    """私有配置缺失、不可读或语法非法。"""


# --------------------------------------------------------------------------- #
# 私有词表
# --------------------------------------------------------------------------- #
class PrivateRule:
    __slots__ = ("mode", "value", "audited", "index", "folded", "_path_segment_re")

    def __init__(self, mode: str, value: str, audited: bool, index: int):
        self.mode = mode
        self.value = value
        self.audited = audited
        self.index = index
        self.folded = value.casefold()
        self._path_segment_re = None

    @property
    def label(self) -> str:
        return f"PRIVATE_{self.mode.upper()}[{self.index}]"

    def matches(self, masked_line: str) -> bool:
        lowered = masked_line.casefold()
        if self.mode == "literal":
            return self.folded in lowered
        if self.mode == "token":
            return _bounded_match(lowered, self.folded, TOKEN_WORD_EXTRA)
        if _bounded_match(lowered, self.folded, SEGMENT_WORD_EXTRA):
            return True
        # P0/H02: 正文路径语法识别（前有路径/文本界，后接可选扩展名及路径/文本界）
        # 例：.../SYNTH_PRIVATE_DIR_97.txt 或 /test/SYNTH_PRIVATE_DIR_97.flac
        if self.mode == "segment":
            if self._path_segment_re is None:
                pat = (
                    r"(?:^|(?<=[\/\\\"\'\`\(\[\{\s:=]))"
                    + re.escape(self.folded)
                    + r"(?=(?:\.[a-zA-Z0-9_-]+)?(?:[\/\\\"\'\`\)\]\}\s,;:!?]|$))"
                )
                self._path_segment_re = re.compile(pat, re.IGNORECASE)
            return bool(self._path_segment_re.search(lowered))
        return False


def _is_word_char(ch: str, extra: set) -> bool:
    return ch.isalnum() or ch in extra


def _bounded_match(haystack: str, needle: str, extra_word_chars: set) -> bool:
    """needle 在 haystack 中出现，且两侧都不是「词内字符」。

    `extra_word_chars` 是除字母数字外仍算作词内部的额外字符集合。
    """
    if not needle:
        return False
    start = 0
    while True:
        pos = haystack.find(needle, start)
        if pos < 0:
            return False
        left = haystack[pos - 1] if pos > 0 else ""
        end = pos + len(needle)
        right = haystack[end] if end < len(haystack) else ""
        if (not left or not _is_word_char(left, extra_word_chars)) and (
            not right or not _is_word_char(right, extra_word_chars)
        ):
            return True
        start = pos + 1


def _read_private_file(path: Path) -> tuple[list[PrivateRule], list[str]]:
    """解析私有词表。返回 (规则, 告警)。语法非法抛 PrivateConfigError。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PrivateConfigError(f"无法读取私有词表（{type(exc).__name__}）") from exc

    rules: list[PrivateRule] = []
    warnings: list[str] = []
    legacy_seen = False

    for lineno, line in enumerate(raw.splitlines(), 1):
        # 仅去掉行尾换行与回车，不 strip 掉值中的空格/单引号（H02 第 3 条）
        entry = line.rstrip("\r\n").rstrip()
        if not entry.strip():
            continue
        if entry.lstrip().startswith("#"):
            continue

        audited = False
        mode = "segment"
        value: str | None = None

        head, sep, tail = entry.partition(":")
        if sep and head.strip().lower() in MODES:
            mode = head.strip().lower()
            value = tail
        elif sep and head.strip().lower().startswith("audited-"):
            raw_mode = head.strip().lower()[len("audited-"):]
            if raw_mode not in MODES:
                raise PrivateConfigError(f"私有关键词第 {lineno} 行模式非法（audited-{raw_mode}）")
            mode = raw_mode
            audited = True
            value = tail
        else:
            value = entry
            legacy_seen = True

        if value is None or not value.strip():
            raise PrivateConfigError(f"私有词表第 {lineno} 行缺少有效值")

        value = value.rstrip()
        if not value.strip():
            raise PrivateConfigError(f"私有词表第 {lineno} 行为空值")
        rules.append(PrivateRule(mode, value, audited, len(rules) + 1))

    if legacy_seen:
        warnings.append(
            "检测到旧格式（无模式前缀）词条，已按 segment 处理；"
            "请在可信本地迁移时显式标注 segment/literal/token 并复核。"
        )
    return rules, warnings


def _unaudited_short_rules(rules: list[PrivateRule]) -> list[PrivateRule]:
    return [r for r in rules if not r.audited and len(r.value.strip()) < MIN_SAFE_ENTRY_LEN]


def _resolve_private_path(cli_value: str) -> tuple[Path | None, str]:
    """返回 (路径, 来源说明)。"""
    if cli_value:
        return Path(cli_value).expanduser(), "--private-file"
    env_value = os.environ.get(PRIVATE_ENV_HINT, "").strip()
    if env_value:
        return Path(env_value).expanduser(), PRIVATE_ENV_HINT
    try:
        out = subprocess.check_output(
            ["git", "config", "--local", "--get", PRIVATE_NAME_HINT],
            cwd=str(REPO_ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        out = ""
    if out:
        return Path(out).expanduser(), f"git config {PRIVATE_NAME_HINT}"
    return None, ""


def load_private_rules(cli_value: str, *, require_private: bool) -> list[PrivateRule]:
    path, origin = _resolve_private_path(cli_value)
    if path is None:
        if require_private:
            raise PrivateConfigError(
                f"未配置私有词表（请设置 {PRIVATE_ENV_HINT} 或 git config --local {PRIVATE_NAME_HINT}）"
            )
        return []
    if not path.is_file():
        raise PrivateConfigError(f"私有词表不存在（来源 {origin}）")
    rules, warnings = _read_private_file(path)
    for msg in warnings:
        print(f"⚠️  {msg}", file=sys.stderr)
    if not rules:
        raise PrivateConfigError("私有词表不含任何有效词条")
    short = _unaudited_short_rules(rules)
    if short:
        detail = "、".join(f"#{r.index}({len(r.value.strip())}字符)" for r in short)
        if require_private:
            raise PrivateConfigError(
                f"存在未经审核的短词条 {detail}；请本地审核后用 audited-<mode>: 前缀显式启用"
            )
        print(f"⚠️  已跳过未审核短词条 {detail}", file=sys.stderr)
    return [r for r in rules if r not in short]


# --------------------------------------------------------------------------- #
# 扫描
# --------------------------------------------------------------------------- #
def _is_text(name: str) -> bool:
    base = os.path.basename(name)
    if base in (".gitignore", ".gitattributes"):
        return True
    return os.path.splitext(base)[1].lower() in TEXT_EXTS


# 文件名/目录名不可作为“文本后缀过滤”的前提，先对所有成员路径做检查。
# 未知后缀仅在证明是有限大小的文本后扫描；高风险的二进制/密文容器直接拒绝。
MAX_SCAN_BYTES = 16 * 1024 * 1024
HIGH_RISK_NAMES = {"id_rsa", "id_ed25519", "cookies.json", "cookies.sqlite"}
HIGH_RISK_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".env", ".zip", ".7z", ".rar", ".tar"}
KNOWN_BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".mp3", ".flac", ".m4a", ".wav", ".ogg", ".dsf", ".pdf"}


def _safe_label(name: str, prefix: str = "member") -> str:
    """用稳定摘要定位违规成员，避免路径名本身包含私人目录或邮箱时回显。"""
    return f"{prefix}#{hashlib.sha256(name.encode('utf-8', 'surrogatepass')).hexdigest()[:12]}"


def _high_risk_name(name: str) -> bool:
    base = name.replace(chr(92), "/").rsplit("/", 1)[-1].lower()
    return (base in HIGH_RISK_NAMES or base.startswith(".env") or
            base.startswith("cookies.") or Path(base).suffix in HIGH_RISK_SUFFIXES)


def _decode_scannable(payload: bytes, name: str) -> tuple[str | None, str | None]:
    """文本全部扫描，未知二进制标注不可审；不能把解码失败当作安全通过。"""
    if len(payload) > MAX_SCAN_BYTES:
        return None, "OVERSIZED_UNSCANNED_MEMBER"
    if b"\x00" in payload:
        if _is_text(name):
            return None, "TEXT_MEMBER_CONTAINS_NUL"
        if Path(name).suffix.lower() in KNOWN_BINARY_SUFFIXES:
            return None, None
        return None, "UNKNOWN_BINARY_MEMBER"
    try:
        return payload.decode("utf-8-sig", "strict"), None
    except UnicodeError:
        if Path(name).suffix.lower() in KNOWN_BINARY_SUFFIXES:
            return None, None
        # 未知扩展名/带文本扩展名但乱码都不应静默通过
        return None, "UNSCANNABLE_MEMBER"


def _matches_path_segment(path_str: str, needle_folded: str) -> bool:
    """检查 needle_folded 是否作为路径段或文件名 stem 在 path_str 中完整出现。"""
    norm = path_str.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    for p in parts:
        p_fold = p.casefold()
        if p_fold == needle_folded:
            return True
        if "." in p_fold:
            stem = p_fold.rsplit(".", 1)[0]
            if stem == needle_folded:
                return True
    return False


def _scan_member_name(name: str, rules: list[PrivateRule], hits: list, allowed: list, verbose: bool, prefix: str):
    label = _safe_label(name, prefix)
    normalized = name.replace(chr(92), "/")
    if _high_risk_name(normalized):
        hits.append((label, 0, "HIGH_RISK_FILENAME"))
    # P0/H02: 路径成员名称专用 segment 检查（覆盖目录段与文件名 stem，如 normal/SYNTH_PRIVATE_DIR_97.txt）
    for rule in rules:
        if rule.mode == "segment" and _matches_path_segment(normalized, rule.folded):
            hits.append((label, 0, rule.label))
            break
    # 路径整体扫描兼顾 private segment/literal/token 三类词条以及通用 PII。
    scan_text(normalized, label, rules, hits, allowed, verbose)
    return label


def _norm(p: str) -> str:
    return p.replace("/", "\\").lower()


def _path_allowed(p: str) -> bool:
    n = _norm(p)
    if n[:2] in {d + ":" for d in SAFE_DRIVES}:
        return True
    if any(n.startswith(prefix) for prefix in SAFE_PREFIXES):
        return True
    m = re.match(r"(?i)^[a-z]:[\\/]+users[\\/]+([a-z0-9._-]+)", n)
    if m and m.group(1) in SAFE_USERNAMES:
        return True
    return False


def _email_allowed(addr: str) -> bool:
    domain = addr.rsplit("@", 1)[-1].lower()
    return domain in SAFE_EMAIL_DOMAINS


def _mask_pii(line: str, rules: list[PrivateRule]) -> str:
    """把命中片段替换为占位符，保证输出不泄露原文。"""
    out = line
    for m in DRIVE_RE.finditer(line):
        if not _path_allowed(m.group(0)):
            out = out.replace(m.group(0), "<REDACTED-PATH>")
    for m in EMAIL_RE.finditer(line):
        if not _email_allowed(m.group(0)):
            out = out.replace(m.group(0), "<REDACTED-EMAIL>")
    for rule in rules:
        if rule.value and rule.matches(out):
            out = re.sub(re.escape(rule.value), "<REDACTED>", out, flags=re.IGNORECASE)
    return out[:160]


def scan_text(
    text: str, label: str, rules: list[PrivateRule], hits: list, allowed: list, verbose: bool
) -> None:
    for lineno, line in enumerate(text.splitlines(), 1):
        masked = PLACEHOLDER_RE.sub(PLACEHOLDER_MASK, line)

        if masked != line and verbose:
            allowed.append((label, lineno, "placeholder-masked"))

        for rule in rules:
            if rule.matches(masked):
                hits.append((label, lineno, rule.label))
                break
        else:
            for m in USERPROFILE_RE.finditer(masked):
                if m.group(1).lower() not in SAFE_USERNAMES:
                    hits.append((label, lineno, "USER_PROFILE"))
                    break
            else:
                for m in EMAIL_RE.finditer(masked):
                    if not _email_allowed(m.group(0)):
                        hits.append((label, lineno, "EMAIL"))
                        break
                else:
                    for m in DRIVE_RE.finditer(masked):
                        if not _path_allowed(m.group(0)):
                            hits.append((label, lineno, "DRIVE_PATH"))
                            break


def iter_repo_files():
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=str(REPO_ROOT), stderr=subprocess.DEVNULL
        )
        names = [n for n in out.decode("utf-8", "ignore").split("\0") if n]
        if names:
            return [REPO_ROOT / n for n in names]
    except Exception:
        pass
    files = []
    for root, dirs, names in os.walk(REPO_ROOT):
        dirs[:] = [d for d in dirs if d not in {".git", "dist", "__pycache__"}]
        for n in names:
            files.append(Path(root) / n)
    return files


def scan_repo(rules: list[PrivateRule], verbose: bool):
    hits, allowed = [], []
    for path in iter_repo_files():
        try:
            rel = path.relative_to(REPO_ROOT).as_posix()
        except ValueError:
            hits.append((_safe_label(str(path), "repo"), 0, "PATH_OUTSIDE_REPO"))
            continue
        label = _scan_member_name(rel, rules, hits, allowed, verbose, "repo")
        if path.is_symlink():
            hits.append((label, 0, "SYMLINK_MEMBER"))
            continue
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            if size > MAX_SCAN_BYTES:
                hits.append((label, 0, "OVERSIZED_UNSCANNED_MEMBER"))
                continue
            data = path.read_bytes()
        except OSError:
            hits.append((label, 0, "UNREADABLE_MEMBER"))
            continue
        content, issue = _decode_scannable(data, rel)
        if issue:
            hits.append((label, 0, issue))
        if content is not None:
            scan_text(content, label, rules, hits, allowed, verbose)
    return hits, allowed


def scan_zip(zip_path: Path, rules: list[PrivateRule], verbose: bool):
    hits, allowed = [], []
    try:
        with zipfile.ZipFile(zip_path) as zf:
            seen = set()
            for member in zf.infolist():
                name = member.filename
                label = _scan_member_name(name, rules, hits, allowed, verbose, "zip")
                normalized = name.replace(chr(92), "/")
                if normalized in seen:
                    hits.append((label, 0, "DUPLICATE_MEMBER"))
                seen.add(normalized)
                if (normalized.startswith("/") or any(part == ".." for part in normalized.split("/"))):
                    hits.append((label, 0, "UNSAFE_MEMBER_PATH"))
                    continue
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    hits.append((label, 0, "SYMLINK_MEMBER"))
                    continue
                if member.is_dir():
                    continue
                if member.file_size > MAX_SCAN_BYTES:
                    hits.append((label, 0, "OVERSIZED_UNSCANNED_MEMBER"))
                    continue
                try:
                    data = zf.read(member)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    hits.append((label, 0, "UNREADABLE_MEMBER"))
                    continue
                content, issue = _decode_scannable(data, name)
                if issue:
                    hits.append((label, 0, issue))
                if content is not None:
                    scan_text(content, label, rules, hits, allowed, verbose)
    except (OSError, RuntimeError, zipfile.BadZipFile):
        hits.append(("zip", 0, "INVALID_ARCHIVE"))
    return hits, allowed


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="路径与个人信息泄漏守卫（H02）")
    ap.add_argument("--zip", default="", help="扫描指定 zip 内的文本成员")
    ap.add_argument("--verbose", action="store_true", help="打印被遮蔽/放行的命中")
    ap.add_argument("--private-file", default="", help="私有指纹词表路径")
    ap.add_argument(
        "--require-private",
        action="store_true",
        help="可信模式：私有词表缺失/不可读/为空/非法/含未审核短词即失败",
    )
    args = ap.parse_args(argv)

    try:
        rules = load_private_rules(args.private_file, require_private=args.require_private)
    except PrivateConfigError as exc:
        print(f"❌ 私有配置错误: {exc}", file=sys.stderr)
        return 2

    if args.zip:
        target = Path(args.zip)
        if not target.is_file():
            print(f"ERROR: 找不到 {target}")
            return 2
        hits, allowed = scan_zip(target, rules, args.verbose)
        scope = "发布包"
    else:
        hits, allowed = scan_repo(rules, args.verbose)
        scope = "仓库"

    if args.verbose and allowed:
        print(f"ℹ️  已遮蔽占位符 {len(allowed)} 处")

    if not hits:
        suffix = f"，私有词条 {len(rules)} 条" if rules else "，私有词表未加载"
        print(f"✅ 泄漏守卫通过（{scope}）：未发现本机路径或个人信息{suffix}")
        return 0

    print(f"❌ 泄漏守卫失败（{scope}）：命中 {len(hits)} 处")
    for label, lineno, rule in hits:
        print(f"  {label}:{lineno}  [{rule}]")
    return 1


if __name__ == "__main__":
    sys.exit(main())

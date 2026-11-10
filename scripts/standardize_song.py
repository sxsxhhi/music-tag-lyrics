# -*- coding: utf-8 -*-
"""music-tag-lyrics 音频歌词标准化治理与双向审计工具 (v1.8.2)。

满足《歌曲检测与修补标准》§2.4、§3、§4、§6 全部要求：
1. 缺省 --mode audit 只读审计，绝无副作用；
2. 显式 --mode standardize 执行 R2 全曲制作名单清理 -> R3 A/B 时序排版 -> R4 双格式输出；
3. 输出严格 3 位毫秒 ELRC 与单行 iTunes XML TTML；
4. 独立运行 R1 ELRC ↔ TTML 双向 1ms/2ms 硬门禁；
5. 支持 FLAC/MP3/WAV 容器安全工作副本写入与磁盘物理回读校验；
6. 严格保持原有 process_song.py 默认入口兼容性。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import hashlib
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.container_tag_adapter import (
    read_container_tags,
    write_verified_standardized_container,
)
from core.auditors.elrc_ttml_comparator import compare_elrc_ttml, verify_canonical_exports, parse_ttml_structural
from core.cleaners.content_classifier import classify_and_clean_all_lines
from core.parsers.lrc_parser import parse_lrc
from core.parsers.ttml_parser import parse_ttml
from core.renderers.standard_elrc_renderer import render_elrc_v182
from core.renderers.standard_ttml_renderer import render_ttml_v182
from core.schedulers.prelude_scheduler import plan_prelude_ab
from core.version import __version__


def _read_ttml_for_standardization(raw: str) -> Dict[str, Any]:
    """The legacy TTML parser omits head translations. Preserve head/inline
    translation and original literal word gaps via the structural parser.
    """
    lines, _, issues = parse_ttml_structural(raw)
    if issues:
        raise ValueError("TTML_STRUCTURE_INVALID:" + ",".join(x.code for x in issues))
    if not lines:
        raise ValueError("TTML_NO_LINES")
    provenance = {}
    if 'Lyrics provided by lrc.red (https://lrc.red)' in raw:
        provenance['re'] = 'lrc.red (https://lrc.red)'
    converted=[]
    for ln in lines:
        # Previously generated P_TITLE/P_LYRICIST/P_COMPOSER spans are
        # synthetic display text, NOT a real acoustic singing word axis.
        wrds=[]
        for j,w in enumerate(ln.words):
            txt=w.text.rstrip(' ')
            if not txt:continue
            wrds.append({'text':txt,'start':w.start_ms,'end':w.end_ms,
                         'join':'space' if w.text.endswith(' ') else 'none'})
        if ln.is_prelude:
            wrds=[]
        converted.append({'start':ln.start_ms,'end':ln.end_ms,
                          'text':ln.text,'words':wrds,
                          'translation':ln.translation,
                          'semantic_role':ln.role})
    return {'lines':converted,'tags':provenance,'time_precision':3}


def _stage_sidecar(directory: str, name: str, content: str) -> str:
    fd,path=tempfile.mkstemp(prefix='.mtl182-stage-',suffix='.tmp',dir=directory)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
            f.write(content);f.flush();os.fsync(f.fileno())
    except BaseException:
        if os.path.exists(path):os.unlink(path)
        raise
    return path


def standardize_single_song(
    audio_path: str,
    mode: str = 'audit',
    output_dir: Optional[str] = None,
    write_tags: bool = False,
    no_tags: bool = False,
    in_place: bool = False,
    ref_lyrics_path: Optional[str] = None,
    report_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Audits in place but NEVER writes to the source audio or existing target.
    New outputs are private-staged then committed using non-clobbering links.
    """
    if mode not in ('audit','standardize'):raise ValueError('Unknown standardization mode')
    if no_tags and write_tags:raise ValueError('--no-tags conflicts with --write-tags')
    # Explicitly disabled until a separately verified multi-file transaction
    # protocol exists. No sidecar is created or overwritten by this branch.
    if in_place:raise ValueError('IN_PLACE_DISABLED: v1.8.2 cannot safely overwrite source/sidecars')
    audio_abs=os.path.abspath(audio_path)
    if not os.path.isfile(audio_abs):raise FileNotFoundError('Audio file not found')
    if report_path:
        report_abs = os.path.abspath(report_path)
        report_p = Path(report_abs)
        protected = {Path(audio_abs).resolve()}
        base = Path(audio_abs).with_suffix('')
        for ext in ('.lrc', '.spl', '.ttml'):
            p = base.with_suffix(ext)
            try:
                protected.add(p.resolve())
            except OSError:
                pass
        if ref_lyrics_path:
            protected.add(Path(ref_lyrics_path).resolve())
        dest_dir = Path(output_dir or os.path.dirname(audio_abs)).resolve()
        filename = Path(audio_abs).name
        stem = Path(audio_abs).stem
        for ext in ('.lrc', '.spl', '.ttml'):
            protected.add((dest_dir / (stem + ext)).resolve())
        protected.add((dest_dir / filename).resolve())

        try:
            report_resolved = report_p.resolve()
        except OSError:
            report_resolved = report_p

        if report_resolved in protected or report_p in protected:
            raise ValueError(f"REPORT_TARGET_PROTECTED: refusing to overwrite audio, lyrics, reference or target at {report_path}")
        for p in protected:
            if report_p == p or report_resolved == p:
                raise ValueError(f"REPORT_TARGET_PROTECTED: {report_path} conflicts with {p}")
            if os.path.lexists(p) and os.path.lexists(report_abs):
                try:
                    if os.path.samefile(p, report_abs):
                        raise ValueError(f"REPORT_TARGET_PROTECTED: samefile conflict with {p}")
                except OSError:
                    pass
        if os.path.lexists(report_abs):
            raise FileExistsError(f"REPORT_TARGET_EXISTS: Refusing to overwrite existing file at {report_path}")
    original_audio_hash=hashlib.sha256(Path(audio_abs).read_bytes()).hexdigest()
    embedded=read_container_tags(audio_abs)
    noext,_=os.path.splitext(audio_abs)
    def read_optional(f):
        return Path(f).read_text('utf-8') if Path(f).is_file() else ''
    disk_elrc=read_optional(noext+'.lrc') or read_optional(noext+'.spl')
    disk_ttml=read_optional(noext+'.ttml')
    active_elrc=embedded.get('lyrics') or disk_elrc
    active_ttml=embedded.get('ttml') or disk_ttml
    report={
        'version':__version__,'audio':audio_abs,'mode':mode,'status':'PASS',
        'has_embedded_elrc':bool(embedded.get('lyrics')),
        'has_embedded_ttml':bool(embedded.get('ttml')),
        'has_sidecar_elrc':bool(disk_elrc),'has_sidecar_ttml':bool(disk_ttml),
        'baseline_comparison':None,'format_audit':'NO_LYRICS',
        'issues':[],'prelude_plan':None,'actions_taken':[]}
    def issue(code,description='',blocking=True):
        report['issues'].append({'code':code,'message':description,'blocking':blocking})
        if blocking and report['status'] not in ('BLOCKED',):report['status']='REVIEW_REQUIRED'
    def finish():
        if report_path:
            try:
                _write_json_report(report_path, report)
            except Exception as exc:
                report['status'] = 'BLOCKED'
                issue('REPORT_WRITE_FAILED', f"{type(exc).__name__}: {str(exc)}")
        return report

    if active_elrc and active_ttml:
        baseline=compare_elrc_ttml(active_elrc,active_ttml)
        report['baseline_comparison']={'passed':baseline.passed,
            'max_time_delta_ms':baseline.max_time_delta_ms,
            'issues':[i.code for i in baseline.issues]}
        report['format_audit']='CONSISTENT' if baseline.passed else 'INCONSISTENT'
        if not baseline.passed:issue('INPUT_FORMATS_CONFLICT','Existing ELRC/TTML differ')
    elif active_elrc or active_ttml:
        report['format_audit']='ONE_FORMAT_ONLY'
        issue('ONE_FORMAT_ONLY','Cannot claim baseline cross-format consistency',False)
        if mode=='audit': report['status']='ONE_FORMAT_ONLY'
    else:
        report['status']='BLOCKED';issue('NO_LYRIC_INPUT','No lyrics found')
        return finish()
    if mode=='standardize' and report['status']=='REVIEW_REQUIRED':
        return finish()

    # Prefer the higher fidelity TTML, but do not silently swallow parse errors.
    try:
        parsed=_read_ttml_for_standardization(active_ttml) if active_ttml else parse_lrc(active_elrc)
        if not parsed.get('lines'):raise ValueError('No parsed lyric lines')
    except (ValueError,TypeError,KeyError) as exc:
        report['status']='BLOCKED';issue('INPUT_PARSE_FAILED',str(exc))
        return finish()
    # Preserve re: attribution even when TTML was preferred over ELRC.
    if active_elrc:
        lrc_tags=parse_lrc(active_elrc).get('tags',{})
        parsed.setdefault('tags',{}).update({k:v for k,v in lrc_tags.items()
                       if k in ('re','ti','ar') and v})
    parsed.setdefault('tags',{}).update({k:v for k,v in (
        ('title',embedded.get('title')),('artist',embedded.get('artist')),
        ('lyricist',embedded.get('lyricist')),('composer',embedded.get('composer')) )
                                              if v})

    reference=None
    if ref_lyrics_path:
        ref=Path(ref_lyrics_path)
        if not ref.is_file() or ref.suffix.lower() not in ('.lrc','.spl','.ttml'):
            report['status']='BLOCKED';issue('INVALID_REFERENCE','Reference must be a readable .lrc/.spl/.ttml file')
            return finish()
        try:
            raw=ref.read_text('utf-8')
            reference=_read_ttml_for_standardization(raw) if ref.suffix.lower()=='.ttml' else parse_lrc(raw)
        except (UnicodeError,ValueError,TypeError) as exc:
            report['status']='BLOCKED';issue('REFERENCE_PARSE_FAILED',str(exc))
            return finish()
    canonical=classify_and_clean_all_lines(parsed,ref_lyrics_data=reference,
        song_title=embedded.get('title',''),song_artist=embedded.get('artist',''),
        audio_path=audio_abs)
    plan=plan_prelude_ab(canonical)
    report['prelude_plan']={'mode':plan.mode,'t_sing_ms':plan.t_sing_ms,
        'title_window':plan.title_window,'lyricist_window':plan.lyricist_window,
        'composer_window':plan.composer_window,
        'display_title':plan.display_title,'display_lyricist':plan.display_lyricist,
        'display_composer':plan.display_composer}
    # Merge issues after *all* planning steps. No permission flag bypasses them.
    report['issues'].extend(i.to_dict() for i in canonical.issues)
    if any(i.blocking or i.severity.value=='REVIEW_REQUIRED' for i in canonical.issues):
        report['status']='REVIEW_REQUIRED'
    if mode=='audit':
        if report['format_audit'] == 'ONE_FORMAT_ONLY' and report['status'] != 'BLOCKED':
            report['status'] = 'ONE_FORMAT_ONLY'
        return finish()

    if not output_dir:raise ValueError('--output-dir required for v1.8.2 standardize')
    dest=Path(output_dir).resolve()
    if dest==Path(audio_abs).parent.resolve():
        raise ValueError('TARGET_IN_SOURCE_DIRECTORY: choose isolated output directory')
    filename=Path(audio_abs).name;stem=Path(audio_abs).stem
    sidecars={dest/(stem+'.lrc'):None,dest/(stem+'.spl'):None,dest/(stem+'.ttml'):None}
    target_audio=dest/filename if write_tags else None
    all_targets=list(sidecars)+( [target_audio] if target_audio else [] )
    if any(os.path.lexists(x) for x in all_targets):
        report['status']='BLOCKED';issue('TARGET_EXISTS','Existing destination is never overwritten')
        return finish()

    if report['status']!='PASS':
        report['actions_taken'].append('ABORT_DUE_TO_BLOCKING_ISSUES')
        return finish()
    try:
        elrc=render_elrc_v182(canonical)
        ttml=render_ttml_v182(canonical)
        check=verify_canonical_exports(canonical,elrc,ttml)
        if not check.passed:
            report['status']='BLOCKED'
            issue('R1_CANONICAL_GENERATION_FAILED',','.join(i.code for i in check.issues))
            return finish()
    except (ValueError,TypeError) as exc:
        report['status']='BLOCKED';issue('CANONICAL_RENDER_FAILED',str(exc))
        return finish()
    dest.mkdir(parents=True,exist_ok=True)
    sidecars[dest/(stem+'.lrc')]=elrc
    sidecars[dest/(stem+'.spl')]=elrc
    sidecars[dest/(stem+'.ttml')]=ttml
    staged={};committed=[]
    try:
        for dst,content in sidecars.items():
            staged[dst]=_stage_sidecar(str(dest),dst.name,content)
            if Path(staged[dst]).read_text('utf-8')!=content:
                raise RuntimeError('STAGED_SIDECAR_READBACK_FAILED')
        if write_tags:
            # Container helper independently stages, verifies and commits;
            # it NEVER writes to an occupied target.
            cr=write_verified_standardized_container(audio_abs,str(target_audio),elrc,ttml,
                  lyricist=canonical.song.get('extracted_lyricist'),
                  composer=canonical.song.get('extracted_composer'))
            report['container_verification']=cr
            committed.append(target_audio)
        if hashlib.sha256(Path(audio_abs).read_bytes()).hexdigest()!=original_audio_hash:
            raise RuntimeError('SOURCE_MODIFIED_DURING_STANDARDIZATION')
        for dst,tmp in staged.items():
            os.link(tmp,dst)
            committed.append(dst)
        report['actions_taken']=['WRITE_SIDECAR_LRC','WRITE_SIDECAR_SPL','WRITE_SIDECAR_TTML']
        if write_tags:report['actions_taken'].append('WRITE_VERIFIED_CONTAINER_TAGS')
    except (OSError,RuntimeError,ValueError) as exc:
        # Roll back only files this process just committed, NEVER preexisting.
        for dst in reversed(committed):
            try:os.unlink(dst)
            except OSError:pass
        report['status']='BLOCKED';issue('TRANSACTION_FAILED',type(exc).__name__+': '+str(exc))
    finally:
        for tmp in staged.values():
            if os.path.exists(tmp):os.unlink(tmp)
    return finish()


def _write_json_report(report_path: str, data: Dict[str, Any]) -> None:
    report_abs = os.path.abspath(report_path)
    if os.path.lexists(report_abs):
        raise FileExistsError(f"REPORT_TARGET_EXISTS: Refusing to overwrite existing file at {report_path}")
    rep_dir = os.path.dirname(report_abs)
    if rep_dir:
        os.makedirs(rep_dir, exist_ok=True)
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp_report_", dir=rep_dir)
    try:
        with open(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        with open(tmp_path, "r", encoding="utf-8") as f:
            _ = json.load(f)
        if os.path.lexists(report_abs):
            raise FileExistsError(f"REPORT_TARGET_EXISTS: Refusing to overwrite existing file at {report_path}")
        os.link(tmp_path, report_abs)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser(
        description=f"music-tag-lyrics 音频歌词标准化治理与双向审计工具 (v{__version__})"
    )
    parser.add_argument("audio", help="待处理的音频文件路径")
    parser.add_argument(
        "--mode",
        choices=["audit", "standardize"],
        default="audit",
        help="运行模式：audit（默认只读审计）或 standardize（标准化修补与导出）",
    )
    parser.add_argument("--output-dir", help="输出目录（用于保存 sidecar 或生成的工作副本）")
    parser.add_argument(
        "--no-tags",
        action="store_true",
        help="仅生成 sidecar 歌词文件，不修改音频标签",
    )
    parser.add_argument(
        "--write-tags",
        action="store_true",
        help="向目标工作副本写入双格式歌词与词曲标签并执行磁盘回读校验",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="明确授权原地修改（高风险，需严格满足前置安全门禁）",
    )
    parser.add_argument("--ref-lyrics", help="显式指定的只读参考歌词路径（用于角色恢复等）")
    parser.add_argument("--report", help="JSON 审计报告输出路径")

    args = parser.parse_args()

    try:
        res = standardize_single_song(
            audio_path=args.audio,
            mode=args.mode,
            output_dir=args.output_dir,
            write_tags=args.write_tags,
            no_tags=args.no_tags,
            in_place=args.in_place,
            ref_lyrics_path=args.ref_lyrics,
            report_path=args.report,
        )
        print(f"[{res['status']}] {args.audio}")
        if res.get("prelude_plan"):
            p = res["prelude_plan"]
            print(f"  前奏规划: 模式={p['mode']}, 首唱T={p['t_sing_ms']}ms, 标题={p['title_window']}")
        if res.get("actions_taken"):
            print(f"  执行动作: {', '.join(res['actions_taken'])}")
        if res.get("issues"):
            print(f"  审计告警 ({len(res['issues'])} 项):")
            for iss in res["issues"][:5]:
                print(f"    - [{iss.get('severity', 'INFO')}] {iss.get('code')}: {iss.get('evidence') or iss.get('message')}")
        sys.exit(0 if res["status"] == "PASS" else 1)
    except Exception as exc:
        print(f"[ERROR] 执行失败: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

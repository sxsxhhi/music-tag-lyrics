# -*- coding: utf-8 -*-
"""music-tag-lyrics 音频歌词标准化治理与双向审计工具 (v1.8.4)。

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
import re
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.adapters.container_tag_adapter import (
    read_container_tags,
    write_verified_standardized_container,
)
from core.auditors.elrc_ttml_comparator import (
    compare_elrc_ttml, verify_canonical_exports,
    parse_ttml_structural, parse_elrc_structural,
)
from core.cleaners.content_classifier import classify_and_clean_all_lines
from core.models.canonical_lyrics import IssueSeverity
from core.parsers.lrc_parser import parse_lrc
from core.parsers.ttml_parser import parse_ttml
from core.renderers.standard_elrc_renderer import render_elrc_v182
from core.renderers.standard_ttml_renderer import render_ttml_v182
from core.schedulers.prelude_scheduler import plan_prelude_ab
from core.utils.io_safe import sha256_file
from core.version import __version__


def _read_ttml_for_standardization(raw: str) -> Dict[str, Any]:
    """The legacy TTML parser omits head translations. Preserve head/inline
    translation and original literal word gaps via the structural parser.
    """
    lines, _, issues = parse_ttml_structural(raw)
    blocking_issues = [item for item in issues if item.blocking]
    if blocking_issues:
        raise ValueError("TTML_STRUCTURE_INVALID:" + ",".join(x.code for x in blocking_issues))
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
            trailing = getattr(w, 'trailing_space', '')
            has_space = bool(w.text.endswith(' ') or (trailing and not trailing.strip()))
            wrds.append({'text':txt,'start':w.start_ms,'end':w.end_ms,
                         'join':'space' if has_space else 'none'})
        if ln.is_prelude:
            wrds=[]
        converted.append({'start':ln.start_ms,'end':ln.end_ms,
                          'text':ln.text,'words':wrds,
                          'translation':ln.translation,
                          'semantic_role':ln.role})
    return {'lines':converted,'tags':provenance,'time_precision':3,
            'ttml_structure_notices': [dict(code=i.code, message=i.message, blocking=False)
                                       for i in issues if not i.blocking]}


def _stage_sidecar(directory: str, name: str, content: str) -> str:
    fd,path=tempfile.mkstemp(prefix='.mtl182-stage-',suffix='.tmp',dir=directory)
    try:
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
            f.write(content);f.flush();os.fsync(f.fileno())
    except BaseException:
        if os.path.exists(path):os.unlink(path)
        raise
    return path


def _public_inventory_item(item: Dict[str, Any], index: int) -> Dict[str, Any]:
    """No ID3 description/hash key, potentially user-authored text, is public."""
    desc = str(item.get('desc', ''))
    key = str(item.get('key', ''))
    lang = str(item.get('lang', ''))
    return {
        'container': item.get('container', ''),
        'kind': item.get('kind', ''),
        'key': f"track[{index}]",
        'key_sha256': hashlib.sha256(key.encode('utf-8')).hexdigest(),
        'lang': lang if re.fullmatch(r'[a-zA-Z]{3}', lang) else '',
        'desc': '',
        'desc_sha256': hashlib.sha256(desc.encode('utf-8')).hexdigest(),
        'sha256': item.get('sha256', ''),
        'bytes': item.get('bytes', 0),
    }


def _public_canonical_issue(event: Any) -> Dict[str, Any]:
    """Strict DTO: evidence and free-form action/line-id may contain lyrics."""
    result = event.to_dict()
    return {
        'code': result['code'],
        'severity': result['severity'],
        'line_id': None,
        'source_index': result['source_index'],
        'evidence': '',
        'action': (result['action'] if re.fullmatch(r'[A-Z0-9_]+', result['action'] or '')
                   else ''),
        'blocking': result['blocking'],
    }


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
        parent = report_p.parent
        while not parent.exists() and parent != parent.parent:
            parent = parent.parent
        if not parent.is_dir():
            raise NotADirectoryError(f"REPORT_PARENT_NOT_DIRECTORY: {report_path}")
    original_audio_hash=sha256_file(audio_abs)
    embedded=read_container_tags(audio_abs)
    noext,_=os.path.splitext(audio_abs)
    def read_optional(f):
        path = Path(f)
        if not path.is_file():
            return ''
        # Bound disk sidecar reads *before* allocating/decode. The strict
        # structural parsers enforce the same 16 MiB source size ceiling.
        if path.stat().st_size > 16 * 1024 * 1024:
            raise ValueError('LYRICS_SOURCE_TOO_LARGE: ' + path.suffix.lower())
        return path.read_text(encoding='utf-8-sig')
    # Read every source independently. Selecting the first nonempty version
    # *before* comparison would silently discard conflicting embedded/sidecar
    # lyrics and misleadingly report all formats as consistent.
    sources = {
        'embedded_elrc': embedded.get('lyrics') or '',
        'sidecar_lrc': read_optional(noext+'.lrc'),
        'sidecar_spl': read_optional(noext+'.spl'),
        'embedded_ttml': embedded.get('ttml') or '',
        'sidecar_ttml': read_optional(noext+'.ttml'),
    }
    elrc_keys=('embedded_elrc','sidecar_lrc','sidecar_spl')
    ttml_keys=('embedded_ttml','sidecar_ttml')
    disk_elrc = sources['sidecar_lrc'] or sources['sidecar_spl']
    disk_ttml = sources['sidecar_ttml']
    active_elrc = next((sources[k] for k in elrc_keys if sources[k]), '')
    active_ttml = next((sources[k] for k in ttml_keys if sources[k]), '')
    extra_inv = [_public_inventory_item(item, idx)
                 for idx,item in enumerate(embedded.get('extra_lyrics', []))]
    extra_ttml_inv = [_public_inventory_item(item, idx)
                      for idx,item in enumerate(embedded.get('extra_ttml', []))]
    report={
        'version':__version__,'audio':Path(audio_abs).name,'mode':mode,'status':'PASS',
        'has_embedded_elrc':bool(embedded.get('lyrics')),
        'has_embedded_ttml':bool(embedded.get('ttml')),
        'has_sidecar_elrc':bool(disk_elrc),'has_sidecar_ttml':bool(disk_ttml),
        'secondary_lyrics_count': len(extra_inv) + len(extra_ttml_inv),
        'baseline_comparison':None,'format_audit':'NO_LYRICS',
        'issues':[],'prelude_plan':None,'actions_taken':[],
        'source_inventory': {
            **{k: {'present': bool(v), 'sha256': hashlib.sha256(v.encode('utf-8')).hexdigest() if v else None}
               for k, v in sources.items()},
            'embedded_secondary_lyrics': extra_inv,
            'embedded_secondary_ttml': extra_ttml_inv,
        },
        'source_audit': 'PENDING'}
    # Track precisely which paths this invocation created. A report failure
    # must not leave apparently successful outputs behind.
    committed_outputs = []
    def issue(code,description='',blocking=True):
        # Errors can contain arbitrary rejected lyric lines or tag descriptions.
        # Keep the machine-readable code; never serialize exception evidence.
        report['issues'].append({'code':code,'message':code,'blocking':blocking})
        if blocking and report['status'] not in ('BLOCKED',):report['status']='REVIEW_REQUIRED'
    def finish():
        if report_path:
            try:
                _write_json_report(report_path, report)
            except Exception as exc:
                for target in reversed(committed_outputs):
                    try: os.unlink(target)
                    except OSError: pass
                committed_outputs.clear()
                report['actions_taken'] = []
                report['status'] = 'BLOCKED'
                issue('REPORT_WRITE_FAILED', f"{type(exc).__name__}: {str(exc)}")
        return report

    # Strictly validate *all* available sources, including those that will
    # not be used for rendering. No selected high-priority source can hide a
    # malformed or conflicting lower-priority source.
    def _source_signature(data, fmt):
        if fmt == 'ELRC':
            parsed_lines = parse_elrc_structural(data)
            # Differential check: the legacy parser feeds the actual
            # canonical standardizer. A strict structural comparison alone is
            # insufficient when that parser could invent or omit word spans.
            legacy = parse_lrc(data)
            if legacy.get('unparsed_lines') or len(legacy['lines']) != len(parsed_lines):
                raise ValueError('ELRC_PARSER_DIVERGENCE: unconsumed or duplicated lyric line')
            for strict_line, legacy_line in zip(parsed_lines, legacy['lines']):
                if (strict_line.start_ms != legacy_line['start'] or
                        strict_line.text.strip() != legacy_line['text'].strip()):
                    raise ValueError('ELRC_PARSER_DIVERGENCE: text or line time changed')
                if strict_line.words:
                    legacy_words = legacy_line.get('words', [])
                    if (len(strict_line.words) != len(legacy_words) or
                            any((a.text, a.start_ms, a.end_ms) !=
                                (str(b['text']), int(b['start']), int(b['end']))
                                for a,b in zip(strict_line.words, legacy_words))):
                        raise ValueError('ELRC_PARSER_DIVERGENCE: acoustic word axis changed')
                    if strict_line.end_ms != legacy_line['end']:
                        raise ValueError('ELRC_PARSER_DIVERGENCE: explicit line end changed')
            return tuple((line.start_ms, line.end_ms, line.text,
                          tuple((w.text,w.start_ms,w.end_ms) for w in line.words))
                         for line in parsed_lines)
        parsed_lines, bound, checks = parse_ttml_structural(data)
        blocking = [i.code for i in checks if i.blocking]
        if blocking:
            raise ValueError('TTML_STRUCTURE_INVALID:' + ','.join(blocking))
        return (bound,tuple((line.start_ms,line.end_ms,line.text,
                      tuple((w.text,w.start_ms,w.end_ms) for w in line.words),
                      line.translation or '',line.role,line.is_prelude)
                     for line in parsed_lines))

    validated = {}
    invalid_sources = []
    for name, data in sources.items():
        if not data:
            continue
        try:
            validated[name] = _source_signature(data, 'TTML' if name in ttml_keys else 'ELRC')
        except (ValueError, TypeError, UnicodeError) as exc:
            invalid_sources.append(name)
            issue('SOURCE_LYRICS_INVALID', f'{name}: {type(exc).__name__}: {exc}')
            issue('INPUT_PARSE_FAILED', f'Invalid source {name}: {exc}')
    for family in (elrc_keys,ttml_keys):
        available=[name for name in family if name in validated]
        if len(available)>1:
            baseline_name=available[0]
            for name in available[1:]:
                if validated[name]!=validated[baseline_name]:
                    issue('SOURCE_LYRICS_CONFLICT', f'{baseline_name} differs from {name}')

    seen_sec = {}
    for item in extra_inv:
        # FLAC Vorbis multi-value entries are positional opaque tracks. They
        # may contain different languages with no language/description tags.
        # ID3 USLT identifiers are semantic; same-id conflicting values block.
        if item['container'] == 'ID3':
            ident = (item['kind'], item['lang'], item['desc_sha256'])
            if ident in seen_sec and seen_sec[ident] != item['sha256']:
                issue('SECONDARY_LYRICS_CONFLICT', 'Secondary identifier conflict')
            seen_sec[ident] = item['sha256']
        # If another USLT has empty desc, it is a competing main lyric track
        if item['kind'] == 'USLT' and item['desc_sha256'] == hashlib.sha256(b'').hexdigest():
            if sources.get('embedded_elrc') and hashlib.sha256(sources['embedded_elrc'].encode('utf-8')).hexdigest() != item['sha256']:
                issue('SOURCE_LYRICS_CONFLICT', 'Multiple competing un-described embedded USLT tracks')
    for item in extra_ttml_inv:
        if sources.get('embedded_ttml') and hashlib.sha256(sources['embedded_ttml'].encode('utf-8')).hexdigest() != item['sha256']:
            issue('SOURCE_LYRICS_CONFLICT', 'Multiple conflicting embedded TTML tracks')
    report['source_audit'] = 'CONFLICT_OR_INVALID' if report['status']!='PASS' else 'VALIDATED_PENDING_FORMAT'
    if report['status']!='PASS':
        report['format_audit']='SOURCE_CONFLICT_OR_INVALID'
        if invalid_sources:
            report['status']='BLOCKED'
        return finish()

    if active_elrc and active_ttml:
        baseline=compare_elrc_ttml(active_elrc,active_ttml)
        report['baseline_comparison']={'passed':baseline.passed,
            'max_time_delta_ms':baseline.max_time_delta_ms,
            'issues':[i.code for i in baseline.issues]}
        report['format_audit']='CONSISTENT' if baseline.passed else 'INCONSISTENT'
        if not baseline.passed:
            issue('INPUT_FORMATS_CONFLICT','Existing ELRC/TTML differ')
            report['source_audit']='CONFLICT_OR_INVALID'
        else:
            report['source_audit']='CONSISTENT'
    elif active_elrc or active_ttml:
        n_sources = sum(bool(x) for x in sources.values())
        report['source_audit'] = ('SAME_FORMAT_CONSISTENT' if n_sources > 1
                                  else 'ONE_SOURCE_ONLY')
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
        if parsed.get('unparsed_lines'):
            raise ValueError(f"UNPARSED_LRC_LINES: Unparsed non-empty line cannot be preserved: {parsed['unparsed_lines'][0]!r}")
    except (ValueError,TypeError,KeyError) as exc:
        report['status']='BLOCKED';issue('INPUT_PARSE_FAILED',str(exc))
        return finish()
    # Preserve re: attribution even when TTML was preferred over ELRC.
    if active_elrc:
        for name in elrc_keys:
            if not sources[name]:
                continue
            lrc_tags=parse_lrc(sources[name]).get('tags',{})
            # Keep source attribution even when only the secondary sidecar
            # carries a mandatory origin notice.
            parsed.setdefault('tags',{}).update({k:v for k,v in lrc_tags.items()
                           if k in ('re','ti','ar') and v})
    # Preserve non-blocking structural provenance in the public audit report.
    # Inferred div bounds are a supported input compatibility path, not a
    # reason to return INPUT_PARSE_FAILED or silently discard the warning.
    for notice in parsed.get('ttml_structure_notices', []):
        issue(notice['code'], notice['message'], False)
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
    if active_ttml:
        # The Apple/iTunes p-level semantic role is source evidence, not a
        # decorative hint. Refuse successful output if semantic classification
        # would re-label or remove a declared vocal/content role.
        by_index={line.source_index:line for line in canonical.lines}
        for original_index, original_line in enumerate(parsed.get('lines', [])):
            declared=str(original_line.get('semantic_role') or '').upper()
            if declared in ('SINGING','SPOKEN_CONTENT','SAMPLE_EXPLANATION',
                            'PERFORMER_CUE','UNKNOWN','TRANSLATION'):
                resolved=by_index.get(original_index)
                if resolved is None or resolved.role.value!=declared:
                    canonical.add_issue('INPUT_SEMANTIC_ROLE_CHANGED',
                        severity=IssueSeverity.REVIEW_REQUIRED,
                        source_index=original_index,
                        action='BLOCK_UNVERIFIED_ROLE_RECLASSIFICATION',blocking=True)
                elif declared in ('SAMPLE_EXPLANATION','PERFORMER_CUE') and original_line.get('words'):
                    src_words=original_line['words']
                    if (len(src_words)!=1 or
                            int(src_words[0]['start'])!=int(original_line['start']) or
                            int(src_words[0]['end'])!=int(original_line['end'])):
                        canonical.add_issue('UNSUPPORTED_TIMED_NONSINGING_WORDS',
                            severity=IssueSeverity.REVIEW_REQUIRED,
                            source_index=original_index,
                            action='RETAIN_SOURCE_REQUIRE_REVIEW',blocking=True)
    plan=plan_prelude_ab(canonical)
    report['prelude_plan']={'mode':plan.mode,'t_sing_ms':plan.t_sing_ms,
        'title_window':plan.title_window,'lyricist_window':plan.lyricist_window,
        'composer_window':plan.composer_window,
        'display_title':plan.display_title,'display_lyricist':plan.display_lyricist,
        'display_composer':plan.display_composer}
    # Merge issues after *all* planning steps. No permission flag bypasses them.
    report['issues'].extend(_public_canonical_issue(i) for i in canonical.issues)
    if any(i.blocking or i.severity.value=='REVIEW_REQUIRED' for i in canonical.issues):
        report['status']='REVIEW_REQUIRED'
    if mode=='audit':
        if report['format_audit'] == 'ONE_FORMAT_ONLY' and report['status'] == 'PASS':
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
            # Paths identify the caller's local machine; the public report
            # carries only non-sensitive verification fields.
            report['container_verification']={k:v for k,v in cr.items()
                                              if k != 'target_path'}
            committed.append(target_audio)
        if sha256_file(audio_abs) != original_audio_hash:
            raise RuntimeError('SOURCE_MODIFIED_DURING_STANDARDIZATION')
        for dst,tmp in staged.items():
            os.link(tmp,dst)
            committed.append(dst)
        report['actions_taken']=['WRITE_SIDECAR_LRC','WRITE_SIDECAR_SPL','WRITE_SIDECAR_TTML']
        if write_tags:report['actions_taken'].append('WRITE_VERIFIED_CONTAINER_TAGS')
        committed_outputs.extend(committed)
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
        help="原地修改源文件（为保护源音频绝对安全已显式禁用，调用将被阻断）",
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

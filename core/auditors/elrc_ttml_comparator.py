# -*- coding: utf-8 -*-
"""Independent structural ELRC/TTML parser and strict v1.8.2 verifier.

The comparison never interprets an equal timestamp alone as translation; TTML
for= evidence is required. Source-canonical verification is a separate hard gate.
"""
from __future__ import annotations
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass,field
from typing import Any,List,Optional,Tuple
from ..utils.time_utils import parse_time_string_to_ms

ELINE=re.compile(r"^\[(\d+:\d+(?:\.\d+)?)\](.*)$")
ETAG=re.compile(r"<(\d+:\d+(?:\.\d+)?)>([^<]*)")
NT="http://www.w3.org/ns/ttml"
NI="http://music.apple.com/lyric-ttml-internal"

@dataclass
class ParsedWord:
    text:str
    start_ms:int
    end_ms:int

@dataclass
class ParsedLine:
    line_id:str
    start_ms:int
    end_ms:Optional[int]
    text:str
    words:List[ParsedWord]=field(default_factory=list)
    translation:Optional[str]=None
    has_explicit_end:bool=False
    is_prelude:bool=False
    role:str=""

@dataclass
class ComparisonIssue:
    code:str
    message:str
    line_id:Optional[str]=None
    elrc_val:Any=None
    ttml_val:Any=None
    blocking:bool=True

@dataclass
class ComparisonResult:
    passed:bool
    issues:List[ComparisonIssue]=field(default_factory=list)
    max_time_delta_ms:int=0
    max_div_delta_ms:int=0
    def add_error(self,code,message,line_id=None,elrc_val=None,ttml_val=None):
        self.issues.append(ComparisonIssue(code,message,line_id,elrc_val,ttml_val))
        self.passed=False


def _time(text):
    t=parse_time_string_to_ms(text)
    if t is None:raise ValueError("Invalid timestamp")
    return t


def parse_elrc_structural(text:str)->List[ParsedLine]:
    if len(text.encode('utf-8'))>16*1024*1024:raise ValueError("ELRC exceeds allowed size")
    res=[]
    for raw in text.splitlines():
        # DO NOT .strip() the payload: whitespace is part of the lyric.
        if not raw.strip():continue
        if re.match(r'^\[[a-zA-Z][a-zA-Z0-9_-]*:',raw):continue
        m=ELINE.match(raw)
        if not m:continue
        st=_time(m.group(1));body=m.group(2);tags=list(ETAG.finditer(body));words=[];end=None;explicit=False
        prefix=body[:tags[0].start()] if tags else ''
        if tags:
            for j,tag in enumerate(tags):
                w_st=_time(tag.group(1));w_txt=tag.group(2)
                w_end=_time(tags[j+1].group(1)) if j+1<len(tags) else w_st
                if w_txt:
                    words.append(ParsedWord(w_txt,w_st,w_end))
            if tags[-1].group(2)=="":
                explicit=True;end=_time(tags[-1].group(1))
            elif words:end=words[-1].end_ms
            value=prefix+''.join(w.text for w in words)
        else:
            value=body
        res.append(ParsedLine(f"L{len(res)+1}",st,end,value,words,None,explicit))
    return res


def parse_ttml_structural(text:str)->Tuple[List[ParsedLine],Tuple[int,int],List[ComparisonIssue]]:
    problems=[];res=[]
    if len(text.encode('utf-8'))>16*1024*1024 or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        return [],(0,0),[ComparisonIssue('XML_UNSAFE_INPUT','DTD/entity or oversized XML is rejected')]
    try: root=ET.fromstring(text)
    except (ET.ParseError,ValueError) as exc:
        return [],(0,0),[ComparisonIssue('XML_SYNTAX_ERROR',f'Invalid XML: {type(exc).__name__}')]
    trans={}
    for n in root.findall('.//{*}iTunesMetadata/{*}translations/{*}translation/{*}text'):
        key=n.get('for')
        if not key:
            problems.append(ComparisonIssue('TRANSLATION_MISSING_REFERENCE','Missing for= key'))
            continue
        if key in trans:
            problems.append(ComparisonIssue('TRANSLATION_DUPLICATE_REFERENCE','Multiple translations for same key',key))
        trans[key]=(n.text or '').strip()
    divs=root.findall('.//{*}body/{*}div')
    if not divs:
        return [],(0,0),problems+[ComparisonIssue('DIV_MISSING','No TTML div')]
    if len(divs)>1:
        problems.append(ComparisonIssue('MULTI_DIV_UNSUPPORTED','Multi-div input needs dedicated mapping'))
    div=divs[0]
    try:bound=(_time(div.attrib.get('begin','')), _time(div.attrib.get('end','')))
    except ValueError:
        bound=(0,0);problems.append(ComparisonIssue('DIV_TIME_INVALID','Invalid div boundary'))
    seen=set()
    for p in div.findall('./{*}p'):
        try:st=_time(p.get('begin',''));en=_time(p.get('end',''))
        except ValueError:
            problems.append(ComparisonIssue('LINE_TIME_INVALID','Invalid p time'))
            continue
        if en<=st:
            problems.append(ComparisonIssue('LINE_TIME_INVALID','Nonpositive p duration'))
        key=p.get('{%s}key'%NI) or p.get('key') or 'L'+str(len(res)+1)
        if key in seen:problems.append(ComparisonIssue('DUPLICATE_P_KEY','Repeated p key',key))
        seen.add(key)
        pre=p.text or ''
        words=[];inline=[];pieces=[pre]
        for s in p.findall('./{*}span'):
            role=s.get('{http://www.w3.org/ns/ttml#metadata}role') or s.get('role')
            if role=='x-translation':
                inline.append(''.join(s.itertext()).strip());continue
            try:wst=_time(s.get('begin',''));wen=_time(s.get('end',''))
            except ValueError:
                problems.append(ComparisonIssue('WORD_TIME_INVALID','Invalid span time',key))
                continue
            if wen<=wst:problems.append(ComparisonIssue('WORD_TIME_INVALID','Nonpositive span',key))
            txt=(s.text or '')+(s.tail or '')
            words.append(ParsedWord(txt,wst,wen));pieces.append(txt)
        if key in trans and inline and trans[key]!=''.join(inline):
            problems.append(ComparisonIssue('TRANSLATION_CONFLICT','Head/inline translations conflict',key))
        res.append(ParsedLine(key,st,en,''.join(pieces),words,
                              trans.get(key) or ''.join(inline) or None,True,
                              key.startswith('P_'),p.get('{%s}role'%NI) or ''))
    for key in trans:
        if key not in seen:problems.append(ComparisonIssue('TRANSLATION_HANGING_REFERENCE','Unmatched translation for=',key))
    return res,bound,problems


def _elrc_corresponding_lines(src:List[ParsedLine],ttml:List[ParsedLine])->List[ParsedLine]:
    """Attach a sidecar row only if TTML explicitly declares exactly that translation."""
    result=[];i=0
    for t in ttml:
        if i>=len(src):break
        e=src[i];i+=1
        if t.translation and i<len(src):
            x=src[i]
            if (x.start_ms==e.start_ms and not x.words and
                x.text==t.translation):
                e.translation=x.text;i+=1
        result.append(e)
    result.extend(src[i:])
    return result


def compare_elrc_ttml(elrc_text:str,ttml_text:str)->ComparisonResult:
    res=ComparisonResult(True)
    try:raw_elrc=parse_elrc_structural(elrc_text)
    except (ValueError,TypeError) as exc:
        res.add_error('ELRC_SYNTAX_ERROR',str(exc));return res
    ttml,bound,issues=parse_ttml_structural(ttml_text)
    if issues:
        for issue in issues:res.add_error(issue.code,issue.message,issue.line_id)
        return res
    elrc=_elrc_corresponding_lines(raw_elrc,ttml)
    if len(elrc)!=len(ttml):
        res.add_error('LINE_MISSING',f'ELRC {len(elrc)} vs TTML {len(ttml)}');return res
    maxd=0
    for i,(e,t) in enumerate(zip(elrc,ttml)):
        key=t.line_id
        d=abs(e.start_ms-t.start_ms);maxd=max(maxd,d)
        if d>1:res.add_error('TIME_AXIS_MISMATCH','Line start differs',key)
        if e.end_ms is not None:
            d=abs(e.end_ms-t.end_ms);maxd=max(maxd,d)
            if d>1:res.add_error('TIME_AXIS_MISMATCH','Line end differs',key)
        if e.text!=t.text:
            res.add_error('TEXT_MISMATCH','Line text or whitespace differs',key,e.text,t.text)
        if len(e.words)!=len(t.words):
            res.add_error('WORD_MISMATCH','Word count differs',key)
        else:
            for idx,(ew,tw) in enumerate(zip(e.words,t.words)):
                d1=abs(ew.start_ms-tw.start_ms);d2=abs(ew.end_ms-tw.end_ms)
                maxd=max(maxd,d1,d2)
                if d1>1 or d2>1:res.add_error('TIME_AXIS_MISMATCH',f'Word {idx} times differ',key)
                if ew.text.strip()!=tw.text.strip():
                    res.add_error('WORD_MISMATCH',f'Word {idx} differs',key,ew.text,tw.text)
        if (e.translation or '')!=(t.translation or ''):
            res.add_error('TRANSLATION_LINK_MISMATCH','Translation differs',key,e.translation,t.translation)
        if not e.has_explicit_end and not e.words:
            res.add_error('LINE_END_NOT_REPRESENTABLE','Cannot validate end of line-only ELRC',key)
    if elrc:
        mins=min(x.start_ms for x in elrc);maxs=max(x.end_ms or x.start_ms for x in elrc)
        bd=max(abs(mins-bound[0]),abs(maxs-bound[1]));res.max_div_delta_ms=bd
        if bd>2:res.add_error('DIV_BOUNDARY_MISMATCH','div boundaries differ')
    res.max_time_delta_ms=maxd
    return res


def verify_canonical_exports(canonical,elrc_text:str,ttml_text:str)->ComparisonResult:
    """Third independent oracle: two identical but corrupt exports MUST fail."""
    out=compare_elrc_ttml(elrc_text,ttml_text)
    if not out.passed:return out
    tlines,_,issues=parse_ttml_structural(ttml_text)
    if issues:
        for x in issues:out.add_error(x.code,x.message,x.line_id)
        return out
    expected=[]
    p=canonical.prelude
    if p and p.mode in ('A','B'):
        for key,w,txt in (('P_TITLE',p.title_window,p.display_title),
                          ('P_LYRICIST',p.lyricist_window,p.display_lyricist),
                          ('P_COMPOSER',p.composer_window,p.display_composer)):
            if w and txt and w[1]>w[0]:expected.append((key,w[0],w[1],txt,[],None))
    for line in canonical.lines:
        if line.role.value in ('TITLE','LYRICIST','COMPOSER','PRODUCTION_CREDIT','PLACEHOLDER','TRANSLATION'):
            continue
        if not line.text.strip():continue
        realwords=[w for w in line.words if w.text and not w.is_pause]
        expected.append((line.stable_id,line.start_ms,line.end_ms,line.text,realwords,line.translation))
    if len(expected)!=len(tlines):
        out.add_error('CANONICAL_LINE_COUNT','Canonical vs generated line count differs');return out
    for (key,st,en,orig,words,translation), got in zip(expected,tlines):
        if got.line_id!=key or abs(got.start_ms-st)>1 or abs(got.end_ms-en)>1:
            out.add_error('CANONICAL_LINE_ID_TIME','Canonical line identity/times differ',key)
        if got.text!=orig:
            out.add_error('CANONICAL_TEXT_LOSS','Canonical text/whitespace was lost',key,orig,got.text)
        if not key.startswith('P_'):
            original_line = next((ln for ln in canonical.lines if ln.stable_id==key),None)
            if original_line and got.role != original_line.role.value:
                out.add_error('CANONICAL_ROLE_MISMATCH','Canonical semantic role was lost',key)
        if (translation or '')!=(got.translation or ''):
            out.add_error('CANONICAL_TRANSLATION_LOSS','Canonical translation was lost',key)
        if words:
            if len(words)!=len(got.words):out.add_error('CANONICAL_WORD_COUNT','Canonical word count changed',key)
            else:
                for w,actual in zip(words,got.words):
                    if (w.text.strip()!=actual.text.strip() or abs(w.start_ms-actual.start_ms)>1 or
                        abs(w.end_ms-actual.end_ms)>1):
                        out.add_error('CANONICAL_WORD_LOSS','Canonical word/timing changed',key)
    if canonical.song.get('attribution_re'):
        if '[re:lrc.red (https://lrc.red)]' not in elrc_text:
            out.add_error('ATTRIBUTION_LOSS','Required source attribution lost')
    return out

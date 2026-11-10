# -*- coding: utf-8 -*-
"""Independent structural ELRC/TTML parser and strict v1.8.3 verifier.

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

_RE_W3C_CLOCK = re.compile(r"^(\d{2,}):([0-5]\d):([0-5]\d)(?:\.(\d+))?$")
_RE_APPLE_COLON = re.compile(r"^(\d+):([0-5]\d)(?:\.(\d+))?$")
_RE_SECONDS_OFFSET = re.compile(r"^(\d+)(?:\.(\d+))?\s*s?$", re.IGNORECASE)


def _parse_fraction_ms(frac: Optional[str]) -> int:
    if not frac:
        return 0
    if len(frac) == 1:
        return int(frac) * 100
    if len(frac) == 2:
        return int(frac) * 10
    if len(frac) == 3:
        return int(frac)
    return int(frac[:3])


def parse_ttml_time_ms(time_str: Optional[str], profile: str = "APPLE_AMLL_INPUT") -> Optional[int]:
    """Profile-aware strict TTML timestamp parser.

    Profiles:
      - 'W3C_CLOCK': Strict W3C TTML §10.3 clock-time: HH:MM:SS[.fraction]
        Minutes and seconds strictly 00-59. Hours >= 0 (2+ digits).
      - 'APPLE_AMLL_INPUT': Tolerant input profile: accepts W3C_CLOCK (3-seg),
        Apple/AMLL MM:SS[.fraction] (2-seg, seconds strictly 00-59),
        or offset-time seconds ('15.000s' or '15.000').
        Strictly rejects invalid seconds (e.g. '00:75.000'), negative numbers,
        NaN, Infinity, empty/whitespace strings, and trailing garbage.
    """
    if not time_str or not isinstance(time_str, str):
        return None
    s = time_str.strip()
    if not s or s.startswith("-"):
        return None

    # 1. Strict W3C Clock-time (HH:MM:SS.mmm)
    m_w3c = _RE_W3C_CLOCK.match(s)
    if m_w3c:
        hours = int(m_w3c.group(1))
        minutes = int(m_w3c.group(2))
        seconds = int(m_w3c.group(3))
        millis = _parse_fraction_ms(m_w3c.group(4))
        return (hours * 3600 + minutes * 60 + seconds) * 1000 + millis

    if profile == "W3C_CLOCK":
        return None

    # 2. Apple/AMLL 2-segment colon (MM:SS.mmm)
    m_apple = _RE_APPLE_COLON.match(s)
    if m_apple:
        minutes = int(m_apple.group(1))
        seconds = int(m_apple.group(2))
        millis = _parse_fraction_ms(m_apple.group(3))
        return (minutes * 60 + seconds) * 1000 + millis

    # 3. Offset-time seconds (e.g. '15.000s' or '15.000')
    m_sec = _RE_SECONDS_OFFSET.match(s)
    if m_sec:
        seconds = int(m_sec.group(1))
        millis = _parse_fraction_ms(m_sec.group(2))
        return seconds * 1000 + millis

    return None


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
    div_inferred:bool=False
    def add_error(self,code,message,line_id=None,elrc_val=None,ttml_val=None):
        self.issues.append(ComparisonIssue(code,message,line_id,elrc_val,ttml_val,blocking=True))
        self.passed=False


def _time_elrc(text: str) -> int:
    t = parse_time_string_to_ms(text)
    if t is None:
        raise ValueError("Invalid ELRC timestamp")
    return t


def _time_ttml(text: str, profile: str = "APPLE_AMLL_INPUT") -> int:
    t = parse_ttml_time_ms(text, profile=profile)
    if t is None:
        raise ValueError(f"Invalid TTML timestamp: {text}")
    return t


def parse_elrc_structural(text:str)->List[ParsedLine]:
    if len(text.encode('utf-8'))>16*1024*1024:raise ValueError("ELRC exceeds allowed size")
    res=[]
    for raw in text.splitlines():
        if not raw.strip():continue
        if re.match(r'^\[[a-zA-Z][a-zA-Z0-9_-]*:',raw):continue
        m=ELINE.match(raw)
        if not m:continue
        st=_time_elrc(m.group(1));body=m.group(2);tags=list(ETAG.finditer(body));words=[];end=None;explicit=False
        prefix=body[:tags[0].start()] if tags else ''
        if tags:
            for j,tag in enumerate(tags):
                w_st=_time_elrc(tag.group(1));w_txt=tag.group(2)
                w_end=_time_elrc(tags[j+1].group(1)) if j+1<len(tags) else w_st
                if w_txt:
                    words.append(ParsedWord(w_txt,w_st,w_end))
            if tags[-1].group(2)=="":
                explicit=True;end=_time_elrc(tags[-1].group(1))
            elif words:end=words[-1].end_ms
            value=prefix+''.join(w.text for w in words)
        else:
            value=body
        res.append(ParsedLine(f"L{len(res)+1}",st,end,value,words,None,explicit))
    return res


def parse_ttml_structural(text:str, profile: str = "APPLE_AMLL_INPUT")->Tuple[List[ParsedLine],Tuple[int,int],List[ComparisonIssue]]:
    problems=[];res=[]
    if len(text.encode('utf-8'))>16*1024*1024 or '<!DOCTYPE' in text.upper() or '<!ENTITY' in text.upper():
        return [],(0,0),[ComparisonIssue('XML_UNSAFE_INPUT','DTD/entity or oversized XML is rejected')]
    try: root=ET.fromstring(text)
    except (ET.ParseError,ValueError) as exc:
        return [],(0,0),[ComparisonIssue('XML_SYNTAX_ERROR',f'Invalid XML: {type(exc).__name__}')]

    # Timing model checks: reject unsupported dur or timeContainer="seq"
    for elem in root.iter():
        if elem.tag.endswith('head') or elem.tag.endswith('metadata'):
            continue
        if 'dur' in elem.attrib:
            problems.append(ComparisonIssue('TIMING_MODEL_UNSUPPORTED', f"dur attribute on <{elem.tag.split('}')[-1]}> is unsupported"))
            break
        tc = elem.attrib.get('timeContainer') or elem.attrib.get('{http://www.w3.org/ns/ttml}timeContainer')
        if tc and tc.lower() in ('seq', 'par'):
            problems.append(ComparisonIssue('TIMING_MODEL_UNSUPPORTED', f"timeContainer='{tc}' timing model is unsupported"))
            break

    # Declared agents
    declared_agents = set()
    for ag in root.findall('.//{*}metadata/{*}agent'):
        for attr_k, attr_v in ag.attrib.items():
            if attr_k.endswith('id') or attr_k == 'id':
                declared_agents.add(attr_v)

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
        problems.append(ComparisonIssue('MULTI_DIV_UNSUPPORTED','Multi-div input is unsupported'))
    div=divs[0]

    has_begin = 'begin' in div.attrib
    has_end = 'end' in div.attrib
    explicit_bound: Optional[Tuple[int, int]] = None
    div_inferred = False

    if has_begin and has_end:
        try:
            div_b = _time_ttml(div.attrib['begin'], profile=profile)
            div_e = _time_ttml(div.attrib['end'], profile=profile)
            explicit_bound = (div_b, div_e)
        except ValueError:
            problems.append(ComparisonIssue('DIV_TIME_INVALID','Invalid explicit div boundary'))
    elif has_begin or has_end:
        problems.append(ComparisonIssue('DIV_TIME_INVALID','Partially missing div boundary'))
    else:
        div_inferred = True

    seen=set()
    for p in div.findall('./{*}p'):
        key=p.get('{%s}key'%NI) or p.get('key') or 'L'+str(len(res)+1)
        if key in seen:problems.append(ComparisonIssue('DUPLICATE_P_KEY','Repeated p key',key))
        seen.add(key)

        p_role = p.get('{http://www.w3.org/ns/ttml#metadata}role') or p.get('role') or ''
        if p_role == 'x-bg':
            problems.append(ComparisonIssue('BACKGROUND_VOCAL_UNSUPPORTED', 'x-bg background vocal track is unsupported', key))
        elif p_role == 'x-roman':
            problems.append(ComparisonIssue('AUXILIARY_TRACK_UNSUPPORTED', 'x-roman auxiliary track is unsupported', key))

        agent = p.get('{%s}agent'%NI) or p.get('agent') or p.get('{http://www.w3.org/ns/ttml#metadata}agent')
        if agent and agent not in declared_agents:
            problems.append(ComparisonIssue('TRANSLATION_AGENT_MISMATCH', f'Undeclared agent reference: {agent}', key))

        try:
            st=_time_ttml(p.get('begin',''), profile=profile)
            en=_time_ttml(p.get('end',''), profile=profile)
        except ValueError:
            problems.append(ComparisonIssue('LINE_TIME_INVALID','Invalid p time',key))
            continue
        if en<=st:
            problems.append(ComparisonIssue('LINE_TIME_INVALID','Nonpositive p duration',key))

        # Check for nested span or ruby
        for child in p:
            if child.tag.endswith('span'):
                if list(child):
                    for sub in child:
                        if sub.tag.endswith('span') or 'ruby' in sub.tag.lower() or 'ruby' in str(sub.attrib).lower():
                            problems.append(ComparisonIssue('RUBY_UNSUPPORTED', 'Nested span or ruby structure is unsupported', key))
                if 'ruby' in str(child.attrib).lower():
                    problems.append(ComparisonIssue('RUBY_UNSUPPORTED', 'tts:ruby is unsupported', key))

        pre=p.text or ''
        words=[];inline=[];pieces=[pre]
        for s in p.findall('./{*}span'):
            role=s.get('{http://www.w3.org/ns/ttml#metadata}role') or s.get('role') or ''
            if role=='x-translation':
                inline.append(''.join(s.itertext()).strip());continue
            elif role=='x-bg':
                problems.append(ComparisonIssue('BACKGROUND_VOCAL_UNSUPPORTED', 'x-bg background vocal track is unsupported', key))
                continue
            elif role=='x-roman':
                problems.append(ComparisonIssue('AUXILIARY_TRACK_UNSUPPORTED', 'x-roman auxiliary track is unsupported', key))
                continue

            try:
                wst=_time_ttml(s.get('begin',''), profile=profile)
                wen=_time_ttml(s.get('end',''), profile=profile)
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

    if div_inferred:
        if res:
            mins_p = min(x.start_ms for x in res)
            maxs_p = max(x.end_ms or x.start_ms for x in res)
            bound = (mins_p, maxs_p)
            problems.append(ComparisonIssue('DIV_BOUNDARY_INFERRED', 'div boundary was inferred from child p elements', blocking=False))
        else:
            bound = (0, 0)
            problems.append(ComparisonIssue('DIV_TIME_UNRESOLVED', 'Empty div with no child elements to infer boundary'))
    elif explicit_bound is not None:
        bound = explicit_bound
        if res:
            mins_p = min(x.start_ms for x in res)
            maxs_p = max(x.end_ms or x.start_ms for x in res)
            if bound[0] > mins_p or bound[1] < maxs_p:
                problems.append(ComparisonIssue('DIV_TIME_CONFLICT', f'Explicit div boundary [{bound[0]}, {bound[1]}] conflicts with child p range [{mins_p}, {maxs_p}]'))
    else:
        bound = (0, 0)

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
        has_blocking = False
        for issue in issues:
            if issue.blocking:
                res.add_error(issue.code,issue.message,issue.line_id)
                has_blocking = True
            else:
                res.issues.append(issue)
                if issue.code == 'DIV_BOUNDARY_INFERRED':
                    res.div_inferred = True
        if has_blocking:
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

    # Verify self-generated TTML strictly complies with W3C_CLOCK profile
    try:
        root = ET.fromstring(ttml_text)
        for elem in root.iter():
            for attr in ('begin', 'end'):
                if attr in elem.attrib:
                    val = elem.attrib[attr]
                    if parse_ttml_time_ms(val, profile="W3C_CLOCK") is None:
                        out.add_error('W3C_CLOCK_FORMAT_VIOLATION', f"Timestamp '{val}' in <{elem.tag.split('}')[-1]}> is not valid W3C clock-time (HH:MM:SS.mmm)")
                        return out
    except Exception as exc:
        out.add_error('XML_SYNTAX_ERROR', f'Generated TTML invalid XML: {type(exc).__name__}')
        return out

    tlines,_,issues=parse_ttml_structural(ttml_text)
    if issues:
        for x in issues:
            if x.blocking:
                out.add_error(x.code,x.message,x.line_id)
        if not out.passed:
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

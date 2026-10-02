"""Opt-in live Kakao metadata diagnostic: <=8 calls, no LLM/search or DB writes."""
from unittest.mock import patch

from travel import place_service as ps
from travel.kakao_course_candidates import KakaoCourseCandidates, CandidatePolicy, CandidateSearchError


def run(*, live=False):
    if not live:
        raise ValueError('live=True required')
    original = ps._request_kakao
    trace = []

    def fetch(query, **kwargs):
        entry = {'page': query['page'], 'rect': query.get('rect')}
        trace.append(entry)
        try:
            value = original(query, **kwargs)
        except ps.PlaceError as exc:
            entry.update(error=type(exc).__name__, cause=type(exc.__cause__).__name__,
                         http_status=getattr(exc.__cause__, 'code', None))
            raise
        meta, docs = value.get('meta', {}), value.get('documents', [])
        w,s,e,n = map(float, query['rect'].split(','))
        entry.update(meta=meta, documents=len(docs),
            outside=sum(not (w-1e-8 <= float(p['x']) <= e+1e-8 and s-1e-8 <= float(p['y']) <= n+1e-8) for p in docs),
            outside_degrees=[(max(w-float(p['x']),float(p['x'])-e,0), max(s-float(p['y']),float(p['y'])-n,0))
                for p in docs if not (w-1e-8 <= float(p['x']) <= e+1e-8 and s-1e-8 <= float(p['y']) <= n+1e-8)],
            categories=sorted({p.get('category_group_code') for p in docs}))
        return value

    source = KakaoCourseCandidates('DAEJEON', policy=CandidatePolicy(max_calls=8, wall_seconds=25))
    try:
        with patch.object(ps, '_request_kakao', fetch):
            for radius in (100,300):
                source.search({'lat':36.3161788,'lng':127.43127175},radius,'food')
    except CandidateSearchError as exc:
        print('result', exc.reason)
    print(trace)

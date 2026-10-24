"""Optional bounded advice over anonymous, finite candidates; never an approval."""
import json
import math
import urllib.request

from pydantic import BaseModel, ConfigDict


class Choice(BaseModel):
    model_config = ConfigDict(extra='forbid')
    candidate_id: str


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post(url, data, timeout):
    request = urllib.request.Request(url, json.dumps(data).encode(),
                                     {'Content-Type':'application/json'}, method='POST')
    # Do not inherit proxy variables or follow an endpoint to another host.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    with opener.open(request, timeout=timeout) as response:
        payload = response.read(65537)
    if len(payload)>65536:
        raise ValueError('Advice response exceeds 64 KiB')
    return json.loads(payload)


def candidate_order(options, *, post_json=post):
    """At most one rerank and one LLM request. Failure retains deterministic order."""
    candidates = [{'candidate_id':f'margin-{n}', 'density_margin_percent':n} for n in (0,10,20)]
    if options is None:
        return candidates, {'mode':'deterministic','calls':0}
    info = {'mode':'hybrid_advisory','calls':0,'failures':[]}
    if options.ranker_url:
        try:
            info['calls'] += 1
            body = {'query':'Prefer sufficient priming with minimum material, without changing structural infill',
                    'documents':[json.dumps(c,sort_keys=True) for c in candidates]}
            if options.ranker_model: body['model'] = options.ranker_model
            answer=post_json(options.ranker_url,body,options.timeout_seconds)
            rows=answer['results']
            if (len(rows)!=len(candidates) or {r['index'] for r in rows}!=set(range(len(candidates)))
                    or any(type(r['index']) is not int or not math.isfinite(float(r['relevance_score'])) for r in rows)):
                raise ValueError('Ranker must return each candidate index exactly once')
            candidates=[candidates[r['index']] for r in sorted(rows,key=lambda r:(-r['relevance_score'],r['index']))]
        except (OSError, ValueError, KeyError, TypeError):
            info['failures'].append('ranker_unavailable_or_invalid')
    if options.llm_url:
        try:
            info['calls'] += 1
            body={'model':options.llm_model,'temperature':0,'max_tokens':80,
                  'messages':[{'role':'system','content':'Choose one supplied candidate. Reply only with JSON {"candidate_id":"..."}. You cannot change numerical gates.'},
                              {'role':'user','content':json.dumps({'candidates':candidates})}]}
            answer=post_json(options.llm_url,body,options.timeout_seconds)
            chosen=Choice.model_validate_json(answer['choices'][0]['message']['content']).candidate_id
            if chosen not in {c['candidate_id'] for c in candidates}: raise ValueError('Unknown candidate')
            candidates.sort(key=lambda c: c['candidate_id'] != chosen)
        except (OSError, ValueError, KeyError, TypeError, IndexError):
            info['failures'].append('llm_unavailable_or_invalid')
    return candidates, info

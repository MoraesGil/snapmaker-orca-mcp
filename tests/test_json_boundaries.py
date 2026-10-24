import json

import pytest

from snapmaker_slicer_mcp.domain.types.json import decode_json, decode_object, json_object


def test_default_json_decoder_and_generic_python_boundary_agree():
    value = {'nested': [1, 2.5, True, None, {'text': 'ok'}]}
    assert decode_json(json.dumps(value).encode()) == json_object(value)
    assert decode_object(json.dumps(value)) == value
    with pytest.raises(ValueError, match='JSON object'):
        json_object({'nested': [(1, 2)]})


@pytest.mark.parametrize('text', ['null', '[]', '"scalar"', 'false', '1'])
def test_object_decoder_rejects_non_object_roots(text):
    with pytest.raises(ValueError, match='record must be a JSON object'):
        decode_object(text, 'record')


def test_malformed_json_retains_decoder_exception():
    with pytest.raises(json.JSONDecodeError):
        decode_json('{')

#!/usr/bin/env python3
"""Report contract inventory; a case declaration never proves a native run.

Use --junit PATH to identify passed native testcase names from a real run.
Only tests/native/ testcases in that XML are accepted as execution evidence.
"""
import argparse
import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
from regression.contracts import manifest, named_cases, schema_fingerprint


def report(junit=None, native_report=None):
    doc = manifest()
    cases = doc['cases']
    blocked = {k: r['policy'] for k, r in cases.items() if r['policy']}
    passed = []
    contract_passed = []
    native_properties, native_functions, native_evidence = {}, {}, None
    if junit:
        for test in ET.parse(junit).iter('testcase'):
            identity = test.get('classname', '') + '.' + test.get('name', '')
            success = not any(test.find(tag) is not None for tag in ('skipped', 'failure', 'error'))
            name = test.get('name', '')
            prefix = 'test_schema_contract_and_persistence['
            if success and test.get('classname') in {'test_schema_matrix', 'tests.test_schema_matrix'} and name.startswith(prefix) and name.endswith(']'):
                key = name[len(prefix):-1]
                if key in cases:
                    contract_passed.append(key)
            if ('tests.native.' in identity or test.get('file', '').startswith('tests/native/')) and not any(test.find(tag) is not None for tag in ('skipped', 'failure', 'error')):
                passed.append(identity)
    if native_report:
        observed = json.loads(Path(native_report).read_text())
        engine = observed.get('engine', {})
        accepted = (observed.get('status') == 'pass'
                    and engine.get('source_commit') == doc['source_commit']
                    and all(re.fullmatch(r'[0-9a-f]{64}', str(engine.get(k, '')))
                            for k in ('executable_sha256', 'resources_sha256')))
        native_evidence = {'accepted': bool(accepted), 'engine': engine,
                           'test_source_commit': observed.get('test_source_commit'),
                           'scope': 'Explicit native fixture results, not exhaustive setting/geometry combinations.'}
        if accepted:
            for key, case in observed.get('cases', {}).items():
                if not isinstance(case, dict) or case.get('status') != 'pass' or not case.get('scope'):
                    continue
                if key in cases:
                    native_properties[key] = case
                elif key == 'modifier':
                    native_functions['add_modifier'] = case
    return {'schema_matches_pin': schema_fingerprint() == doc['schema_sha256'],
            'source_commit': doc['source_commit'], 'catalog_count': len(cases),
            'contract_matrix_declared': sorted(cases), 'contract_matrix_executed_passed': sorted(set(contract_passed)), 'policy_rejections': blocked,
            'named_contract_cases': [c['key'] for c in named_cases()],
            'metadata_incomplete_keys': doc['unparsed_keys'],
            'legacy_unparsed_keys_meaning': 'Catalog entries missing both label and tooltip; not unsupported types and not an additional population.',
            'native_executed_passed_testcases': passed,
            'native_fixture_passed_properties': sorted(native_properties),
            'native_property_cases': native_properties, 'native_function_cases': native_functions,
            'native_evidence': native_evidence,
            'missing_native_semantic_coverage': sorted(set(cases) - set(native_properties)),
            'limits': 'Inventory is not a passing test result. The legacy unparsed_keys list identifies missing label and tooltip metadata within the catalog. JUnit names alone never imply property attribution; explicit native reports retain per-case scope. A finite fixture pass does not validate all values, combinations or physical printing.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--junit', type=Path)
    parser.add_argument('--native-report', type=Path, help='Required-runner JSON for the matching schema source commit')
    args = parser.parse_args()
    result = report(args.junit, args.native_report)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['schema_matches_pin'] else 1)

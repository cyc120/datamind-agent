"""Verify the frozen synthetic CAD contract bundle without DXF/LLM/DB calls."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ingestion.cad_contract import CONTRACT_VERSION, DrawingSnapshot, eligible_features


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_bundle(root=ROOT):
    base = root/'data/cad_demo'
    manifest = json.loads((base/'manifest_v1.json').read_text())
    if manifest['contract_version'] != CONTRACT_VERSION:
        raise ValueError('CAD_CONTRACT_VERSION_MISMATCH')
    for relative, expected in manifest['frozen_files'].items():
        path = (root/relative).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file() or sha256(path) != expected:
            raise ValueError('CAD_FROZEN_FILE_CHANGED: '+relative)
    payload = json.loads((base/'snapshot_v1.json').read_text())
    snapshot = DrawingSnapshot.model_validate(payload)
    if snapshot.source.source_sha256 != sha256(base/'drawing_v1.dxf'):
        raise ValueError('CAD_SOURCE_HASH_MISMATCH')
    if json.loads((base/'contract.schema.json').read_text()) != DrawingSnapshot.model_json_schema():
        raise ValueError('CAD_SCHEMA_CHANGED')
    labels = json.loads((base/'association_labels_v1.json').read_text())['labels']
    for feature in snapshot.features:
        evidence = feature.association_evidence
        if evidence and evidence.model_dump() not in labels:
            raise ValueError('CAD_ASSOCIATION_LABEL_UNAVAILABLE')
    context = json.loads((base/'binding_context_v1.json').read_text())
    result = eligible_features(payload, context)
    if result != json.loads((base/'expected_gate_v1.json').read_text()):
        raise ValueError('CAD_GATE_EXPECTATION_MISMATCH')
    return {'contract_version': CONTRACT_VERSION, 'frozen_files_verified': len(manifest['frozen_files']),
            'source_sha256': snapshot.source.source_sha256, 'entity_count': len(snapshot.entities),
            'feature_count': len(snapshot.features), 'gate': result,
            'provenance': 'synthetic_demo', 'parser_executed': False, 'models_called': False,
            'database_accessed': False,
            'limitation': 'Hand-authored adapter-output fixture and author labels; not DXF extraction or factory accuracy.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = verify_bundle()
        result['verified_at'] = datetime.now(timezone.utc).isoformat()
        text = json.dumps(result, ensure_ascii=False, indent=2)+'\n'
        if args.output:
            # First evidence reports are immutable; never silently overwrite.
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(text)
        print(text, end='')
    except (ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

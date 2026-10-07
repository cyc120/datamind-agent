"""Run the frozen V-CAR mechanism ablation and write a new report.

The dataset digest is pinned here: if the frozen cases change, this script refuses to run
instead of silently reporting numbers against different labels.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from datamind import ablation

DATASET_SHA256 = '74cbbc1ef760f53026152f441c484f18125bd11a4d473a92195aee22d1fc4b31'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='新报告路径；已存在则拒绝覆盖')
    parser.add_argument('--dataset', type=Path, default=ablation.DATASET_PATH)
    args = parser.parse_args(argv)
    if args.output.exists():
        print(json.dumps({'status': 'failed', 'error_code': 'REPORT_EXISTS'}), file=sys.stderr)
        return 1
    try:
        dataset = ablation.load_dataset(args.dataset, expected_sha256=DATASET_SHA256)
    except ValueError as error:
        print(json.dumps({'status': 'failed', 'error_code': str(error)}), file=sys.stderr)
        return 1
    result = ablation.run(dataset)
    result['generated_at'] = datetime.now(timezone.utc).isoformat(timespec='seconds')
    result['kind'] = 'mechanism-ablation'
    result['note'] = ('合成机制消融：不调用任何模型，不衡量 OCR/问答准确率；'
                      '四条机制臂在同一冻结数据集上对照，全部失败与分母保留。')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f"数据集 {result['dataset']['version']} sha256={result['dataset']['sha256'][:16]}… 共 {result['dataset']['cases']} 例")
    header = f"{'臂':<11}{'正确':>5}{'错误':>5}{'未消解':>7}{'准确率':>9}{'冲突P':>8}{'冲突R':>8}{'修复成功':>9}{'误修复':>7}"
    print(header)
    for arm in result['arms']:
        metrics, conflict, repair = arm['metrics'], arm['metrics']['conflict'], arm['metrics']['repair']
        print(f"{arm['arm']:<11}{metrics['correct']:>5}{metrics['wrong']:>5}{metrics['unresolved']:>7}"
              f"{str(metrics['accuracy']):>9}{str(conflict['precision']):>8}{str(conflict['recall']):>8}"
              f"{str(repair['success_rate']):>9}{repair['misrepair']:>7}")
    print(f"报告：{args.output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

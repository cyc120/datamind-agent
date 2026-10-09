"""Multi-scale timing and peak-memory report (official item E7/F6: system efficiency).

Everything here is measured, not estimated. Each scale runs in its **own subprocess** so the
reported peak RSS belongs to that scale instead of being the high-water mark of every earlier
one (`ru_maxrss` never decreases within a process).

What this is not: a production throughput claim. It is one machine, one process at a time, no
CPU pinning, no cgroup isolation, no concurrent load, and the file cache is warm after the
first run of each shape. The report prints those caveats next to the numbers.

Usage:
    python scripts/eval_scale.py                      # full sweep, writes reports/scale_v1.json
    python scripts/eval_scale.py --entity-scales 100,1000
    python scripts/eval_scale.py --json-only
"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))          # the parent imports the package too (retrieval section)
REPORT = ROOT / "reports/scale_v1.json"
REPEATS = 3

#: Each child prints one JSON object; the parent never imports the heavy modules itself.
CHILD = r'''
import json, resource, sys, time
kind = sys.argv[1]
payload = json.loads(sys.argv[2])
start = time.perf_counter()
if kind == "cad":
    from ingestion import cad_adapter
    result = cad_adapter.parse(payload["path"], document_id="scale-probe",
                               source_version="v1", provenance="synthetic_demo")
    limits = (result.payload.get("manifest") or {}).get("limits") or {}
    detail = {"parse_status": result.payload["parse_status"],
              "blocks": len(result.payload["blocks"]),
              "max_blocks": limits.get("max_blocks")}
elif kind == "retrieval":
    # Measure the *selection* path the product actually uses when N files are chosen, instead
    # of reading every row and slicing: the earlier version reported a flat ~100 ms for 1..8
    # documents because the row read dominated and the limit changed nothing.
    import psycopg2
    from datamind.agent import _read_dsn
    from datamind.knowledge import KnowledgeRetriever
    dsn = _read_dsn()
    retriever = KnowledgeRetriever(dsn, mode="lexical")
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""SELECT a.document_id, a.doc_version, a.sha256, a.parser_version, a.title
                       FROM document_assets a
                       WHERE a.status='indexed' AND 'public'=ANY(a.acl) AND NOT a.needs_review
                         AND a.collection=ANY(%s)
                         AND (SELECT count(*) FROM chunks c WHERE c.document_id=a.document_id)>0
                       ORDER BY a.document_id LIMIT %s""", (payload["collections"], payload["limit"]))
        refs = [{"document_id": r[0], "document_version": r[1], "source_sha256": r[2],
                 "parser_version": r[3], "title": r[4]} for r in cur.fetchall()]
    rows, snapshots = retriever._selected_rows(payload["collections"], refs)
    detail = {"rows": len(rows), "documents": len(snapshots)}
else:
    raise SystemExit("unknown kind: " + kind)
elapsed_ms = (time.perf_counter() - start) * 1000.0
_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
# `ru_maxrss` is bytes on macOS and kibibytes on Linux; multiplying unconditionally reported
# absurd values (a 183 MiB peak came out as 187120 MiB).
print(json.dumps({"elapsed_ms": elapsed_ms,
                  "peak_rss_bytes": _rss if sys.platform == "darwin" else _rss * 1024,
                  "detail": detail}))
'''


def machine() -> dict:
    cpu = ""
    try:
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True,
                             text=True, check=True).stdout.strip()
    except Exception:
        cpu = platform.processor() or "unknown"
    return {"platform": platform.platform(), "cpu": cpu,
            "cpu_count": os.cpu_count(), "python": platform.python_version(),
            "memory_bytes": _total_memory()}


def _total_memory() -> int | None:
    try:
        return int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                  text=True, check=True).stdout.strip())
    except Exception:
        return None


def measure(kind: str, payload: dict) -> dict:
    """Run one scale `REPEATS` times in fresh subprocesses; report the median and the peak."""
    runs = []
    for _ in range(REPEATS):
        completed = subprocess.run([sys.executable, "-c", CHILD, kind, json.dumps(payload)],
                                   cwd=str(ROOT), capture_output=True, text=True,
                                   env={**os.environ, "PYTHONPATH": str(ROOT)})
        if completed.returncode != 0:
            return {"error": (completed.stderr or completed.stdout).strip().splitlines()[-1:] or
                    ["failed"], "returncode": completed.returncode}
        runs.append(json.loads(completed.stdout.strip().splitlines()[-1]))
    return {"elapsed_ms_median": round(statistics.median(r["elapsed_ms"] for r in runs), 1),
            "elapsed_ms_runs": [round(r["elapsed_ms"], 1) for r in runs],
            "peak_rss_bytes": max(r["peak_rss_bytes"] for r in runs),
            "detail": runs[-1]["detail"], "repeats": len(runs)}


def build_dxf(path: Path, entities: int) -> None:
    """`entities` LINEs plus a little of everything else, so the mix stays realistic."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    model = doc.modelspace()
    for index in range(entities):
        x = float(index % 1000)
        y = float(index // 1000)
        model.add_line((x, y), (x + 1.0, y + 0.5))
        if index % 50 == 0:
            model.add_circle((x, y), radius=0.5)
        if index % 200 == 0:
            model.add_text(f"L{index}", dxfattribs={"height": 2.0}).set_placement((x, y + 2))
    doc.saveas(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity-scales", default="100,1000,5000,20000")
    parser.add_argument("--json-only", action="store_true")
    args = parser.parse_args()

    scales = [int(item) for item in args.entity_scales.split(",") if item.strip()]
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "machine": machine(),
              "caveats": [
                  "one machine, one process at a time; no CPU pinning or cgroup isolation",
                  "no concurrent load; file cache warm after the first run of each shape",
                  "peak RSS is the child process high-water mark for that scale only",
                  "not a production throughput or capacity claim",
              ],
              "repeats_per_scale": REPEATS, "cad_parse": [], "retrieval": []}

    with tempfile.TemporaryDirectory() as directory:
        for entities in scales:
            path = Path(directory) / f"scale-{entities}.dxf"
            build_dxf(path, entities)
            entry = {"entities_requested": entities, "bytes": path.stat().st_size}
            entry.update(measure("cad", {"path": str(path)}))
            report["cad_parse"].append(entry)
            if not args.json_only:
                _print_cad(entry)

    if not args.json_only:
        print()
    from datamind.agent import _read_dsn  # noqa: F401  (import only to fail fast if misconfigured)
    from datamind.knowledge import COLLECTIONS
    for limit in (1, 2, 5, 8):
        entry = {"selected_documents": limit}
        entry.update(measure("retrieval", {"collections": sorted(COLLECTIONS), "limit": limit}))
        report["retrieval"].append(entry)
        if not args.json_only:
            _print_retrieval(entry)

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failures = [item for group in ("cad_parse", "retrieval") for item in report[group]
                if "error" in item]
    if not args.json_only:
        print(f"\n报告：{REPORT.relative_to(ROOT)}")
        print("注意：以上为单机单进程实测，不是生产吞吐或容量承诺。")
    return 1 if failures else 0


def _print_cad(entry: dict) -> None:
    if "error" in entry:
        print(f"CAD {entry['entities_requested']:>6} 实体: 失败 {entry['error']}")
        return
    cap = entry['detail'].get('max_blocks')
    truncated = ' ← 块数触顶（上限 %s，属声明式 partial，非静默截断）' % cap \
        if cap and entry['detail']['blocks'] >= cap else ''
    print(f"CAD {entry['entities_requested']:>6} 实体 ({entry['bytes']/1024:>8.1f} KiB): "
          f"解析中位 {entry['elapsed_ms_median']:>9.1f} ms  "
          f"峰值内存 {entry['peak_rss_bytes']/1048576:>7.1f} MiB  "
          f"块={entry['detail']['blocks']} 状态={entry['detail']['parse_status']}{truncated}")


def _print_retrieval(entry: dict) -> None:
    if "error" in entry:
        print(f"检索 选 {entry['selected_documents']} 份: 失败 {entry['error']}")
        return
    print(f"检索 选 {entry['selected_documents']} 份: "
          f"中位 {entry['elapsed_ms_median']:>9.1f} ms  "
          f"峰值内存 {entry['peak_rss_bytes']/1048576:>7.1f} MiB  "
          f"命中行={entry['detail']['rows']} 实际文档={entry['detail']['documents']}")


if __name__ == "__main__":
    raise SystemExit(main())

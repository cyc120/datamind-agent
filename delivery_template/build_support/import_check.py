"""构建期依赖自检：用交付包里那套「嵌入式 Python + pylibs」导入应用模块。

放在打包流程里，是为了让「包里少了某个依赖」在**打包阶段**就红着脸失败，
而不是等审核老师点到那个页面才发现 ImportError。

只做导入，不连数据库、不写文件。缺依赖就打印清单并以退出码 1 结束。
"""
from __future__ import annotations

import importlib
import sys
import traceback

THIRD_PARTY = [
    'fastapi', 'starlette', 'uvicorn', 'pydantic',
    'psycopg2', 'psycopg2.extras', 'sqlglot', 'httpx',
    'pymupdf', 'fastembed', 'qdrant_client', 'numpy', 'PIL',
    'onnxruntime', 'cv2', 'rapidocr', 'docx', 'pptx', 'openpyxl', 'ezdxf',
    'portalocker', 'pywintypes', 'pythoncom', 'win32api', 'win32_setctime',
    'tzdata', 'huggingface_hub', 'lxml', 'omegaconf', 'yaml', 'tqdm', 'requests',
]

#: 应用模块：覆盖 API 启动链路 + 各功能页面对应的后端模块 + 上传/解析/摄取链路。
APP_MODULES = [
    'datamind.api', 'datamind.agent', 'datamind.sql_engine', 'datamind.knowledge',
    'datamind.settings_store', 'datamind.providers', 'datamind.auth', 'datamind.sessions',
    'datamind.wiki', 'datamind.wiki_query', 'datamind.wiki_projection', 'datamind.wiki_build',
    'datamind.wiki_jobs', 'datamind.pdf_index', 'datamind.pdf_search', 'datamind.pdf_vectors',
    'datamind.pdf_publications', 'datamind.pdf_maintenance', 'datamind.pdf_answer',
    'datamind.cad_store', 'datamind.cad_reviews', 'datamind.cad_dimensions',
    'datamind.evidence_graph', 'datamind.cross_source', 'datamind.parse_contract',
    'datamind.binding', 'datamind.authority', 'datamind.approvals', 'datamind.fact_reviews',
    'datamind.industrial', 'datamind.industrial_query', 'datamind.industrial_rules',
    'datamind.task_budget', 'datamind.frame', 'datamind.visibility', 'datamind.catalog',
    'datamind.complexity', 'datamind.clarification', 'datamind.vcar', 'datamind.rag_engine',
    'datamind.file_jobs', 'datamind.file_uploads', 'datamind.field_store',
    'ingestion.ingest', 'ingestion.ocr', 'ingestion.tables', 'ingestion.office',
    'ingestion.cad_adapter', 'ingestion.cad_dxf', 'ingestion.cad_contract',
    'ingestion.contract_store', 'ingestion.facts', 'ingestion.norm_facts',
    'ingestion.industrial_facts', 'ingestion.upload_pipeline', 'ingestion.upload_worker',
    'ingestion.pdf_prepare', 'ingestion.formula',
    'db.migrate', 'db.seed_demo_retail', 'db.seed_eval_cases', 'db.seed_industrial',
    'db.build_catalog', 'db.build_industrial_catalog',
]

#: 队列 worker 不在包（ingestion/）里而在 scripts/ 下：它们是独立进程入口，
#: 老师那台机器上 run.bat → dmstack.ps1 -Action start-api 会拉起它们，
#: 所以打包时必须验证「包内运行时导得动」。
WORKER_MODULES = ['scripts.pdf_index_worker', 'scripts.wiki_build_worker']

#: 可选：只在离线生成语料 / 抽样脚本里用到，未安装是既有状态（见 requirements.lock 尾注）。
DEV_ONLY = {'matplotlib', 'pyarrow', 'reportlab'}


def main() -> int:
    # 解释器路径里含中文（D:\ICT中国电子杯\...），而以脚本形式运行时 stdout 会退回
    # cp936；父进程（构建脚本，PowerShell 5.1）按 UTF-8 解码，于是中文路径与控制台
    # 输出全变乱码，出问题时反而看不清。这里统一把 stdout/stderr 钉成 UTF-8。
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:                          # noqa: BLE001
        pass
    print(f'解释器      : {sys.executable}')
    print(f'版本        : {sys.version.split()[0]}')
    missing: list[str] = []
    for name in THIRD_PARTY + APP_MODULES + WORKER_MODULES:
        try:
            importlib.import_module(name)
        except Exception as error:            # noqa: BLE001 - 要把每个失败都列出来
            root = name.split('.')[0]
            tag = '（可选，未安装）' if root in DEV_ONLY else ''
            missing.append(f'{name}{tag}: {type(error).__name__}: {error}')
            print(f'  导入失败 {name}: {type(error).__name__}: {error}')
            if not isinstance(error, ImportError):
                traceback.print_exc()

    hard = [item for item in missing if '（可选，未安装）' not in item]
    print(f'第三方依赖  : {len(THIRD_PARTY)} 项，应用模块 {len(APP_MODULES)} 个，worker {len(WORKER_MODULES)} 个')
    if hard:
        print(f'缺依赖/导入失败 {len(hard)} 项：')
        for item in hard:
            print(f'  - {item}')
        return 1
    print('依赖自检通过：包内运行时可以导入全部应用模块。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Parse a DXF in the bounded native profile and save source-linked previews."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from ingestion.cad_dxf import CadReadError, parse_file
from ingestion.cad_render import render_html, render_svg


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source',type=Path)
    ap.add_argument('--document-id',required=True)
    ap.add_argument('--document-version',type=int,default=1)
    ap.add_argument('--provenance',choices=['synthetic_demo','authorized_external'],required=True)
    ap.add_argument('--output-dir',type=Path,required=True,help='New directory; existing output is never overwritten')
    args = ap.parse_args()
    created = False
    try:
        bundle = parse_file(args.source,document_id=args.document_id,document_version=args.document_version,provenance=args.provenance)
        svg, html = render_svg(bundle), render_html(bundle)
        args.output_dir.mkdir(parents=True,exist_ok=False)
        created = True
        (args.output_dir/'snapshot.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2)+'\n')
        (args.output_dir/'drawing.svg').write_text(svg)
        (args.output_dir/'preview.html').write_text(html)
        print(json.dumps({'status':'parsed','parser':bundle['metadata']['parser_version'],
                         'entities':len(bundle['snapshot']['entities']),'features':len(bundle['snapshot']['features']),
                         'warnings':bundle['metadata']['warnings'],'output_dir':str(args.output_dir.resolve())},ensure_ascii=False))
    except (CadReadError,OSError,ValueError,KeyError) as error:
        if created:
            shutil.rmtree(args.output_dir)
        print(json.dumps({'status':'failed','error_code':getattr(error,'code','CAD_OUTPUT_FAILED'),
                          'details':getattr(error,'details',{})},ensure_ascii=False),file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())

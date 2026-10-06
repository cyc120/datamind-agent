"""Native-byte extraction, explicit reference and failure-boundary tests."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import ezdxf
from ingestion.cad_contract import eligible_features
from ingestion.cad_dxf import CadReadError, MAX_BYTES, parse_bytes, parse_file
from ingestion.cad_render import render_svg, render_html


def drawing(unit=4, reference=True):
    doc = ezdxf.new('R2010');doc.header['$INSUNITS']=unit
    for layer in ['DM_DRAWING_ID','DM_REVISION','DM_PART_ID','DM_DIAMETER','GEOMETRY']:
        doc.layers.new(layer)
    doc.appids.new('DATAMIND');m=doc.modelspace()
    for value,layer,y in [('DRW-TEST','DM_DRAWING_ID',60),('R4','DM_REVISION',50),('A104','DM_PART_ID',40)]:
        m.add_text(value,dxfattribs={'layer':layer,'insert':(0,y),'height':2})
    c=m.add_circle((10,10),10,dxfattribs={'layer':'GEOMETRY'})
    m.add_circle((50,10),10,dxfattribs={'layer':'GEOMETRY'})
    m.add_line((0,0),(60,0),dxfattribs={'layer':'GEOMETRY'})
    t=m.add_text('%%c20 +/-0.1',dxfattribs={'layer':'DM_DIAMETER','insert':(25,25),'height':2})
    if reference:t.set_xdata('DATAMIND',[(1000,'diameter_target'),(1005,c.dxf.handle)])
    return doc,t,c


def source(doc):
    stream=io.StringIO();doc.write(stream);return stream.getvalue().encode('utf-8')


def parsed(doc):
    return parse_bytes(source(doc),document_id='test-cad')


def context(bundle):
    s=bundle['snapshot'];return dict(part_id=s['identity']['part_id']['value'],
        drawing_id=s['identity']['drawing_id']['value'],drawing_revision=s['identity']['drawing_revision']['value'],
        source_sha256=s['source']['source_sha256'])


class NativeCadTests(unittest.TestCase):
    def expect_error(self,doc,code):
        with self.assertRaises(CadReadError) as error:parsed(doc)
        self.assertEqual(error.exception.code,code)

    def test_extracts_actual_bytes_handles_titles_and_dimension(self):
        doc,t,c=drawing();data=source(doc);bundle=parse_bytes(data,document_id='test-cad')
        s=bundle['snapshot'];f=s['features'][0]
        self.assertEqual(s['source']['source_sha256'],hashlib.sha256(data).hexdigest())
        self.assertEqual(s['identity']['part_id']['value'],'A104')
        self.assertEqual(len(s['entities']),7)
        self.assertEqual(f['nominal_value'],'20');self.assertEqual(f['tolerance_lower'],'-0.1')
        self.assertEqual(f['locator']['handle'],t.dxf.handle)
        self.assertEqual(f['selected_target'],c.dxf.handle)
        self.assertEqual(f['association_evidence']['method'],'native_reference')
        self.assertEqual(eligible_features(s,context(bundle))['status'],'ready')

    def test_no_reference_never_picks_nearest_or_only_circle(self):
        doc,t,c=drawing(reference=False);bundle=parsed(doc)
        self.assertEqual(bundle['snapshot']['features'][0]['association_status'],'ambiguous')
        self.assertEqual(eligible_features(bundle['snapshot'],context(bundle))['features'],[])
        circles=list(doc.modelspace().query('CIRCLE'));doc.modelspace().delete_entity(circles[1])
        bundle=parsed(doc);self.assertEqual(bundle['snapshot']['features'][0]['association_status'],'unresolved')

    def test_two_explicit_references_remain_ambiguous(self):
        doc,t,c=drawing();circles=list(doc.modelspace().query('CIRCLE'))
        t.set_xdata('DATAMIND',[(1000,'diameter_target')]+[(1005,e.dxf.handle) for e in circles])
        self.assertEqual(parsed(doc)['snapshot']['features'][0]['association_status'],'ambiguous')

    def test_invalid_reference_target_or_shape_is_rejected(self):
        doc,t,c=drawing();t.set_xdata('DATAMIND',[(1000,'diameter_target'),(1005,'ABCDEF')])
        self.expect_error(doc,'CAD_REFERENCE_TARGET_INVALID')
        doc,t,c=drawing();t.set_xdata('DATAMIND',[(1000,'guess_nearest'),(1005,c.dxf.handle)])
        self.expect_error(doc,'CAD_REFERENCE_FORMAT_UNSUPPORTED')
        doc,t,c=drawing();target=list(doc.modelspace().query('TEXT'))[0]
        t.set_xdata('DATAMIND',[(1000,'diameter_target'),(1005,target.dxf.handle)])
        self.expect_error(doc,'CAD_REFERENCE_TARGET_INVALID')

    def test_units_missing_or_unknown_do_not_become_mm(self):
        for missing in [False,True]:
            doc,t,c=drawing(unit=0)
            if missing:del doc.header['$INSUNITS']
            b=parsed(doc);self.assertEqual(b['snapshot']['source']['unit'],'unknown')
            self.assertIsNone(b['snapshot']['features'][0]['nominal_value'])
            self.assertEqual(eligible_features(b['snapshot'],context(b))['features'],[])
        doc,t,c=drawing(unit=2);self.expect_error(doc,'CAD_UNIT_UNSUPPORTED')

    def test_inches_and_small_decimal_offsets_preserved(self):
        doc,t,c=drawing(unit=1)
        t.dxf.text='%%c0.5 +/-0.00000001';c.dxf.radius=0.25
        b=parsed(doc);f=b['snapshot']['features'][0]
        self.assertEqual(f['unit'],'in');self.assertEqual(f['tolerance_upper'],'0.00000001')
        self.assertEqual(eligible_features(b['snapshot'],context(b))['features'][0]['lower'],'0.49999999')

    def test_geometry_conflict_retains_both_values(self):
        doc,t,c=drawing();c.dxf.radius=9;b=parsed(doc)
        self.assertEqual(b['snapshot']['features'][0]['nominal_value'],'20')
        result=eligible_features(b['snapshot'],context(b))
        self.assertEqual(result['excluded'][0]['reason'],'CAD_GEOMETRY_ANNOTATION_CONFLICT')

    def test_unknown_annotation_is_not_silently_dropped(self):
        doc,t,c=drawing();t.dxf.text='diameter unreadable';b=parsed(doc)
        self.assertEqual(len(b['snapshot']['features']),1)
        self.assertIsNone(b['snapshot']['features'][0]['nominal_value'])
        self.assertEqual(b['snapshot']['features'][0]['annotation_raw'],'diameter unreadable')

    def test_missing_or_duplicate_title_field(self):
        doc,t,c=drawing();doc.modelspace().delete_entity(list(doc.modelspace().query('TEXT'))[0])
        self.expect_error(doc,'CAD_TITLE_MISSING')
        doc,t,c=drawing();doc.modelspace().add_text('DRW-OTHER',dxfattribs={'layer':'DM_DRAWING_ID'})
        self.expect_error(doc,'CAD_TITLE_DUPLICATE')

    def test_unsupported_entity_paperspace_and_transform(self):
        doc,t,c=drawing();doc.modelspace().add_arc((1,1),2,0,90)
        self.expect_error(doc,'CAD_ENTITY_TYPE_UNSUPPORTED')
        doc,t,c=drawing();doc.layout().add_circle((1,1),2)
        self.expect_error(doc,'CAD_PAPERSPACE_UNSUPPORTED')
        doc,t,c=drawing();block=doc.blocks.new('PART');block.add_circle((0,0),1)
        doc.modelspace().add_blockref('PART',(10,10))
        self.expect_error(doc,'CAD_ENTITY_TYPE_UNSUPPORTED')
        doc,t,c=drawing();t.dxf.rotation=15
        self.expect_error(doc,'CAD_TEXT_TRANSFORM_UNSUPPORTED')
        doc,t,c=drawing();c.dxf.extrusion=(0,1,0)
        self.expect_error(doc,'CAD_OCS_OR_THICKNESS_UNSUPPORTED')

    def test_3d_nonfinite_and_hidden_sources_are_rejected(self):
        doc,t,c=drawing();c.dxf.center=(10,10,1)
        self.expect_error(doc,'CAD_3D_OR_NONFINITE_UNSUPPORTED')
        doc,t,c=drawing();c.dxf.radius=float('inf');self.expect_error(doc,'CAD_RADIUS_INVALID')
        doc,t,c=drawing();doc.layers.get('GEOMETRY').off()
        self.expect_error(doc,'CAD_HIDDEN_ENTITY_UNSUPPORTED')

    def test_duplicate_source_handle_detected_before_library_repair(self):
        doc,t,c=drawing();data=source(doc)
        modified=data.replace(('\n'+t.dxf.handle+'\n').encode(), ('\n'+c.dxf.handle+'\n').encode(),1)
        with self.assertRaises(CadReadError) as error:parse_bytes(modified,document_id='test-cad')
        self.assertEqual(error.exception.code,'CAD_DUPLICATE_SOURCE_HANDLE')

    def test_dwg_binary_malformed_and_oversize_sources(self):
        for data,code in [(b'AutoCAD Binary DXF\x00','CAD_BINARY_DXF_UNSUPPORTED'),
                          (b'invalid\nsource\n','CAD_DXF_TAGS_INVALID'),(b'x'*(MAX_BYTES+1),'CAD_SOURCE_SIZE_INVALID')]:
            with self.subTest(code=code),self.assertRaises(CadReadError) as error:
                parse_bytes(data,document_id='test-cad')
            self.assertEqual(error.exception.code,code)
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'drawing.dwg';p.write_bytes(b'AC1032')
            with self.assertRaises(CadReadError) as error:parse_file(p,document_id='test-cad')
            self.assertEqual(error.exception.code,'CAD_FORMAT_UNSUPPORTED')

    def test_svg_has_every_native_handle_and_safe_original_text(self):
        doc,t,c=drawing();t.dxf.text='<script>alert(1)</script>';b=parsed(doc)
        svg=render_svg(b);xml=ET.fromstring(svg)
        groups=xml.findall('{http://www.w3.org/2000/svg}g')
        self.assertEqual({g.attrib['data-handle'] for g in groups},{e['handle'] for e in b['snapshot']['entities']})
        self.assertNotIn('<script>',svg);self.assertIn('&lt;script&gt;',svg)
        html=render_html(b);self.assertNotIn('<script>alert(1)',html)
        self.assertIn('\\u003cscript\\u003e',html)

    def test_cli_generates_preview_and_refuses_overwrite_or_partial_output(self):
        root=Path(__file__).resolve().parents[1]
        doc,t,c=drawing()
        with tempfile.TemporaryDirectory() as name:
            p=Path(name)/'input.dxf';p.write_bytes(source(doc));out=Path(name)/'preview'
            cmd=[sys.executable,str(root/'scripts/parse_cad.py'),str(p),'--document-id','test-cad',
                 '--provenance','synthetic_demo','--output-dir',str(out)]
            first=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            before=(out/'snapshot.json').read_bytes()
            second=subprocess.run(cmd,capture_output=True,text=True)
            self.assertEqual(second.returncode,1);self.assertEqual((out/'snapshot.json').read_bytes(),before)
            doc.modelspace().add_arc((0,0),1,0,90);p.write_bytes(source(doc))
            failed=subprocess.run(cmd[:-1]+[str(Path(name)/'failed')],capture_output=True,text=True)
            self.assertEqual(failed.returncode,1);self.assertFalse((Path(name)/'failed').exists())


if __name__=='__main__':
    unittest.main()

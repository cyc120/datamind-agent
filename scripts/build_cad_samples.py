"""Author separate synthetic DXF profile samples and evaluation-only labels."""
import argparse
import hashlib
import json
from pathlib import Path

import ezdxf

ROOT = Path(__file__).resolve().parents[1]
FROZEN = ['ingestion/cad_contract.py','ingestion/cad_dxf.py','ingestion/cad_render.py',
          'scripts/parse_cad.py','scripts/eval_cad_dxf.py','requirements-cad.txt']


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(stage):
    if stage=='acceptance':
        frozen=json.loads((ROOT/'data/cad_demo/dxf_reader_v1_freeze.json').read_text())
        for name,sha in frozen['code_hashes'].items():
            if digest(ROOT/name)!=sha:raise ValueError('CAD_FROZEN_CODE_CHANGED: '+name)
    else:
        frozen=None
    base=ROOT/'data/cad_demo'/stage
    if base.exists():raise FileExistsError(base)
    base.mkdir()
    specs=([('dev-mm',4,20,0.1,True,0,False,False),('dev-no-ref',4,12,0.05,False,10,False,False)] if stage=='development' else
           [('new-mm',4,32,0.25,True,-30,False,False),('new-inch',1,0.75,0.005,True,0.1,False,False),
            ('new-ambiguous',4,18,0.12,False,80,False,False),('new-conflict',4,26,0.2,True,150,True,False),
            ('new-unknown-unit',0,40,0.5,True,-100,False,False),('new-unsupported-arc',4,24,0.1,True,10,False,True)])
    cases=[]
    for i,(name,unit,nominal,tol,linked,shift,conflict,unsupported) in enumerate(specs):
        doc=ezdxf.new('R2010');doc.header['$INSUNITS']=unit;doc.appids.new('DATAMIND')
        for layer in ['DM_DRAWING_ID','DM_REVISION','DM_PART_ID','DM_DIAMETER','GEOMETRY']:doc.layers.new(layer)
        m=doc.modelspace();factor=0.05 if unit==1 else 1
        values={'drawing_id':'CAD-'+('DEV' if stage=='development' else 'NEW')+'-'+str(i+1),
                'drawing_revision':'R'+str(i+2),'part_id':'A'+str(200+i)}
        expected_entities=[];titles={}
        # Different ordering and coordinates from the contract fixture and tests.
        order=['part_id','drawing_id','drawing_revision'] if i%2 else ['drawing_revision','part_id','drawing_id']
        layers={'part_id':'DM_PART_ID','drawing_id':'DM_DRAWING_ID','drawing_revision':'DM_REVISION'}
        for j,key in enumerate(order):
            x,y=shift+5*factor,(55+8*j)*factor
            e=m.add_text(values[key],dxfattribs={'layer':layers[key],'insert':(x,y),'height':(2+i*0.1)*factor})
            titles[key]={'value':values[key],'handle':e.dxf.handle}
            expected_entities.append({'entity_type':'TEXT','handle':e.dxf.handle,'layer':layers[key],
                                      'text':values[key],'insertion':{'x':x,'y':y}})
        radius=nominal/2-(0.5 if conflict else 0)
        circles=[]
        for x in [shift+radius,shift+radius+max(nominal*2,8*factor)]:
            c=m.add_circle((x,radius),radius,dxfattribs={'layer':'GEOMETRY'});circles.append(c)
            expected_entities.append({'entity_type':'CIRCLE','handle':c.dxf.handle,'layer':'GEOMETRY',
                                      'center':{'x':x,'y':radius},'radius':radius})
        line=m.add_line((shift,0),(shift+max(nominal*4,50*factor),0),dxfattribs={'layer':'GEOMETRY'})
        expected_entities.append({'entity_type':'LINE','handle':line.dxf.handle,'layer':'GEOMETRY',
                                 'start':{'x':shift,'y':0},'end':{'x':shift+max(nominal*4,50*factor),'y':0}})
        raw=f'%%c{nominal} +/-{tol}';x,y=shift+nominal,35*factor
        annotation=m.add_text(raw,dxfattribs={'layer':'DM_DIAMETER','insert':(x,y),'height':2*factor})
        expected_entities.append({'entity_type':'TEXT','handle':annotation.dxf.handle,'layer':'DM_DIAMETER',
                                 'text':raw,'insertion':{'x':x,'y':y}})
        if linked:annotation.set_xdata('DATAMIND',[(1000,'diameter_target'),(1005,circles[0].dxf.handle)])
        if unsupported:m.add_arc((shift,0),2,0,90)
        file=base/(name+'.dxf');doc.saveas(file)
        excluded=('CAD_GEOMETRY_ANNOTATION_CONFLICT' if conflict else 'CAD_VALUE_OR_UNIT_UNKNOWN' if unit==0
                  else 'CAD_ASSOCIATION_UNCERTAIN' if not linked else None)
        cases.append(dict(case_id=name,source_file=str(file.relative_to(ROOT)),source_sha256=digest(file),
                          document_id='doc-'+name,expected_error='CAD_ENTITY_TYPE_UNSUPPORTED' if unsupported else None,
                          expected_identity=titles,expected_entities=expected_entities,
                          expected_feature=dict(annotation_raw=raw,annotation_handle=annotation.dxf.handle,
                              nominal_value=str(nominal) if unit else None,tolerance_lower=str(-tol) if unit else None,
                              tolerance_upper=str(tol) if unit else None,unit={0:'unknown',1:'in',4:'mm'}[unit],
                              association_status='confirmed' if linked else 'ambiguous',
                              selected_target=circles[0].dxf.handle if linked else None),
                          expected_gate_reason=excluded))
    suite=dict(suite_version='cad-dxf-'+stage+'-v1',stage=stage,provenance='synthetic_demo',
               limitation='Same-author controlled DXF layer/XDATA profile, not independent factory drawings.',
               frozen_reader=frozen,cases=cases)
    path=ROOT/'data/evaluation'/('cad_dxf_'+stage+'_v1.json')
    with path.open('x') as stream:stream.write(json.dumps(suite,ensure_ascii=False,indent=2)+'\n')
    print(str(path))


if __name__=='__main__':
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--stage',choices=['development','acceptance'],required=True)
    build(ap.parse_args().stage)

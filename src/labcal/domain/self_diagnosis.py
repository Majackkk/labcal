"""Plan-free candidate preparation and explicit decimal recalculation."""
from decimal import Decimal, InvalidOperation, localcontext
import re
from ..errors import LabcalError

GROUPS = {'identity':'信息与页面','numeric':'数据与计算','technical':'技术内容与表达','conclusion':'结果与结论'}
LABELS = {'certificate_number':['证书编号','证书号'], 'instrument_name':['器具名称','计量器具名称'],
 'manufacturer':['制造单位'], 'model':['型号/规格','型号／规格'], 'serial_number':['器具编号','出厂编号'],
 'asset_number':['管理编号','设备管理号'], 'client':['客户单位','送检单位']}


def candidate_fields(payload):
    """Only same-line value or immediate labelled value; never merge the two IDs."""
    lines=payload['pages'][0]['lines'];result={}
    for key,aliases in LABELS.items():
        for i,line in enumerate(lines):
            compact=re.sub(r'\s+','',line['text'])
            alias=next((a for a in aliases if compact.startswith(a)),None)
            if not alias: continue
            value=compact[len(alias):].lstrip(':：')
            selected=line
            if not value and i+1<len(lines):
                selected=lines[i+1];value=selected['text'].strip()
            if value and not any(re.sub(r'\s+','',value).startswith(a) for values in LABELS.values() for a in values):
                result[key]={'value':value,'page':1,'bbox':selected['bbox'],'quote':selected['text'],'origin':'extraction_candidate'}
            break
    return result


def recalculate(spec):
    """Operands must have the same explicitly stated unit. Half-last-digit rounding interval."""
    try:
        if not all(isinstance(spec[k],str) and len(spec[k])<=100 for k in ['indication','reference','reported','resolution']) or not isinstance(spec['unit'],str) or not spec['unit'].strip(): raise ValueError()
        if spec.get('formula')=='relative_to_reference_percent' and spec['unit']!='%': raise ValueError()
        with localcontext() as ctx:
            ctx.prec=40
            x,y,reported,step=[Decimal(spec[k]) for k in ['indication','reference','reported','resolution']]
            if not all(v.is_finite() for v in [x,y,reported,step]) or step<=0: raise ValueError()
            if spec['formula']=='indication_minus_reference': expected=x-y
            elif spec['formula']=='reference_minus_indication': expected=y-x
            elif spec['formula']=='relative_to_reference_percent':
                if y==0: raise ValueError()
                expected=(x-y)/y*100
            else: raise ValueError()
            return {'expected':str(expected),'reported':str(reported),'resolution':str(step),
                    'rounding_compatible':abs(expected-reported)<=step/2,'unit':spec['unit'],'formula':spec['formula']}
    except (KeyError,ValueError,TypeError,InvalidOperation,ZeroDivisionError):
        raise LabcalError('CALCULATION_INPUT_INVALID','复算需明确公式、同单位输入、有限十进制数及正的报告分辨力。')


def automatic_checks(payload):
    checks=[];numbers={}
    for page in payload['pages']:
        for i,line in enumerate(page['lines']):
            text=line['text'];loc={'page':page['page'],'bbox':line['bbox'],'kind':'text','quote':text}
            for match in re.finditer(r'第\s*(\d+)\s*页\s*共\s*(\d+)\s*页|Page\s*(\d+)\s*of\s*(\d+)',text,re.I):
                values=[int(v) for v in match.groups() if v]
                checks.append({'group':'identity','item':'页码对应','state':'candidate_concern' if values!=[page['page'],len(payload['pages'])] else 'no_anomaly_detected',
                               'reason':f'识别页码{values}，文件位置{page["page"]}/{len(payload["pages"])}；只检查提取文字。','location':loc})
            # Only explicit current-certificate labels; references remain candidates for context review.
            m=re.search(r'证书编号\s*[:：]?\s*([A-Za-z][A-Za-z0-9_-]{5,})',re.sub(r'\s+','',text))
            if m: numbers.setdefault(m[1],[]).append(loc)
    checks.append({'group':'identity','item':'证书编号线索','state':'candidate_concern' if len(numbers)>1 else 'no_anomaly_detected' if numbers else 'not_checked',
                   'reason':'编号线索：'+(', '.join(numbers) or '未取得明确编号；需回看原页'),'locations':[loc for values in numbers.values() for loc in values]})
    for group,label in GROUPS.items():
        checks.append({'group':group,'item':label+'原页核查','state':'not_checked','reason':'待智能体结合原页与适用依据检查；本次自动准备不宣称该维度通过。'})
    return checks

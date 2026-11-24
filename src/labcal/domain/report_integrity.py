"""Bounded internal consistency checks; findings from extraction remain candidates."""
from datetime import date
import re
from .certificate_review import finding


def check_integrity(context):
    checks=[];issues=[];pages=context['payload']['pages'];numbers={}
    def add(field,label,signal,loc=None):
        row={'field':field,'label':label,'status':'pending','candidate_signal':signal}
        if loc:row['location']=loc
        checks.append(row)
    for page in pages:
        for line in page.get('lines',[]):
            text=line['text'];loc={'page':page['page'],'bbox':line['bbox'],'kind':'text','quote':text,'line_ids':[line['id']]}
            matches=re.findall(r'第\s*(\d+)\s*页\s*共\s*(\d+)\s*页|Page\s*(\d+)\s*(?:of|/)\s*(\d+)',text,re.I)
            for match in matches:
                current,total=[int(v) for v in match if v]
                consistent=current==page['page'] and total==len(pages)
                add('printed_page_sequence','第'+str(page['page'])+'页页码与总页数','consistent' if consistent else 'inconsistent',loc)
                if not consistent:issues.append(finding(context,'PRINTED_PAGE_SEQUENCE','页码或总页数存在识别疑点',
                    f'原页识别为第{current}页／共{total}页，接入文件对应第{page["page"]}页／共{len(pages)}页。',
                    basis='证书页码内部一致性检查，识别结果仍需回看原页',suggestion='核对页码、附件及文件是否完整。',loc=loc))
            for number in re.findall(r'(?:证书编号|证书号|certificate\s*(?:no\.?|number))\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9_-]{3,})',text,re.I):
                numbers.setdefault(number,loc)
    if len(numbers)>1:
        for number,loc in numbers.items():
            issues.append(finding(context,'CERTIFICATE_NUMBER_VARIANTS','识别到不同证书编号',
                '文件内发现编号：'+', '.join(numbers)+'。需区分当前证书编号与更正／替代引用。',
                basis='跨页编号一致性线索，不能仅凭提取判断原件错误',suggestion='回看该编号及上下文，核对更正或替代说明。',loc=loc))
    add('cross_page_number','跨页证书编号线索','inconsistent' if len(numbers)>1 else 'consistent' if numbers else 'unavailable')
    candidates=context.get('candidate_fields',{})
    try:
        a=date.fromisoformat(candidates['activity_date'][0]['value']);b=date.fromisoformat(candidates['issue_date'][0]['value'])
        loc=candidates['issue_date'][0]['location'];add('candidate_date_order','识别日期先后关系','consistent' if b>=a else 'inconsistent',loc)
        if b<a and not any(c in context['fields'] for c in ['activity_date','issue_date']):
            issues.append(finding(context,'CANDIDATE_DATE_ORDER','识别日期先后存在疑点','识别到签发日期早于实施日期，需回看原页确认。',
                basis='候选日期一致性检查',suggestion='更正识别错误；原件确有问题时取得更正资料。',loc=loc))
    except (KeyError,IndexError,ValueError,TypeError):add('candidate_date_order','识别日期先后关系','unavailable')
    return checks,issues

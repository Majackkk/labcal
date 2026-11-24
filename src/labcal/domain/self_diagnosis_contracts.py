"""Chat review records are agent assertions, separate from technical approval."""
from .report_markup import MARKUP
TEXT={'type':'string','minLength':1}
LOCATION={'type':'object','required':['page','bbox','quote','origin'], 'properties':{
 'page':{'type':'integer','minimum':1},'bbox':{'type':'array','items':{'type':'number','minimum':0,'maximum':1},'minItems':4,'maxItems':4},
 'quote':TEXT,'origin':{'enum':['native_text','visual_page_read']},'image_sha256':TEXT,'markup':MARKUP},'additionalProperties':False}
SCHEMAS={'self-diagnosis-record.v1':{'type':'object','required':['task_hash','author','checks'], 'properties':{
 'task_hash':TEXT,'author':TEXT,'checks':{'type':'array','minItems':1,'items':{'type':'object',
 'required':['extraction_id','group','item','state','reason','basis','suggestion','evidence'], 'properties':{
 'extraction_id':TEXT,'group':{'enum':['identity','numeric','technical','conclusion','filing']},'item':TEXT,
 'state':{'enum':['no_anomaly_detected','confirmed_error','candidate_concern','unable_to_check','not_applicable']},
 'reason':TEXT,'basis':TEXT,'suggestion':TEXT,'evidence':{'type':'array','items':LOCATION},'calculation':{'type':'object'}},'additionalProperties':False}}},'additionalProperties':False}}

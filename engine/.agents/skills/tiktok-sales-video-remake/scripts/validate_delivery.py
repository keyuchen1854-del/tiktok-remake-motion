"""Validate structural delivery invariants; not a visual quality certificate."""
import argparse,json,re,hashlib
from pathlib import Path

FIELDS='shot_id,start_sec,end_sec,duration_sec,scene_group_id,scene_title_cn,shot_title_cn,shot_goal,aspect_ratio,video_type_tag,visual_content_description,location_setting,character_desc,emotion_state,action_blocking,product_desc,must_show,on_screen_text_graphics,camera_shot_size,camera_angle,camera_movement,composition_notes,lighting_atmosphere,color_grading,dialogue_vo_original,dialogue_vo_zh,language_style,emphasis_notes,audio_bgm,audio_sfx,ambient_sound,editing_transition,pacing_notes,constraints_real_shoot,constraints_compliance,reverse_constraints,assets_needed,sentence_mapping,mapping_notes,rewrite_vo_target_language,rewrite_vo_zh_translation,ref_frame_timestamp_sec,ref_frame_basis'.split(',')
def han(s):return len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]',str(s)))
def validate(data,root):
 errors=[];ratios=[];shots=data.get('shots',[]);prev=0.;seen=set()
 if not shots:errors.append('No shots')
 for s in shots:
  sid=s.get('shot_id','MISSING')
  if sid in seen:errors.append(f'Duplicate shot {sid}')
  seen.add(sid)
  missing=set(FIELDS)-set(s)
  if missing:errors.append(f'{sid}: missing {sorted(missing)}');continue
  st,en,du=map(float,(s['start_sec'],s['end_sec'],s['duration_sec']))
  if abs(st-prev)>0.0002:errors.append(f'{sid}: timeline gap or overlap')
  if en<=st or abs(en-st-du)>0.0002:errors.append(f'{sid}: invalid duration')
  if not st<=float(s['ref_frame_timestamp_sec'])<en:errors.append(f'{sid}: reference outside shot')
  prev=en
  a,b=han(s['dialogue_vo_zh']),han(s['rewrite_vo_zh_translation'])
  if a:
   ratio=b/a;ratios.append({'shot_id':sid,'original_han':a,'rewrite_han':b,'ratio':ratio})
   if not .9<=ratio<=1.1:errors.append(f'{sid}: Chinese ratio {ratio}')
  if s.get('product_visible'):
   p=s.get('prompt_en','')
   for lock in ['The product must be EXACTLY the same as the product in the reference product images','Do not invent packaging.']:
    if lock not in p:errors.append(f'{sid}: missing product lock')
 if abs(prev-float(data.get('total_duration_sec',-1)))>0.0002:errors.append('Final end does not equal T')
 refseen=set()
 for r in data.get('references',[]):
  rid=r.get('id')
  if rid in refseen:errors.append(f'Duplicate reference {rid}')
  refseen.add(rid)
  if r.get('shot_id') not in seen:errors.append(f'{rid}: unknown shot')
  for key in ['source_path','edited_path']:
   path=r.get(key)
   if not path or not (root/Path(path)).is_file():errors.append(f'{rid}: missing {key}')
  if r.get('generation_status') not in ['generated_reviewed','preserved_unedited_subject_absent']:
   errors.append(f'{rid}: unresolved generation status')
  sp,ep=root/Path(r.get('source_path','')),root/Path(r.get('edited_path',''))
  if sp.is_file() and ep.is_file():
   equal=hashlib.sha256(sp.read_bytes()).digest()==hashlib.sha256(ep.read_bytes()).digest()
   if r.get('generation_status')=='generated_reviewed' and equal:errors.append(f'{rid}: edited file equals original')
   if r.get('generation_status')=='preserved_unedited_subject_absent' and not equal:errors.append(f'{rid}: supposedly unchanged reference differs')
 for s in shots:
  if not s.get('reference_ids'):errors.append(f'{s["shot_id"]}: no mapped reference')
  for rid in s.get('reference_ids',[]):
   if rid not in refseen:errors.append(f'{s["shot_id"]}: unknown reference {rid}')
 return {'structure_pass':not errors,'errors':errors,'ratios':ratios,'shots':len(shots),'references':len(refseen),'visual_quality':'requires actual image review','video_generated':data.get('video_generated',False)}
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('delivery');ap.add_argument('--out');args=ap.parse_args()
 file=Path(args.delivery).resolve();out=validate(json.loads(file.read_text(encoding='utf-8')),file.parent)
 if args.out:Path(args.out).write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(out,ensure_ascii=False,indent=2));raise SystemExit(0 if out['structure_pass'] else 1)

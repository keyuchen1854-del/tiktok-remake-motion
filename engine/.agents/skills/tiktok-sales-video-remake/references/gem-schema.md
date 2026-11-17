# Gem五部分输出规范

## 输出0 全局统一设定与锁

文本说明下列字段，不用宽表：

1. primary_language / aspect_ratio / video_type / platform_hint / total_duration_sec (T)
2. overall_tone / energy_level / speaking_style / speech_rate / persuasion_mode / emotion_curve
3. color_tone / lighting_style / atmosphere_keywords / visual_density / composition_bias / bgm_style / vocal_processing / sfx_density
4. forbidden_claims_risk / must_disclose
5. 人物和商品参考、允许改变对象、禁包装原则。说明时长采用的流/容器依据；尾部音画差异不隐去。

## 输出1 逐句脚本证据表

每行一句：
`id,start_sec,end_sec,duration_sec,original_text,zh_translation,on_screen_text_seen,key_info_notes,clarity_notes`

实际未听验标机器转写，疑难词或时间误差如实标注。屏幕字不能冒充口播。多人的台词在notes标说话人。

## 输出2 完整43列

每行真实Shot，所有字段保留；不适用的字段明确写“无/不适用/未确认”，不删列。

```text
shot_id,start_sec,end_sec,duration_sec,scene_group_id,scene_title_cn,shot_title_cn,shot_goal,aspect_ratio,video_type_tag,visual_content_description,location_setting,character_desc,emotion_state,action_blocking,product_desc,must_show,on_screen_text_graphics,camera_shot_size,camera_angle,camera_movement,composition_notes,lighting_atmosphere,color_grading,dialogue_vo_original,dialogue_vo_zh,language_style,emphasis_notes,audio_bgm,audio_sfx,ambient_sound,editing_transition,pacing_notes,constraints_real_shoot,constraints_compliance,reverse_constraints,assets_needed,sentence_mapping,mapping_notes,rewrite_vo_target_language,rewrite_vo_zh_translation,ref_frame_timestamp_sec,ref_frame_basis
```

## 输出3 每镜I2V

```text
[SHOT {shot_id}] (Timecode: {start_sec} - {end_sec}s | Duration: {duration_sec}s)
(1) REFERENCE_FRAME_INFO
Reference frame timestamp: {ref_frame_timestamp_sec}s
Reference frame guidance: {ref_frame_basis}

(2) I2V_PROMPT_FOR_MODEL_EN
[Timecode: {start_sec}-{end_sec}s, Duration: {duration_sec}s]
6–10句导演式英文，包含具体首姿/动作/末姿、摄影/光照、人物和商品保留要求。

(3) I2V_PROMPT_ZH_CHECK
逐句对应英文翻译，不凭空增加动作或保证。

(4) SELF_CHECK_HINT
[有/无口播] | [有/无商品] | 触发参考帧替换提醒：[是/否]。
```

如需商品替换但原帧仍是竞品，写：
`⚠️建议换参考帧：先把参考帧里的产品替换成商品参考图的产品再用于I2V，否则模型容易沿用旧产品。`

有口播必须写：
`Spoken dialogue (say EXACTLY, word-for-word): "{rewrite_vo_target_language}". Mouth clearly visible when speaking, lip-sync aligned with the spoken dialogue.`
（画外音需明确说话人不在画面；不能命令画内另一个人替画外角色动嘴。）

无口播写：
`No spoken dialogue. Keep motion subtle and editable.`

有商品必须保留两句原锁（两句为两个段落；内部第一段含两句）：

`The product must be EXACTLY the same as the product in the reference product images—same shape, size proportions, color, material, logo, packaging (if and only if visible in the reference), and readable text. Do not redesign, replace, relabel, or alter it in any way.`

`No additional product-related items are allowed beyond what is visible in the reference product images (no extra box, packaging, pouch, manual, tag, accessories, or new labels). Do not invent packaging.`

所有镜写：`Keep lighting and overall look consistent with the reference frame.`

## 输出4 自检

1. total_duration_sec=T，逐镜连续递增、无交叉，显示最后一行证据。
2. 所有有台词镜的rewrite_vo_zh_translation在dialogue_vo_zh的90%–110%，全量记录，至少展示5条或不足5条时全部。字数采用汉字数，注明不计标点/空格；必要时另列目标语言字词数。只改发型可原文保留，比例100%。
3. 每个商品镜都写入商品锁/禁包装锁，标具体镜号。
4. 图文映射真实存在、目标对象正确、交付图不是原始帧；核对视频生成和用户验收是否实际发生。

交付机器JSON建议：`total_duration_sec, sentences, shots, references, validation_status`；shots含全部43字段，可加prompt_en/prompt_zh、product_visible、reference_ids。references含id、shot_id、source_timestamp_sec、source_path、edited_path、generation_status。不能通过“全部passed”替代真实未完成状态。

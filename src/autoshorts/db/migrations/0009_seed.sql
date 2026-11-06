SELECT as_migration_guard(9);

INSERT INTO schedules (name, task_kind, resource_class, interval_sec, anchor_local_time, payload) VALUES
 ('task_reaper','task_reaper','light',60,NULL,'{}'),
 ('alerts_eval','alerts_eval','light',60,NULL,'{}'),
 ('metrics_poll','metrics_poll','light',300,NULL,'{}'),                -- берёт публикации с next_poll_at <= now()
 ('competitor_discovery','competitor_discovery','light',1800,NULL,'{}'),
 ('competitor_stats_poll','competitor_stats_poll','light',900,NULL,'{}'),
 ('outlier_compute','outlier_compute','light',1800,NULL,'{}'),
 ('yt_analytics_pull','yt_analytics_pull','light',86400,'07:00','{}'),
 ('channel_stats','channel_stats','light',86400,'06:00','{}'),
 ('stats_rollup_recent','stats_rollup','light',900,NULL,'{"days":3}'),
 ('stats_rollup_full','stats_rollup','light',86400,'03:30','{"days":35}'),
 ('cleanup','cleanup','light',86400,'04:00','{}'),
 ('baselines_refresh','baselines_refresh','light',604800,NULL,'{}'),
 ('brief_field_stats','brief_field_stats','light',604800,NULL,'{}'),
 ('rss_health_check','rss_health_check','light',86400,'05:00','{}')
ON CONFLICT (name) DO NOTHING;

INSERT INTO provider_configs (key, kind, enabled, is_default, params, secret_name) VALUES
 ('openai_compat','llm',true,true,'{"base_url":null,"model":"gpt-4o-mini","structured":"json_schema"}','LLM_API_KEY'),
 ('gigachat_lite','llm',false,false,'{"model":"GigaChat-Lite","scope":"GIGACHAT_API_PERS"}','GIGACHAT_AUTH_KEY'),
 ('mock_llm','llm',true,false,'{}',NULL),
 ('edge_tts','tts',true,true,'{"voice":"ru-RU-DmitryNeural","boundary":"WordBoundary"}',NULL),
 ('silero_v5_cis_base','tts',false,false,'{"speaker":"auto"}',NULL),
 ('fal_minimax_speech','tts',false,false,'{"model":"fal-ai/minimax/speech-2.8-hd"}','FAL_API_KEY'),
 ('mock_tts','tts',true,false,'{}',NULL),
 ('fal_flux_schnell','image',true,true,'{"model":"fal-ai/flux/schnell","price_usd":0.003}','FAL_API_KEY'),
 ('mock_image','image',true,false,'{}',NULL),
 ('gigaam_v3_onnx','asr',true,true,'{"model":"gigaam-v3-e2e-ctc","quantization":"int8","threads":2}',NULL),
 ('yandex_speechkit','asr',false,false,'{"mode":"deferred"}','YANDEX_SPEECHKIT_API_KEY'),
 ('openai_compat_asr','asr',false,false,'{"model":null}','ASR_CLOUD_API_KEY'),
 ('mock_asr','asr',true,false,'{}',NULL),
 ('yunet','faces',true,true,'{"model":"face_detection_yunet_2026may.onnx"}',NULL),
 ('gemini_flash_lite','vision',false,false,'{}','GEMINI_API_KEY')
ON CONFLICT (key) DO NOTHING;

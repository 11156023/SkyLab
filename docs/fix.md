P1 — 中等問題
#	問題	檔案:行號
1	_to_bool vs safe_bool 行為分歧（處理 "True"/"on"/"checked" 不同）	service.py:44 vs utils.py:65
2	RubricAnalysis 衍生欄位（total_items 等）可被獨立寫入，無驗證	schemas.py:47-59
3	當 template_commands=None 時 check_steps 完全未驗證	service.py:58-97
4	Artifact name 缺乏 UNIQUE(group_id, name, template_key) 約束	script_artifact_service.py:830 + model
5	_ensure_group_access 在 3 個檔案中逐字重複（files.py + scripts.py + rubric.py）	3 個路由檔案
6	SessionDep 型別標註誤用於一般輔助函式（應為 Session）	teacher_judge_files.py:40
7	服務層廣泛直接 raise HTTPException（應拋領域例外）	service.py, file_service.py, rubric_parser.py 等
8	rubric_parser.py 在迴圈內做延遲匯入（每個 XML child 都 import）	rubric_parser.py:146,150
9	VLLM 無 FastAPI shutdown 生命週期 — HTTP 連線洩漏	vllm_client.py:64-66
10	min_p / presence_penalty 已配置但 TeacherJudgeSettings 未公開屬性	config.py + system-ai.json
11	Schema 中 UUID/datetime 型別為 str 而非 uuid.UUID/datetime	schemas.py:149-167
12	rubric_snapshot_json 缺少 DB-level default（其他 JSON 欄位有）	teacher_judge_script_artifact.py:64-67
13	teacher_judge_script_run.py 缺少 from __future__ import annotations	teacher_judge_script_run.py:1
14	close_http_client 已宣告但專案中完全無任何 import	rubric_service.py:10
15	_except_handler_appends_errors 有 false positive（errors.clear() 會通過）	script_quality_validator.py:252-263
16	check_script_quality 在發現第一個 errors.append 違規後 break	script_quality_validator.py:532

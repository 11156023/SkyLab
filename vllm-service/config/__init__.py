"""設定模組 - 統一管理所有參數，優先級: .env > config 預設值。

請直接從子模組匯入（config.settings、config.multi_model）；本套件刻意不做
re-export，避免 import config.settings 時連帶載入 multi_model／model_deployment。
"""

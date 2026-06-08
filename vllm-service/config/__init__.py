"""設定模組 - 統一管理所有參數，優先級: .env > config 預設值"""

from config.multi_model import (
	GatewayConfig,
	GatewayRoute,
	ModelInstanceConfig,
	build_gateway_routes,
	find_route_for_model,
	load_gateway_config,
	load_model_instances,
	validate_cluster_resources,
)
from config.settings import Settings, get_settings

__all__ = [
	"Settings",
	"get_settings",
	"ModelInstanceConfig",
	"GatewayConfig",
	"GatewayRoute",
	"load_model_instances",
	"load_gateway_config",
	"build_gateway_routes",
	"validate_cluster_resources",
	"find_route_for_model",
]

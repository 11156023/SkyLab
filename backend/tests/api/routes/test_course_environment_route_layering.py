"""課程環境路由搬成「路由 → service／schemas」之後，對外契約不能變。

路由模組只剩權限與參數：請求 schemas 在 ``app.schemas.course_environment``，
驗證、落地、序列化與發布在 ``environment_service``。
"""

import inspect

from pydantic import BaseModel

from app.api.routes import course_environments as routes
from app.schemas import course_environment as schemas
from tests.utils.routes import iter_api_routes

EXPECTED = {
    ("GET", "/course-environments"),
    ("GET", "/course-environments/published"),
    ("POST", "/course-environments/drafts"),
    ("PUT", "/course-environments/{environment_id}/draft"),
    ("GET", "/course-environments/{environment_id}"),
    ("POST", "/course-environments"),
    ("PUT", "/course-environments/{environment_id}"),
    ("PATCH", "/course-environments/{environment_id}/basics"),
    ("POST", "/course-environments/{environment_id}/files"),
    ("GET", "/course-environments/{environment_id}/files/{file_id}"),
    ("DELETE", "/course-environments/{environment_id}/files/{file_id}"),
    ("POST", "/course-environments/{environment_id}/publish"),
    ("DELETE", "/course-environments/{environment_id}"),
}


def test_every_endpoint_is_still_registered() -> None:
    registered = {
        (method, path)
        for path, context in iter_api_routes(routes.router.routes)
        for method in context.methods or ()
    }
    assert EXPECTED <= registered


def test_route_module_defines_no_request_models() -> None:
    local_models = [
        name
        for name, value in vars(routes).items()
        if inspect.isclass(value)
        and issubclass(value, BaseModel)
        and value.__module__ == routes.__name__
    ]
    assert local_models == []


def test_route_uses_the_shared_schemas() -> None:
    assert routes.EnvironmentDraftIn is schemas.EnvironmentDraftIn
    assert routes.EnvironmentCreate is schemas.EnvironmentCreate

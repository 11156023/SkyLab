"""VMTemplateUpdate：NOT NULL 欄位明確送 null 要在 schema 層就回 422。"""

import pytest
from pydantic import ValidationError

from app.schemas.template import VMTemplateUpdate


@pytest.mark.parametrize(
    "field", ["name", "visibility", "allow_password_change", "requires_gpu"]
)
def test_explicit_null_rejected_for_non_nullable_fields(field: str) -> None:
    with pytest.raises(ValidationError):
        VMTemplateUpdate.model_validate({field: None})


def test_nullable_fields_still_accept_null() -> None:
    data = VMTemplateUpdate.model_validate(
        {"description": None, "default_cores": None, "default_memory": None}
    )
    assert data.model_dump(exclude_unset=True) == {
        "description": None,
        "default_cores": None,
        "default_memory": None,
    }


def test_omitted_fields_stay_unset() -> None:
    data = VMTemplateUpdate.model_validate({"requires_gpu": True})
    assert data.model_dump(exclude_unset=True) == {"requires_gpu": True}


def test_model_construct_bypasses_validator_for_service_level_tests() -> None:
    # service 端的 400 檢查仍要能測：model_construct 不跑 validator，但 null 仍算有送
    data = VMTemplateUpdate.model_construct(name=None)
    assert data.model_dump(exclude_unset=True) == {"name": None}

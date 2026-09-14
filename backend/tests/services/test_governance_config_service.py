"""治理設定跨欄位規則（純函式，不依賴 DB）。"""

import pytest

from app.exceptions import BadRequestError
from app.services.governance.config_service import validate_idle_timing


class TestValidateIdleTiming:
    def test_notify_before_grace_ok(self) -> None:
        validate_idle_timing(notify_after_hours=12, grace_hours=24)

    @pytest.mark.parametrize("notify_after", [24, 25])
    def test_notify_not_before_grace_rejected(self, notify_after: int) -> None:
        with pytest.raises(BadRequestError):
            validate_idle_timing(notify_after_hours=notify_after, grace_hours=24)

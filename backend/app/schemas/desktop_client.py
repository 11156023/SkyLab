"""桌面用戶端裝置授權（device auth）API schemas。"""

from pydantic import BaseModel


class DeviceCodeResponse(BaseModel):
    device_code: str
    login_url: str
    expires_in: int


class DeviceApproveRequest(BaseModel):
    device_code: str


class DevicePollResponse(BaseModel):
    status: str  # "pending" | "approved"
    access_token: str | None = None

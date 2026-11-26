"""Pydantic request schemas for VibeOne HTTP APIs."""

from typing import Dict, List, Optional

from pydantic import BaseModel


class SwitchProfileRequest(BaseModel):
    profile: str
    force: bool = False
    scope: str = "client"


class ReloadProfileRequest(BaseModel):
    profile: str
    force: bool = False


class StartProfileLoginRequest(BaseModel):
    profile: str
    backend: Optional[str] = None
    create_if_missing: bool = False
    logout_first: bool = False


class SubmitProfileLoginCodeRequest(BaseModel):
    code: str


class AuthRequest(BaseModel):
    token: str


class PasskeyRegisterVerifyRequest(BaseModel):
    id: str
    attestationObject: str
    clientDataJSON: str
    label: str = ""


class PasskeyLoginVerifyRequest(BaseModel):
    id: str
    authenticatorData: str
    clientDataJSON: str
    signature: str


class ModeRequest(BaseModel):
    mode_id: str
    profile: Optional[str] = None
    session_id: Optional[str] = None


class ModelRequest(BaseModel):
    model_id: str
    profile: Optional[str] = None
    session_id: Optional[str] = None


class NewSessionRequest(BaseModel):
    workdir: Optional[str] = None
    profile: Optional[str] = None


class SwitchSessionRequest(BaseModel):
    session_id: str
    workdir: Optional[str] = None
    title: Optional[str] = None
    profile: Optional[str] = None


class SyncSessionRequest(BaseModel):
    session_id: Optional[str] = None
    force: bool = True
    profile: Optional[str] = None


class RemoveSessionRequest(BaseModel):
    session_id: str
    profile: Optional[str] = None


class ArchiveSessionsRequest(BaseModel):
    session_ids: List[str]
    profile: Optional[str] = None


class RemoveWorkdirRequest(BaseModel):
    workdir: str
    profile: Optional[str] = None


class PushSubscribeRequest(BaseModel):
    endpoint: str
    keys: Dict[str, str]


class PushUnsubscribeRequest(BaseModel):
    endpoint: str

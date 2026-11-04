"""傳輸與遠端錯誤，不包含機密輸入。"""


class ProtocolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class RemoteError(Exception):
    def __init__(self, error: dict):
        super().__init__(error["message"])
        self.code = error["code"]
        self.details = error["details"]

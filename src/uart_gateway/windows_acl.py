"""以 Win32 Handle 檢查及保護目前使用者的私有檔案。"""

from ctypes import POINTER, Structure, WinDLL, WinError, byref, c_void_p, create_string_buffer, get_last_error, sizeof
from ctypes.wintypes import BOOL, BYTE, DWORD, HANDLE, LPCWSTR, WORD

READ_CONTROL = 0x00020000
WRITE_DAC = 0x00040000
TOKEN_QUERY = 0x0008
TOKEN_USER = 1
ERROR_INSUFFICIENT_BUFFER = 122
INVALID_HANDLE = c_void_p(-1).value
FILE_SHARE_ALL = 0x0007
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_ATTRIBUTE_DIRECTORY = 0x0010
FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
SE_FILE_OBJECT = 1
OWNER_SECURITY_INFORMATION = 0x00000001
DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
SE_DACL_PROTECTED = 0x1000
ACL_REVISION = 2
ACL_SIZE_INFORMATION_CLASS = 2
FILE_ALL_ACCESS = 0x001F01FF
OBJECT_INHERIT_ACE = 0x01
CONTAINER_INHERIT_ACE = 0x02
INHERIT_ONLY_ACE = 0x08
ACCESS_ALLOWED_ACE = 0
ACCESS_DENIED_ACE = 1
ACL_HEADER_SIZE = 8


class _FileAttributeTagInfo(Structure):
    _fields_ = [("attributes", DWORD), ("tag", DWORD)]


class _TokenUser(Structure):
    _fields_ = [("sid", c_void_p), ("attributes", DWORD)]


class _AclSizeInformation(Structure):
    _fields_ = [("count", DWORD), ("used", DWORD), ("free", DWORD)]


class _Ace(Structure):
    _fields_ = [("type", BYTE), ("flags", BYTE), ("size", WORD), ("mask", DWORD), ("sid_start", DWORD)]


def _bind(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = arguments
    return function


_kernel = WinDLL("kernel32", use_last_error=True)
_security = WinDLL("advapi32", use_last_error=True)
_current_process = _bind(_kernel, "GetCurrentProcess", HANDLE)
_close = _bind(_kernel, "CloseHandle", BOOL, HANDLE)
_free = _bind(_kernel, "LocalFree", HANDLE, HANDLE)
_open = _bind(_kernel, "CreateFileW", HANDLE, LPCWSTR, DWORD, DWORD, c_void_p, DWORD, DWORD, HANDLE)
_file_info = _bind(_kernel, "GetFileInformationByHandleEx", BOOL, HANDLE, DWORD, c_void_p, DWORD)
_open_token = _bind(_security, "OpenProcessToken", BOOL, HANDLE, DWORD, POINTER(HANDLE))
_token_info = _bind(_security, "GetTokenInformation", BOOL, HANDLE, DWORD, c_void_p, DWORD, POINTER(DWORD))
_get_security = _bind(_security, "GetSecurityInfo", DWORD, HANDLE, DWORD, DWORD,
                      POINTER(c_void_p), POINTER(c_void_p), POINTER(c_void_p), POINTER(c_void_p), POINTER(c_void_p))
_set_security = _bind(_security, "SetSecurityInfo", DWORD, HANDLE, DWORD, DWORD, c_void_p, c_void_p, c_void_p, c_void_p)
_descriptor_control = _bind(_security, "GetSecurityDescriptorControl", BOOL, c_void_p, POINTER(WORD), POINTER(DWORD))
_acl_info = _bind(_security, "GetAclInformation", BOOL, c_void_p, c_void_p, DWORD, DWORD)
_get_ace = _bind(_security, "GetAce", BOOL, c_void_p, DWORD, POINTER(c_void_p))
_equal_sid = _bind(_security, "EqualSid", BOOL, c_void_p, c_void_p)
_sid_length = _bind(_security, "GetLengthSid", DWORD, c_void_p)
_initialize_acl = _bind(_security, "InitializeAcl", BOOL, c_void_p, DWORD, DWORD)
_add_allow = _bind(_security, "AddAccessAllowedAceEx", BOOL, c_void_p, DWORD, DWORD, DWORD, c_void_p)


def _require(success):
    if not success:
        raise WinError(get_last_error())


def _current_user():
    token = HANDLE()
    _require(_open_token(_current_process(), TOKEN_QUERY, byref(token)))
    try:
        size = DWORD()
        if not _token_info(token, TOKEN_USER, None, 0, byref(size)) and get_last_error() != ERROR_INSUFFICIENT_BUFFER:
            raise WinError(get_last_error())
        buffer = create_string_buffer(size.value)
        _require(_token_info(token, TOKEN_USER, buffer, len(buffer), byref(size)))
        # SID 指標位於這個緩衝區內，呼叫端須保留緩衝區。
        return buffer
    finally:
        _close(token)


def _inspect(handle, sid, *, verify_rules, require_protection=False, exact_flags=None):
    owner, dacl, descriptor = c_void_p(), c_void_p(), c_void_p()
    error = _get_security(handle, SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                          byref(owner), None, byref(dacl), None, byref(descriptor))
    if error:
        raise WinError(error)
    try:
        if not owner or not _equal_sid(owner, sid):
            raise ValueError("Windows ACL 擁有者不是目前使用者。")
        if not verify_rules:
            return
        if not dacl:
            raise ValueError("Windows ACL 缺少有效的 DACL。")
        if require_protection:
            control, revision = WORD(), DWORD()
            _require(_descriptor_control(descriptor, byref(control), byref(revision)))
            if not control.value & SE_DACL_PROTECTED:
                raise ValueError("Windows ACL 仍允許繼承。")
        info = _AclSizeInformation()
        _require(_acl_info(dacl, byref(info), sizeof(info), ACL_SIZE_INFORMATION_CLASS))
        if exact_flags is not None and info.count != 1:
            raise ValueError("Windows ACL 與私有設定不一致。")
        own_full_control = False
        for index in range(info.count):
            address = c_void_p()
            _require(_get_ace(dacl, index, byref(address)))
            ace = _Ace.from_address(address.value)
            if exact_flags is not None and (ace.type != ACCESS_ALLOWED_ACE or
                    ace.flags != exact_flags or ace.mask != FILE_ALL_ACCESS):
                raise ValueError("Windows ACL 與私有設定不一致。")
            if ace.type == ACCESS_DENIED_ACE:
                continue
            # 只接受能完整判讀的允許規則，未知類型一律拒絕。
            if ace.type != ACCESS_ALLOWED_ACE or not _equal_sid(address.value + _Ace.sid_start.offset, sid):
                raise ValueError("Windows ACL 含其他身分或無法驗證的允許規則。")
            if not ace.flags & INHERIT_ONLY_ACE and ace.mask & FILE_ALL_ACCESS == FILE_ALL_ACCESS:
                own_full_control = True
        if require_protection and not own_full_control:
            raise ValueError("Windows ACL 未授予目前使用者完整控制。")
    finally:
        _free(descriptor)


def check_acl(path: str, *, protect: bool) -> None:
    access = READ_CONTROL | (WRITE_DAC if protect else 0)
    handle = _open(path, access, FILE_SHARE_ALL, None, OPEN_EXISTING,
                   FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None)
    _require(handle != INVALID_HANDLE)
    try:
        info = _FileAttributeTagInfo()
        _require(_file_info(handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, byref(info), sizeof(info)))
        if info.attributes & FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("Windows ACL 不接受重新解析點。")
        user = _current_user()
        sid = _TokenUser.from_buffer(user).sid
        if protect:
            flags = OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE if info.attributes & FILE_ATTRIBUTE_DIRECTORY else 0
            try:
                _inspect(handle, sid, verify_rules=True, require_protection=True, exact_flags=flags)
            except ValueError:
                _inspect(handle, sid, verify_rules=False)
            else:
                # 已有相同的受保護規則，避免每次存收據都重設目錄及繼承。
                return
            acl = create_string_buffer(ACL_HEADER_SIZE + _Ace.sid_start.offset + _sid_length(sid))
            _require(_initialize_acl(acl, len(acl), ACL_REVISION))
            _require(_add_allow(acl, ACL_REVISION, flags, FILE_ALL_ACCESS, sid))
            error = _set_security(handle, SE_FILE_OBJECT, DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION,
                                  None, None, acl, None)
            if error:
                raise WinError(error)
        _inspect(handle, sid, verify_rules=True, require_protection=protect)
    finally:
        _close(handle)

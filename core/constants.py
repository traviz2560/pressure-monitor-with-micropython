# Using simple strings for enums is fine in MicroPython to avoid Enum import.
class DeviceState:
    UNINITIALIZED = "UNINITIALIZED"
    INITIALIZING = "INITIALIZING"
    READY = "READY"
    FAILED = "FAILED"
    DISABLED = "DISABLED" # For devices manually disabled

# OS Message Types
OS_MSG_TYPE_HW_ACTION = 'hw_action' # Service -> OS to request HW op
OS_MSG_TYPE_HW_ACTION_RESPONSE = 'hw_action_response' # OS -> Service with result of HW op

OS_MSG_TYPE_HW_RESOURCE_LOCK_REQUEST = 'hw_resource_lock_request' # For exclusive delegation
OS_MSG_TYPE_HW_RESOURCE_LOCK_RESPONSE = 'hw_resource_lock_response'

OS_MSG_TYPE_OS_COMMAND = 'os_command' #! Renamed for clarity (was 'command')
OS_MSG_TYPE_SERVICE_COMMAND = 'service_command' # OS/Service -> Service for service-specific actions

OS_MSG_TYPE_STATUS_REPORT = 'status_report' # OS -> Requester with system status
OS_MSG_TYPE_LOG = 'log_message' # Service -> OS for centralized logging (optional)
OS_MSG_TYPE_BROADCAST = 'broadcast' # Special recipient for OS to distribute
OS_MSG_TYPE_STORAGE_UPDATE = 'storage_update' #! Specific type for storage changes

# OS Command Actions (for msg_type OS_MSG_TYPE_OS_COMMAND)
OS_CMD_CREATE_SERVICE = 'create_service'
OS_CMD_STOP_SERVICE = 'stop_service'
OS_CMD_PAUSE_SERVICE = 'pause_service'
OS_CMD_RESUME_SERVICE = 'resume_service'
OS_CMD_SHUTDOWN = 'shutdown'
OS_CMD_SAVE_STORAGE = 'save_storage'
OS_CMD_GET_STATUS = 'get_status'
OS_CMD_REINIT_HW_MANAGER = 'reinit_hw_manager'

# Service Command Actions (for msg_type OS_MSG_TYPE_SERVICE_COMMAND, sent to specific services)
SVC_CMD_PAUSE = 'pause'
SVC_CMD_RESUME = 'resume'
SVC_CMD_STOP = 'stop' # Request a service to stop itself
SVC_CMD_GET_INFO = 'get_info' # Request service-specific status/info
SVC_CMD_SET_LAYOUT = 'set_display_layout' #! Specific service commands
SVC_CMD_SET_BACKLIGHT = 'set_display_backlight' #!
SVC_CMD_SHOW_TEMP_MSG = 'show_temporary_message' #!
SVC_CMD_SHOW_BOOT_STATUS = 'show_boot_status_overview' #!

# Hardware Resource Actions (for delegation)
HW_RES_ACTION_LOCK = 'request_resource_lock'
HW_RES_ACTION_RELEASE = 'release_resource_lock'

# print("[core/constants] Constants loaded.")
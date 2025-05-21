import time

_DEFAULT_LOG_LEVEL_INTERNAL = "INFO"
_CURRENT_LOG_LEVEL_NAME = _DEFAULT_LOG_LEVEL_INTERNAL
_LOG_LEVEL_MAP = {'DEBUG': 0, 'INFO': 1, 'WARN': 2, 'ERROR': 3, 'CRITICAL': 4}
_CURRENT_LOG_LEVEL_INT = _LOG_LEVEL_MAP.get(_CURRENT_LOG_LEVEL_NAME, 1)

def configure_default_log_level(level_name: str):
    global _CURRENT_LOG_LEVEL_NAME, _CURRENT_LOG_LEVEL_INT
    level_name_upper = level_name.upper()
    if level_name_upper in _LOG_LEVEL_MAP:
        _CURRENT_LOG_LEVEL_NAME = level_name_upper
        _CURRENT_LOG_LEVEL_INT = _LOG_LEVEL_MAP[level_name_upper]
        # print(f"[{time.ticks_ms()}][LoggerUtil][INFO] Global log level set to: {_CURRENT_LOG_LEVEL_NAME}")
    else:
        print(f"[{time.ticks_ms()}][LoggerUtil][ERROR] Invalid global log level for configuration: {level_name}")

class Logger:
    def __init__(self, module_name: str, level_name: str = None):
        self.module_name = module_name
        self.effective_level_name = _CURRENT_LOG_LEVEL_NAME
        self.effective_level_int = _CURRENT_LOG_LEVEL_INT

        if level_name:
            level_upper = level_name.upper()
            if level_upper in _LOG_LEVEL_MAP:
                self.effective_level_name = level_upper
                self.effective_level_int = _LOG_LEVEL_MAP[level_upper]
            # else: fallback silently to global level

    def _log(self, level: str, message: str):
        msg_level_int = _LOG_LEVEL_MAP.get(level.upper(), 1)  # Default to INFO
        if msg_level_int >= self.effective_level_int:
            try:
                log_msg = f"[{time.ticks_ms()}][{self.module_name}][{level.upper()}] {message}"
            except Exception:
                log_msg = f"[RAW_LOG][{self.module_name}][{level.upper()}] Log formatting error, original message: {message}"
            print(log_msg)

    def debug(self, msg): self._log('DEBUG', msg)
    def info(self, msg): self._log('INFO', msg)
    def warn(self, msg): self._log('WARN', msg)
    def error(self, msg): self._log('ERROR', msg)
    def critical(self, msg): self._log('CRITICAL', msg)

def get_logger(module_name: str, service_log_level_name: str = None):
    return Logger(module_name, service_log_level_name)

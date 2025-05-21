import asyncio
import sys

from utils import configure_default_log_level, get_logger
from core import MicroOS
from env import STORAGE_PATH, DEFAULT_LOG_LEVEL

# Configure logging early
configure_default_log_level(DEFAULT_LOG_LEVEL)
log = get_logger(__name__) #! Logger for main.py

async def main():
    """Punto de entrada asíncrono principal."""
    log.info("Initializing MicroOS...") #! Use logger
    os_instance = MicroOS(storage_path=STORAGE_PATH)

    try:
        await os_instance.run()
    except KeyboardInterrupt:
        log.info("\nKeyboardInterrupt, initiating OS shutdown...") #! Use logger
        if os_instance and os_instance.is_running: #! Check if OS was running
            await os_instance.shutdown()
        await asyncio.sleep_ms(500) # Give time for tasks to cleanup
    except Exception as e:
        log.critical("FATAL ERROR during OS execution:") #! Use logger
        sys.print_exception(e)
    finally:
        log.info("Application main() finished.") #! Use logger

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[main.py] Execution interrupted forcefully (asyncio.run level).")
    except Exception as e:
        print("[main.py] FATAL UNHANDLED EXCEPTION at asyncio.run level:")
        sys.print_exception(e)
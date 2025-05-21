import sys #! Added sys for print_exception
import asyncio
from core import Service
from core.constants import OS_MSG_TYPE_OS_COMMAND, OS_CMD_SAVE_STORAGE #! Specific constants

class StorageSaverService(Service):
    async def setup(self):
        await super().setup()
        self.interval_s = self.config.get("save_interval_s", 300)
        self.log.info(f"Periodic storage save enabled. Interval: {self.interval_s}s")
        # No hardware interaction, so setup is minimal.

    async def run(self):
        # await super().run() # Not needed as we have a custom loop
        self.log.info("StorageSaverService run loop started.")
        try:
            while self.is_running: 
                await self._paused_event.wait()
                
                # Check if storage is dirty before sending save command
                # is_storage_dirty() needs to be implemented in MicroOS class
                if hasattr(self.os, 'is_storage_dirty') and self.os.is_storage_dirty():
                    self.log.info("Storage is dirty, requesting save operation from OS.")
                    self.send_message('os', OS_MSG_TYPE_OS_COMMAND, {'action': OS_CMD_SAVE_STORAGE})
                    # OS will typically clear the dirty flag after a successful save.
                # else:
                    # self.log.debug("Periodic check: storage is clean, no save needed.")

                slept_s = 0
                # Sleep in chunks to be responsive, but also check dirty flag more often
                # This allows a save to happen sooner if storage becomes dirty during the interval
                check_dirty_interval = min(self.interval_s, 30) # Check dirty flag every 30s or interval, whichever is shorter

                while slept_s < self.interval_s and self.is_running and self._paused_event.is_set():
                    await asyncio.sleep(1) 
                    slept_s += 1
                    if slept_s % check_dirty_interval == 0: # Check dirty flag periodically
                        if hasattr(self.os, 'is_storage_dirty') and self.os.is_storage_dirty():
                            self.log.debug("Storage became dirty during sleep interval, breaking to save.")
                            break # Exit inner sleep loop to trigger save attempt sooner
                
                if not self.is_running: break

        except asyncio.CancelledError:
            self.log.info("StorageSaverService run loop cancelled.")
        except Exception as e:
            self.log.error(f"Unhandled exception in StorageSaverService run loop: {e}")
            sys.print_exception(e) #! Print traceback
        self.log.info("StorageSaverService run loop finished.")
    
    async def cleanup(self):
        await super().cleanup()
        # Potentially force a save on cleanup if dirty and OS is still capable
        if hasattr(self.os, 'is_storage_dirty') and self.os.is_storage_dirty() and self.os.is_running:
            self.log.info("Storage is dirty during cleanup, performing final save request.")
            self.send_message('os', OS_MSG_TYPE_OS_COMMAND, {'action': OS_CMD_SAVE_STORAGE})
            await asyncio.sleep_ms(300) # Give a bit of time for the OS to process if possible
        self.log.info("StorageSaverService cleanup complete.")
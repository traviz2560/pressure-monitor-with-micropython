import sys
import time
import asyncio
from core.constants import (
    OS_MSG_TYPE_OS_COMMAND, OS_CMD_STOP_SERVICE, OS_MSG_TYPE_SERVICE_COMMAND, 
    OS_MSG_TYPE_BROADCAST, OS_MSG_TYPE_STORAGE_UPDATE #! Added STORAGE_UPDATE
)

from core import Service, Message 
from lib.urtc import DateTimeTuple, datetime_tuple 

def _format_time_manual(fmt_str, time_tuple): #! Keep if time.strftime is problematic
    s = fmt_str
    s = s.replace("%H", "{:02d}".format(time_tuple[3]))
    s = s.replace("%M", "{:02d}".format(time_tuple[4]))
    s = s.replace("%S", "{:02d}".format(time_tuple[5]))
    s = s.replace("%d", "{:02d}".format(time_tuple[2]))
    s = s.replace("%m", "{:02d}".format(time_tuple[1]))
    s = s.replace("%y", "{:02d}".format(time_tuple[0] % 100))
    s = s.replace("%Y", "{}".format(time_tuple[0]))
    return s

class ClockService(Service):
    def __init__(self, name: str, os_instance, config: dict):
        super().__init__(name, os_instance, config)
        self.drift_check_interval_s = int(config.get('drift_check_interval_s', 3600)) #! Ensure int
        self.max_drift_s_before_resync = float(config.get('max_drift_s_before_resync', 60.0)) #! Ensure float
        self.rtc_device_key = config.get('device_key', 'rtc')
        # time_format and date_format are not used by ClockService itself for broadcasting time strings anymore
        
        self.time_synced_initial = False
        self.last_drift_check_ticks = 0
        self.last_drift_s = 0.0
        self._initial_sync_done_in_run = False 
        self.log.info(f"Initialized. RTC:'{self.rtc_device_key}',DriftChk:{self.drift_check_interval_s}s,MaxDrift:{self.max_drift_s_before_resync}s")

    async def _read_ds3231_datetime(self) -> DateTimeTuple | None:
        response = await self._request_hardware(device_name=self.rtc_device_key,method_name='datetime',timeout_s=2.5)
        if response and response.get('request_ok') and response.get('value') is not None:
            dt = response.get('value')
            return dt if isinstance(dt, tuple) else None # type: ignore
        self.log.error(f"Read DS3231 FAIL: {response.get('error','No/Bad Resp') if response else 'No HWM Resp'}")
        return None

    async def _write_ds3231_datetime(self, dt_to_write: DateTimeTuple) -> bool:
        self.log.info(f"Writing to DS3231: {dt_to_write}")
        args_tuple = tuple(dt_to_write) if isinstance(dt_to_write, DateTimeTuple) else dt_to_write
        response = await self._request_hardware(device_name=self.rtc_device_key,method_name='datetime',args=(args_tuple,),timeout_s=2.5)
        if response and response.get('request_ok'): return True
        self.log.error(f"Write DS3231 FAIL: {response.get('error','No/Bad Resp') if response else 'No HWM Resp'}")
        return False

    def _datetime_tuple_to_machine_rtc_format(self, dt: DateTimeTuple | tuple) -> tuple | None:
        try:
            return (getattr(dt,'year',dt[0]), getattr(dt,'month',dt[1]), getattr(dt,'day',dt[2]),
                    getattr(dt,'weekday',dt[3]), getattr(dt,'hour',dt[4]), getattr(dt,'minute',dt[5]),
                    getattr(dt,'second',dt[6]), 0)
        except (AttributeError, IndexError, TypeError) as e: self.log.error(f"Invalid DT for RTC conv: {dt}-{e}"); return None

    async def _set_machine_rtc_and_update_status(self, rtc_dt_tuple_for_machine: tuple, from_ds3231_read_success:bool = True) -> bool: #! Added flag
        if not rtc_dt_tuple_for_machine: self.log.error("Cannot set machine.RTC: None tuple."); return False
        try:
            self.os.system_rtc.datetime(rtc_dt_tuple_for_machine)
            self.log.info(f"System RTC (machine.RTC) set from tuple: {rtc_dt_tuple_for_machine[:7]}")
            # Now, update storage and broadcast status about the clock, not the time strings
            self.time_synced_initial = True # Mark as synced if we are setting it
            self._update_clock_status_storage(ds3231_read_success=from_ds3231_read_success)
            return True
        except Exception as e: 
            self.log.error(f"Set machine.RTC FAIL: {e}");sys.print_exception(e); return False

    async def _check_and_clear_osf(self):
        self.log.debug("Checking OSF...");
        resp = await self._request_hardware(self.rtc_device_key,'lost_power',timeout_s=1.5)
        if resp and resp.get('request_ok') and resp.get('value'):
            self.log.warn("OSF was set. Clearing...");
            curr_rtc_tuple=self.os.system_rtc.datetime()
            # Convert machine.RTC tuple to DateTimeTuple for writing to DS3231
            # machine.RTC weekday: 0-6 (Mon-Sun). urtc.datetime_tuple expects same or similar.
            dt_write=datetime_tuple(curr_rtc_tuple[0],curr_rtc_tuple[1],curr_rtc_tuple[2],curr_rtc_tuple[3],
                                    curr_rtc_tuple[4],curr_rtc_tuple[5],curr_rtc_tuple[6],0)
            if await self._write_ds3231_datetime(dt_write): self.log.info("OSF cleared by rewriting time to DS3231.")
            else: self.log.error("Failed to write time to DS3231 to clear OSF.")
        elif resp and resp.get('request_ok'): self.log.info("OSF is clear.")
        else: self.log.warn(f"Could not read OSF: {resp.get('error','No/Bad Resp') if resp else 'No HWM Resp'}")

    async def setup(self):
        await super().setup() 
        self.log.info(f"ClockSvc setup: RTC dev '{self.rtc_device_key}'. Config OK.")
        if self.os.hardware_manager and self.rtc_device_key not in self.os.hardware_manager.drivers: # type: ignore
            raise RuntimeError(f"RTC dev '{self.rtc_device_key}' not in HWM for ClockSvc.")
        self.log.info("ClockSvc config validated. Initial sync in run().")

    async def _perform_initial_sync(self) -> bool:
        self.log.info(f"Performing initial clock sync from '{self.rtc_device_key}'...")
        ds3231_dt = await self._read_ds3231_datetime()
        if ds3231_dt:
            rtc_tuple = self._datetime_tuple_to_machine_rtc_format(ds3231_dt)
            if await self._set_machine_rtc_and_update_status(rtc_tuple, from_ds3231_read_success=True): # type: ignore
                self.os.storage['system_status'] = "CLOCK_OK" 
                self.os.mark_storage_dirty(['system_status']); self.log.info("Initial System RTC sync SUCCESS.")
                await self._check_and_clear_osf(); return True # OSF check after successful sync
            else: self.os.storage['system_status'] = "CLK_ERR_SET"
        else: self.os.storage['system_status'] = "CLK_ERR_READ"
        self.os.mark_storage_dirty(['system_status']); return False

    def _update_clock_status_storage(self, ds3231_read_success: bool, new_drift: float = None):
        """Updates os.storage with clock status information and broadcasts it."""
        if new_drift is not None:
            self.last_drift_s = new_drift
        
        clock_status_data = {
            'timestamp_epoch': time.time(), # When this status was generated
            'drift_s': round(self.last_drift_s, 3),
            'synced_initial': self.time_synced_initial,
            'last_ds3231_read_success': ds3231_read_success,
            'next_drift_check_s': self.drift_check_interval_s # Informational
        }
        
        self.os.storage['clock_drift_seconds'] = clock_status_data['drift_s'] # For direct access if needed
        if 'clock_info' not in self.os.storage: self.os.storage['clock_info'] = {}
        self.os.storage['clock_info'].update(clock_status_data)
        self.os.mark_storage_dirty(['clock_drift_seconds', 'clock_info'])
        
        # Broadcast clock *status* update, not the time itself frequently
        self.send_message(OS_MSG_TYPE_BROADCAST, 'clock_status_update', clock_status_data)
        self.log.debug(f"Clock STATUS Update Broadcast: Drift={clock_status_data['drift_s']:.1f}s, DS3231OK={ds3231_read_success}")


    async def run(self):
        if not self._initial_sync_done_in_run:
            sync_ok = await self._perform_initial_sync(); self._initial_sync_done_in_run=True
            if not sync_ok:
                if self.is_critical:
                    self.log.critical("CRIT ClockSvc FAILED initial sync. Requesting stop.");
                    self.send_message('os',OS_MSG_TYPE_OS_COMMAND,{'action':OS_CMD_STOP_SERVICE,'name':self.name,'params':{'reason':'crit_sync_fail'}})
                    return
                else: self.log.warn("Initial clock sync FAILED. Time unreliable.")
            # else: _perform_initial_sync calls _set_machine_rtc_and_update_status which broadcasts status
        
        if not self.time_synced_initial and self.is_running: self.log.warn("ClockSvc running, but initial sync unsuccessful.")
        self.last_drift_check_ticks = time.ticks_ms()

        while self.is_running: 
            await self._paused_event.wait() 
            now_ticks=time.ticks_ms()
            if time.ticks_diff(now_ticks, self.last_drift_check_ticks) >= (self.drift_check_interval_s * 1000):
                self.last_drift_check_ticks=now_ticks
                self.log.info("Performing periodic Clock Drift check...")
                
                internal_rtc_epoch_before_read = time.time()
                ds3231_dt = await self._read_ds3231_datetime()
                ds3231_epoch_s = None
                read_ok = False

                if ds3231_dt:
                    read_ok = True
                    try: ds3231_epoch_s = self.os.urtc_lib.tuple2seconds(ds3231_dt)
                    except Exception as e: self.log.warn(f"DS3231 time to epoch FAIL: {e}"); read_ok = False # Count as read failure if conversion fails
                
                if read_ok and ds3231_epoch_s is not None:
                    current_drift = internal_rtc_epoch_before_read - ds3231_epoch_s
                    self.log.info(f"Drift Check: InternalEpoch={internal_rtc_epoch_before_read}, DS3231Epoch={ds3231_epoch_s}, Drift={current_drift:.3f}s")
                    
                    if abs(current_drift) > self.max_drift_s_before_resync:
                        self.log.warn(f"Drift ({current_drift:.2f}s) > max ({self.max_drift_s_before_resync}s). Resyncing machine.RTC from DS3231.")
                        # Re-read DS3231 to get the most current time for resync, as some time passed.
                        fresh_ds3231_dt = await self._read_ds3231_datetime()
                        if fresh_ds3231_dt:
                            rtc_m_tuple = self._datetime_tuple_to_machine_rtc_format(fresh_ds3231_dt)
                            if await self._set_machine_rtc_and_update_status(rtc_m_tuple, from_ds3231_read_success=True): # type: ignore
                                self.last_drift_s = 0.0 # Drift is now 0
                                # _update_clock_status_storage is called by _set_machine_rtc_and_update_status
                            else: 
                                self.log.error("Resync machine.RTC FAILED.")
                                self._update_clock_status_storage(ds3231_read_success=False, new_drift=current_drift) # Report old drift
                        else:
                            self.log.error("Read DS3231 for resync FAILED.")
                            self._update_clock_status_storage(ds3231_read_success=False, new_drift=current_drift) # Report old drift
                    else: # Drift is acceptable
                        self._update_clock_status_storage(ds3231_read_success=True, new_drift=current_drift)
                else: # DS3231 read failed or conversion error
                    self.log.warn("Drift check: DS3231 read/conversion failed.")
                    self._update_clock_status_storage(ds3231_read_success=False) # Don't update drift if read failed
            
            # Sleep logic (wait_for_ms or simple sleep)
            try:
                await asyncio.wait_for_ms(self._stop_requested_event.wait(), min(self.drift_check_interval_s, 300) * 1000 // 4) # Check stop event periodically
                if self._stop_requested_event.is_set(): break
            except asyncio.TimeoutError: pass # Normal timeout
            except asyncio.CancelledError: raise

        self.log.info("ClockService run loop finished.")

    async def on_message(self, msg: Message): 
        await super().on_message(msg) 
        if msg.type == OS_MSG_TYPE_OS_COMMAND or msg.type == OS_MSG_TYPE_SERVICE_COMMAND: 
            payload=msg.payload; action=payload.get('action')
            if msg.payload.get('target_service') and msg.payload.get('target_service') != self.name: return 

            if action == 'set_system_time': 
                dt_data=payload.get('datetime_data')
                if dt_data:
                    try:
                        curr_wd=time.localtime(time.time())[6]
                        dt_set=datetime_tuple(dt_data['year'],dt_data['month'],dt_data['day'],
                            dt_data.get('weekday',curr_wd),dt_data['hour'],dt_data['minute'],dt_data['second'],0)
                        self.log.info(f"Cmd: set system time to: {dt_set}")
                        if await self._write_ds3231_datetime(dt_set):
                            new_dt=await self._read_ds3231_datetime()
                            if new_dt:
                                rtc_m=self._datetime_tuple_to_machine_rtc_format(new_dt)
                                if await self._set_machine_rtc_and_update_status(rtc_m, from_ds3231_read_success=True): # type: ignore
                                    self.last_drift_s=0.0; # Resets drift, status updated by call above
                            else: self.log.error("Re-read DS3231 after set_system_time FAIL.")
                        else: self.log.error("Write new time to DS3231 via cmd FAIL.")
                    except (KeyError,TypeError,ValueError)as e: self.log.error(f"Invalid payload for 'set_system_time': {e}")
                else: self.log.warn("'set_system_time' missing 'datetime_data'.")
            elif action == 'force_drift_check': 
                self.log.info("Forced drift check requested."); self.last_drift_check_ticks=0 
            else: # Pass to base if not handled here and it's a service command
                if msg.type == OS_MSG_TYPE_SERVICE_COMMAND:
                    await super().handle_service_command(payload)
    
    async def cleanup(self): await super().cleanup(); self.log.info("Clock service cleanup.")
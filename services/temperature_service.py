import sys
import asyncio
from core import Service, Message 
from core.constants import OS_MSG_TYPE_OS_COMMAND, OS_CMD_STOP_SERVICE, OS_MSG_TYPE_BROADCAST, OS_MSG_TYPE_SERVICE_COMMAND #! Added BROADCAST

class TemperatureService(Service):
    def __init__(self, name: str, os_instance, config: dict):
        super().__init__(name, os_instance, config)
        self.read_interval_s = int(config.get('read_interval_s', 10)) #! Ensure int
        self.sensor_device_key = config.get('device_key', 'rtc') 
        self.last_temp_c = None
        self._initial_sensor_check_done_in_run = False
        self.log.info(f"Initialized. Sensor: '{self.sensor_device_key}', Interval: {self.read_interval_s}s")
    
    async def setup(self):
        await super().setup()
        self.log.info(f"TempSvc setup: sensor '{self.sensor_device_key}'. Config OK.")
        if self.os.hardware_manager and self.sensor_device_key not in self.os.hardware_manager.drivers: # type: ignore
            raise RuntimeError(f"Sensor dev '{self.sensor_device_key}' not in HWM for TempSvc.")
        self.log.info("TempSvc config validated. Initial sensor check in run().")

    async def _read_temp_from_sensor(self) -> float | None:
        response = await self._request_hardware(device_name=self.sensor_device_key,method_name='get_temperature',timeout_s=2.0)
        if response and response.get('request_ok'):
            temp_c = response.get('value')
            if temp_c is not None and isinstance(temp_c, (float, int)): return round(temp_c, 2)
            self.log.warn(f"HW OK for '{self.sensor_device_key}.get_temp' but val invalid. Val: {temp_c}")
        else:
            err = response.get('error','No/Bad Resp') if response else "No HWM Resp"
            self.log.error(f"Failed HW req for temp from '{self.sensor_device_key}': {err}")
        return None

    async def _perform_initial_sensor_check(self) -> bool:
        self.log.info(f"Performing initial sensor check for '{self.sensor_device_key}'...")
        initial_temp = await self._read_temp_from_sensor()
        if initial_temp is not None:
            self.log.info(f"Sensor '{self.sensor_device_key}' verified. Initial temp: {initial_temp:.2f}C.")
            self.last_temp_c = initial_temp 
            self.os.storage['current_temperature'] = self.last_temp_c
            self.os.mark_storage_dirty(['current_temperature']) #! Specify changed key
            temp_data = {'value': self.last_temp_c, 'unit': 'C', 'source_device': self.sensor_device_key}
            self.send_message(OS_MSG_TYPE_BROADCAST, 'temperature_update', temp_data) #! Use constant
            return True
        self.log.error(f"Initial sensor check FAILED for '{self.sensor_device_key}'."); return False

    async def process_temperature_reading(self):
        temp_c = await self._read_temp_from_sensor()
        if temp_c is not None:
            if self.last_temp_c is None or abs(self.last_temp_c - temp_c) > 0.05: 
                self.log.info(f"Read Temp: {temp_c:.2f}C (Prev: {self.last_temp_c})")
                self.last_temp_c = temp_c
                self.os.storage['current_temperature'] = self.last_temp_c
                self.os.mark_storage_dirty(['current_temperature']) #! Specify changed key
                temp_data = {'value': self.last_temp_c, 'unit': 'C', 'source_device': self.sensor_device_key}
                self.send_message(OS_MSG_TYPE_BROADCAST, 'temperature_update', temp_data) #! Use constant
        else: self.log.warn(f"Failed to read temp. Last known: {self.last_temp_c}")

    async def run(self):
        if not self._initial_sensor_check_done_in_run:
            check_ok = await self._perform_initial_sensor_check(); self._initial_sensor_check_done_in_run = True
            if not check_ok:
                if self.is_critical:
                    self.log.critical(f"CRIT TempSvc FAILED initial sensor check for '{self.sensor_device_key}'. Requesting stop.")
                    self.send_message('os',OS_MSG_TYPE_OS_COMMAND,{'action':OS_CMD_STOP_SERVICE,'name':self.name,'params':{'reason':'crit_sensor_fail'}})
                    return 
                else: self.log.warn(f"Initial sensor check FAILED for '{self.sensor_device_key}'. May not provide valid data.")
        
        if self.last_temp_c is None and self.is_running: 
             self.log.warn(f"TempSvc running, but initial sensor value unknown for '{self.sensor_device_key}'.")

        try:
            while self.is_running: 
                await self._paused_event.wait()
                if not self.is_running: break 
                await self.process_temperature_reading()
                
                # ! Use asyncio.sleep directly for the interval
                # ! This is less reactive to immediate stop requests during the sleep,
                # ! but Service.stop() will cancel this task anyway.
                await asyncio.sleep(self.read_interval_s)

        except asyncio.CancelledError: self.log.info("TemperatureService run loop cancelled.")
        except Exception as e: self.log.error(f"Unhandled exception in TempSvc run loop: {e}"); sys.print_exception(e)
        self.log.info("TemperatureService run loop finished.")

    async def on_message(self, msg: Message):
        await super().on_message(msg) 
        if msg.type == OS_MSG_TYPE_OS_COMMAND and msg.payload.get('action') == 'force_read_temp':
            self.log.info("Forced temperature read requested via command.")
            await self.process_temperature_reading()
        elif msg.type == OS_MSG_TYPE_SERVICE_COMMAND: #! Handle generic service commands
            await super().handle_service_command(msg.payload)

    async def cleanup(self):
        await super().cleanup(); self.log.info("Temperature service cleanup complete."); self.last_temp_c = None
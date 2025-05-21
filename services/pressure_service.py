import asyncio
import sys
import time 
from core import Service, Message
from core.constants import OS_MSG_TYPE_BROADCAST, OS_CMD_STOP_SERVICE 

class PressureService(Service):
    def __init__(self, name: str, os_instance, config: dict):
        super().__init__(name, os_instance, config)
        self.voltage_storage_key = config.get("voltage_storage_key") 
        if not self.voltage_storage_key:
            self.log.error("Missing 'voltage_storage_key' in config. PressureService cannot operate.")
        
        self.read_interval_s = int(config.get("read_interval_s", 10)) 
        self.v_to_mpa_slope = float(config.get("V_TO_MPA_SLOPE", 12.5)) #! Default al valor anterior
        self.v_to_mpa_intercept = float(config.get("V_TO_MPA_INTERCEPT", -1.25)) #! Default al valor anterior
        self.psi_per_mpa = float(config.get("PSI_PER_MPA", 145.038))
        self.broadcast_as = config.get("broadcast_as", "pressure_update")
        
        self.current_pressure_psi = None
        self.last_processed_voltage = None 
        self.log.info(f"Initialized. Reading V from storage '{self.voltage_storage_key}', update_interval:{self.read_interval_s}s. Slope:{self.v_to_mpa_slope}, Intercept:{self.v_to_mpa_intercept}")

    async def setup(self):
        await super().setup()
        if not self.voltage_storage_key:
            raise RuntimeError("PressureService 'voltage_storage_key' not configured.")
        self.log.info("PressureService setup complete. Will periodically check voltage from storage.")

    async def _calculate_and_broadcast_pressure(self):
        linearized_voltage = self.os.storage.get(self.voltage_storage_key) # type: ignore
        
        if linearized_voltage is None: return 

        if self.last_processed_voltage is not None and \
           abs(self.last_processed_voltage - linearized_voltage) < 1e-4: #! Evitar recálculos si V no cambió mucho
            return
        
        self.last_processed_voltage = linearized_voltage

        try:
            mpa = self.v_to_mpa_slope * float(linearized_voltage) + self.v_to_mpa_intercept
            psi = round(mpa * self.psi_per_mpa) 

            if self.current_pressure_psi != psi: 
                self.log.info(f"Pressure updated: {psi} PSI (from V: {linearized_voltage:.4f}, MPA: {mpa:.3f})")
                self.current_pressure_psi = psi
                self.os.storage['current_pressure_psi'] = self.current_pressure_psi
                self.os.mark_storage_dirty(['current_pressure_psi']) 
                
                payload_out = {
                    'psi': self.current_pressure_psi, 'mpa': round(mpa, 3),
                    'source_voltage_key': self.voltage_storage_key, 'voltage_value': linearized_voltage
                }
                self.send_message(OS_MSG_TYPE_BROADCAST, self.broadcast_as, payload_out)
        except Exception as e:
            self.log.error(f"Error converting voltage to pressure: {e}. Voltage: {linearized_voltage}")
            sys.print_exception(e)

    async def run(self):
        if not self.voltage_storage_key:
            self.log.error("PressureService cannot run without 'voltage_storage_key'."); await super().run(); return

        self.log.info("PressureService run loop started.")
        try:
            while self.is_running:
                await self._paused_event.wait()
                if not self.is_running: break
                await self._calculate_and_broadcast_pressure()
                await asyncio.sleep(self.read_interval_s)
        except asyncio.CancelledError: self.log.info("PressureService run loop cancelled.")
        except Exception as e: self.log.error(f"Error in PressureService run: {e}"); sys.print_exception(e)
        finally: self.log.info("PressureService run loop finished.")

    async def on_message(self, msg: Message): 
        await super().on_message(msg)
        if msg.type == "service_command": # OS_MSG_TYPE_SERVICE_COMMAND
            await super().handle_service_command(msg.payload)

    async def cleanup(self):
        await super().cleanup(); self.log.info("PressureService cleanup."); self.current_pressure_psi = None
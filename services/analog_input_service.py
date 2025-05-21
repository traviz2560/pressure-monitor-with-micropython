import asyncio
import sys
import time 
from core import Service, Message
from core.constants import OS_MSG_TYPE_BROADCAST, OS_CMD_STOP_SERVICE
from utils import RunningMedianFilter, LINEARIZATION_FUNCTIONS

class AnalogInputService(Service):
    def __init__(self, name: str, os_instance, config: dict):
        super().__init__(name, os_instance, config)
        self.inputs_config = config.get("inputs", {})
        self.adc_readers = {} 
        self.log.info(f"Initialized for ADC inputs: {list(self.inputs_config.keys())}")

    async def setup(self):
        await super().setup()
        self.log.info("Setting up ADC inputs...")
        valid_inputs_found = False
        for logical_name, input_conf in self.inputs_config.items():
            pin_config_key = input_conf.get("pin_config_key")
            if not pin_config_key:
                self.log.error(f"ADC input '{logical_name}' missing 'pin_config_key'. Skipping.")
                continue

            if not self.os.hardware_manager or \
               pin_config_key not in self.os.hardware_manager.drivers or \
               self.os.hardware_manager.drivers[pin_config_key]['state'] != 'READY': # type: ignore
                self.log.error(f"ADC device '{pin_config_key}' for '{logical_name}' not configured or not READY. Skipping.")
                continue

            filter_size = int(input_conf.get("median_filter_size", 1))
            lin_func_name = input_conf.get("linearization_func_name")
            lin_func = LINEARIZATION_FUNCTIONS.get(lin_func_name, LINEARIZATION_FUNCTIONS.get("passthrough"))

            if lin_func_name and not LINEARIZATION_FUNCTIONS.get(lin_func_name):
                self.log.warn(f"LinFunc '{lin_func_name}' not found for ADC '{logical_name}'. Using passthrough.")
            
            adc_method = input_conf.get("adc_method", "read_u16") #! Default to read_u16
            adc_max_val = float(input_conf.get("adc_max_value", 65535.0)) #! Default for read_u16
            if adc_method == "read" and input_conf.get("adc_max_value") is None:
                adc_max_val = 4095.0 #! Override if 'read' and no specific max_value

            self.adc_readers[logical_name] = {
                "pin_config_key": pin_config_key,
                "interval_s": float(input_conf.get("read_interval_s", 1.0)), 
                "filter": RunningMedianFilter(filter_size) if filter_size > 1 else None,
                "lin_func": lin_func,
                "broadcast_as": input_conf.get("broadcast_as", f"{logical_name}_value"),
                "update_storage_key": input_conf.get("update_storage_key"), 
                "adc_method": adc_method,         #! Guardar método ADC
                "adc_max_value": adc_max_val,     #! Guardar valor máximo para normalización
                "last_read_ticks": time.ticks_ms(), 
                "last_processed_value": None, 
                "value_change_threshold": float(input_conf.get("value_change_threshold", 0.005)) 
            }
            self.log.info(f"ADC input '{logical_name}' on HW '{pin_config_key}' ready. Method: {adc_method}, MaxVal: {adc_max_val}, Interval: {self.adc_readers[logical_name]['interval_s']}s, Filter: {filter_size}, LinFunc: {lin_func_name or 'passthrough'}.")
            valid_inputs_found = True
        
        if not valid_inputs_found:
            self.log.warn("No valid ADC inputs configured.")

    async def _read_and_process_adc(self, logical_name: str):
        reader_conf = self.adc_readers[logical_name]
        pin_key = reader_conf["pin_config_key"]
        adc_method_name = reader_conf["adc_method"]
        adc_divisor = reader_conf["adc_max_value"]

        response = await self._request_hardware(
            device_name=pin_key,
            method_name=adc_method_name, #! Usar método configurado
            timeout_s=0.5 
        )

        if response and response.get('request_ok') and response.get('value') is not None:
            raw_adc_val = response.get('value')
            normalized_value = raw_adc_val / adc_divisor #! Usar divisor configurado

            if reader_conf["filter"]:
                reader_conf["filter"].add(normalized_value)
                processed_value_norm = reader_conf["filter"].get_median()
            else:
                processed_value_norm = normalized_value
            
            if processed_value_norm is None: return

            final_value = reader_conf["lin_func"](processed_value_norm) # type: ignore
            
            storage_key = reader_conf.get("update_storage_key")
            if storage_key:
                current_storage_val = self.os.storage.get(storage_key)
                # Actualizar solo si el valor es diferente para evitar dirty flag innecesario
                if current_storage_val is None or abs(current_storage_val - final_value) > 1e-5: # Pequeña tolerancia para floats
                    self.os.storage[storage_key] = final_value
                    self.os.mark_storage_dirty([storage_key]) 
                    # self.log.debug(f"ADC '{logical_name}' updated storage '{storage_key}' to {final_value:.4f}")

            last_val = reader_conf["last_processed_value"]
            threshold = reader_conf["value_change_threshold"]
            if last_val is None or abs(final_value - last_val) > threshold:
                self.log.info(f"ADC '{logical_name}' ({pin_key}): RawADC={raw_adc_val}, Norm={normalized_value:.3f}, FiltNorm={processed_value_norm:.3f}, FinalV={final_value:.4f}")
                payload = {'logical_name': logical_name, 'value': final_value, 
                           'raw_adc': raw_adc_val, 'normalized': normalized_value, 
                           'filtered_normalized': processed_value_norm}
                self.send_message(OS_MSG_TYPE_BROADCAST, reader_conf["broadcast_as"], payload)
                reader_conf["last_processed_value"] = final_value
        else:
            self.log.warn(f"Failed to read ADC for '{logical_name}' ({pin_key}): {response.get('error') if response else 'No HWM response'}")

    async def run(self):
        # ... (igual que la versión anterior, sin cambios aquí) ...
        if not self.adc_readers:
            self.log.warn("AnalogInputSvc: No ADC inputs. Sleeping."); await super().run(); return
        try:
            while self.is_running:
                await self._paused_event.wait()
                if not self.is_running: break
                now = time.ticks_ms()
                all_reads_done_this_cycle = True
                for logical_name, reader_conf in self.adc_readers.items():
                    interval_ms = int(reader_conf["interval_s"] * 1000)
                    if time.ticks_diff(now, reader_conf["last_read_ticks"]) >= interval_ms:
                        await self._read_and_process_adc(logical_name)
                        reader_conf["last_read_ticks"] = now
                    else: all_reads_done_this_cycle = False 
                min_next_read_delay_ms = float('inf')
                if not all_reads_done_this_cycle:
                    for reader_conf in self.adc_readers.values():
                        interval_ms = int(reader_conf["interval_s"] * 1000)
                        elapsed_ms = time.ticks_diff(now, reader_conf["last_read_ticks"])
                        remaining_ms = max(0, interval_ms - elapsed_ms)
                        if remaining_ms < min_next_read_delay_ms: min_next_read_delay_ms = remaining_ms
                else: 
                    min_next_read_delay_ms = min(rc["interval_s"] for rc in self.adc_readers.values()) * 1000
                sleep_ms = max(10, int(min_next_read_delay_ms)) 
                await asyncio.sleep_ms(sleep_ms)
        except asyncio.CancelledError: self.log.info("AnalogInputSvc run loop cancelled.")
        except Exception as e: self.log.error(f"Error in AnalogInputSvc run: {e}"); sys.print_exception(e)
        finally: self.log.info("AnalogInputSvc run loop finished.")

    async def cleanup(self): await super().cleanup(); self.log.info("AnalogInputSvc cleanup.")
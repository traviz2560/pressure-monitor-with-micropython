import asyncio
import sys
import time
from core import Service
from core.constants import OS_CMD_STOP_SERVICE, OS_MSG_TYPE_OS_COMMAND 

from lib.lora_e220 import LoRaE220 
from lib.lora_e220_operation_constant import ResponseStatusCode, ModeType # ModeType podría no ser necesario

class LoraTxService(Service):
    def __init__(self, name: str, os_instance, config: dict):
        super().__init__(name, os_instance, config)
        
        self.uart_bus_id_str = str(config.get("uart_bus_id_str", "1"))
        self.model_string = config.get("model_string")
        if not self.model_string or self.model_string == "YOUR_LORA_MODEL": 
            self.log.error("LoRa module 'model_string' not correctly specified in config! Example: '900T30D'")
            # Esto causará un error en setup si es crítico.
        
        self.pin_m0_key = config.get("pin_m0_config_key") 
        self.pin_m1_key = config.get("pin_m1_config_key") 
        self.pin_aux_key = config.get("pin_aux_config_key")

        self.transmit_interval_s = int(config.get("transmit_interval_s", 30))
        self.data_format_string = config.get("data_format_string", "T:{tempC},P:{psi}psi,D:{date_str},TS:{time_str}") #! Default con fecha/hora
        
        self.uart_primitive = None 
        self.lora_module: LoRaE220 | None = None 

        self.uart_key = f"uart_{self.uart_bus_id_str}"
        self.log.info(f"Initialized. UART Bus: '{self.uart_key}', Model: {self.model_string}, Interval: {self.transmit_interval_s}s, Format: '{self.data_format_string}'")

    async def _get_pin_instance(self, pin_key: str | None):
        if not pin_key: return None
        if self.os.hardware_manager and \
           pin_key in self.os.hardware_manager.drivers and \
           self.os.hardware_manager.drivers[pin_key]['state'] == 'READY': # type: ignore
            return self.os.hardware_manager.drivers[pin_key]['instance'] # type: ignore
        self.log.warn(f"Pin '{pin_key}' not found or not ready in HWM. Will proceed without it if optional for LoRa lib.")
        return None

    async def setup(self):
        await super().setup()
        self.log.info("Setting up LoRa E220 Transmitter...")

        if not self.model_string or self.model_string == "YOUR_LORA_MODEL":
            raise RuntimeError("LoRa E220: model_string is required and must be valid in configuration.")

        if self.os.hardware_primitives and self.uart_key in self.os.hardware_primitives:
            self.uart_primitive = self.os.hardware_primitives[self.uart_key]
            self.log.info(f"UART primitive '{self.uart_key}' obtained.")
        else:
            raise RuntimeError(f"UART primitive '{self.uart_key}' not found for LoraTxService.")

        m0_pin_obj = await self._get_pin_instance(self.pin_m0_key) if self.pin_m0_key else None
        m1_pin_obj = await self._get_pin_instance(self.pin_m1_key) if self.pin_m1_key else None
        aux_pin_obj = await self._get_pin_instance(self.pin_aux_key) if self.pin_aux_key else None
        
        # El baudrate del UART entre el MCU y el módulo LoRa.
        # Este baudrate debe coincidir con cómo está configurado el módulo LoRa.
        uart_baudrate_mcu_to_lora = self.os.hw_config_full.get("uart",{}).get(self.uart_bus_id_str,{}).get("baudrate", 9600)
        
        try:
            self.lora_module = LoRaE220(
                model=self.model_string, 
                uart=self.uart_primitive, 
                m0_pin=m0_pin_obj, 
                m1_pin=m1_pin_obj, 
                aux_pin=aux_pin_obj, 
                uart_baudrate=uart_baudrate_mcu_to_lora # La librería LoRaE220 llamará a uart.init() con este baudrate
            )
        except ValueError as e: 
            self.log.error(f"Failed to instantiate LoRaE220 (check model_string '{self.model_string}'): {e}")
            raise RuntimeError(f"LoRaE220 instantiation error: {e}") 

        self.log.info(f"LoRaE220 instance created. MCU-LoRa UART Baudrate: {uart_baudrate_mcu_to_lora}.")
        
        # begin() inicializa el UART del MCU a través de la librería y
        # intenta poner el módulo en modo normal (si los pines M0/M1 se proporcionan).
        # Si M0/M1 no se proporcionan, asume que el módulo ya está en el modo correcto.
        code = self.lora_module.begin() 
        if code != ResponseStatusCode.SUCCESS:
            # Si los pines M0/M1/AUX no se usan, un error aquí puede ser normal si el módulo ya está en modo normal.
            self.log.warn(f"LoRa module begin() call returned: {ResponseStatusCode.get_description(code)}. This might be OK if M0/M1/AUX pins are not managed by this service and module is already in the correct mode.")
        else:
            self.log.info(f"LoRa module begin() call successful.")

        # NO LEER NI SETEAR CONFIGURACIÓN DEL LORA AQUÍ si no se gestionan los pines M0/M1.
        # Se asume que el módulo LoRa está pre-configurado externamente con la herramienta de EBYTE
        # para que coincida con los parámetros de comunicación deseados (canal, dirección, potencia, etc.)
        # y, crucialmente, el UART baudrate usado entre el MCU y el módulo.
        self.log.info("LoRa Transmitter setup assumes LoRa module is pre-configured externally for transparent transmission and desired radio parameters.")


    async def run(self):
        if not self.lora_module:
            self.log.error("LoRa module not initialized. LoraTxService cannot run."); return

        self.log.info("LoraTxService run loop started.")
        try:
            while self.is_running:
                await self._paused_event.wait()
                if not self.is_running: break

                # Obtener datos del almacenamiento del OS
                temp_c = self.os.storage.get('current_temperature')
                pressure_psi = self.os.storage.get('current_pressure_psi')
                
                # Obtener fecha y hora actual
                try:
                    current_time_tuple = time.localtime(time.time()) # time.time() da segundos desde la época
                    time_str = "{:02d}:{:02d}:{:02d}".format(current_time_tuple[3], current_time_tuple[4], current_time_tuple[5])
                    date_str = "{:02d}/{:02d}/{:02d}".format(current_time_tuple[2], current_time_tuple[1], current_time_tuple[0] % 100)
                except Exception as e:
                    self.log.warn(f"Could not get or format current time: {e}")
                    time_str = "HH:MM:SS"
                    date_str = "DD/MM/YY"

                # Formatear los valores para el string
                temp_str = f"{temp_c:.1f}" if temp_c is not None else "N/A"
                pressure_str = f"{int(pressure_psi)}" if pressure_psi is not None else "N/A"
                
                message_to_send = ""
                try:
                    message_to_send = self.data_format_string.format(
                        tempC=temp_str, 
                        psi=pressure_str,
                        date_str=date_str,
                        time_str=time_str
                    )
                except Exception as e:
                    self.log.error(f"Data formatting error with '{self.data_format_string}': {e}. Using raw values.")
                    message_to_send = f"T={temp_str},P={pressure_str},D={date_str},TS={time_str}" 
                
                # Añadir terminador de línea si es necesario para el receptor
                full_lora_message = message_to_send + "\r\n" 
                
                self.log.info(f"LoRa TX: Preparing to send: '{message_to_send}'") # Log sin \r\n
                
                # Enviar el mensaje usando transmisión transparente
                # Esto asume que el módulo está en Modo 0 (Normal/Transmisión Transparente)
                code = self.lora_module.send_transparent_message(full_lora_message)
                
                if code == ResponseStatusCode.SUCCESS:
                    self.log.info(f"LoRa TX: Message sent successfully.")
                else:
                    self.log.error(f"LoRa TX: Failed to send. Code: {code} ({ResponseStatusCode.get_description(code)})")
                    # Pequeña pausa si falla el envío para no inundar de errores
                    await asyncio.sleep_ms(1000) 
                
                await asyncio.sleep(self.transmit_interval_s)

        except asyncio.CancelledError: self.log.info("LoraTxService run loop cancelled.")
        except Exception as e: self.log.error(f"Unhandled error in LoraTxService run: {e}"); sys.print_exception(e)
        finally: self.log.info("LoraTxService run loop finished.")

    async def cleanup(self):
        await super().cleanup()
        # La librería LoRaE220 podría tener un método `end()` o `close()` que podría ser llamado aquí.
        # self.lora_module.end() # Si existe y es apropiado.
        # Por ahora, la librería no parece requerir una limpieza explícita más allá de la deinit del UART que
        # el OS podría manejar o la propia librería al ser eliminada.
        # Si M0/M1 no son gestionados, no hay modo que restaurar.
        self.log.info("LoraTxService cleanup complete.")
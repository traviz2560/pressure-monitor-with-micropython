from services import (ClockService, TemperatureService, StorageSaverService, 
                      StatusDisplayService, AnalogInputService, PressureService,
                      LoraTxService)

from lib.lora_e220_constants import UARTBaudRate, UARTParity, AirDataRate, TransmissionPower22, FixedTransmission, WorPeriod, RssiEnableByte,LbtEnableByte

SERVICE_REGISTRY = {
    "clock": {
        "class": ClockService, "start_order": 10, "autostart": True,
        "config":{ "device_key": "rtc", "drift_check_interval_s": 60, 
                   "max_drift_s_before_resync": 10, "time_format": "%H:%M", 
                   "date_format": "%d/%m/%y", "is_critical": True, "log_level": "INFO" }
    },
    "temperature_monitor": { # Mantiene su propia lógica de lectura
        "class": TemperatureService, "start_order": 30, "autostart": True,
        "config":{ "device_key": "rtc", "read_interval_s": 5, # Lee temp del RTC cada 5s
                   "is_critical": False, "log_level": "INFO" }
    },
    "storage_saver":{ 
        "class": StorageSaverService, "start_order": 50, "autostart": True,
        "config":{ "save_interval_s": 600, "is_critical": False, "log_level": "INFO" }
    },
    "display": {
        "class": StatusDisplayService, "start_order": 20, "autostart": True,
        "config": {
            "device_key": "lcd_main", "refresh_interval_s": 1, 
            "default_layout": "main_status",
            "boot_status_layout": "boot_status_overview", "boot_status_duration_s": 7,
            "rows": 2, "cols": 16,
            "display_time_format": "%H:%M", "display_date_format": "%d/%m/%y",
            "alternate_interval_s": 5, 
            "log_level": "DEBUG"
        }
    },
    "analog_reader": { 
        "class": AnalogInputService, "start_order": 35, "autostart": True,
        "config": {
            "log_level": "INFO",
            "inputs": {
                "pressure_sensor_adc": { 
                    "pin_config_key": "pressure_adc", 
                    "read_interval_s": 0.2, 
                    "median_filter_size": 11,
                    "linearization_func_name": "custom_adc_to_voltage", 
                    "broadcast_as": "pressure_adc_voltage", 
                    "update_storage_key": "latest_pressure_adc_voltage",
                    "adc_method": "read", 
                    "adc_max_value": 4095.0 
                }
            }
        }
    },
    "pressure_monitor": { 
        "class": PressureService, "start_order": 40, "autostart": True,
        "config": {
            "log_level": "INFO",
            "voltage_storage_key": "latest_pressure_adc_voltage", 
            "read_interval_s": 10, 
            "V_TO_MPA_SLOPE": 12.5,  
            "V_TO_MPA_INTERCEPT": -1.25, 
            "PSI_PER_MPA": 145.038,
            "broadcast_as": "pressure_update"
        }
    },
    "lora_transmitter": { 
        "class": LoraTxService,
        "start_order": 60, 
        "autostart": True,
        "config": {
            "log_level": "DEBUG", 
            "uart_bus_id_str": "1", # Corresponde a UART(1) en HARDWARE_CONFIGURATION
            "model_string": "900T30D", #! IMPORTANTE: Cambiar al modelo exacto de tu módulo E220. Ej: "433T22D", "868T20S", "915T30D"
            
            # Pines M0, M1, AUX son opcionales. 
            # Si los pines M0/M1 están cableados externamente para el modo normal (M0=GND, M1=GND),
            # entonces no necesitas definirlos aquí (déjalos como None o no los incluyas).
            "pin_m0_config_key": None, # Opcional: "lora_m0" si quieres que el OS lo gestione y está en HARDWARE_CONFIGURATION/devices
            "pin_m1_config_key": None, # Opcional: "lora_m1" 
            "pin_aux_config_key": None, # Opcional: "lora_aux" (AUX también es opcional para la librería)

            "transmit_interval_s": 10, # Intervalo de envío en segundos
            # Formato del string de datos. Asegúrate de que los placeholders coincidan con los usados en LoraTxService.run()
            "data_format_string": "T:{tempC}C, P:{psi}psi, D:{date_str}, TS:{time_str}",

            # La configuración de radio del módulo LoRa (AirRate, Channel, Address, NetID, Power, etc.)
            # DEBE hacerse externamente con la herramienta de configuración de EBYTE.
            # Este servicio ASUME que el módulo ya está configurado para transmisión transparente
            # y con los parámetros de radio correctos.
            # El baudrate del UART entre el MCU (ESP32) y el módulo LoRa se define en
            # HARDWARE_CONFIGURATION.uart.X.baudrate y debe coincidir con la configuración del módulo LoRa.
        }
    }
}

STORAGE_REGISTRY = {
    "system_status": "BOOTING", 
    "clock_drift_seconds": 0.0,
    "current_temperature": None,
    "current_pressure_psi": None, 
    "display_alternating_item": "temp",
    "latest_pressure_adc_voltage": None,
    "clock_info": {} #! Añadido por ClockService
}

HARDWARE_CONFIGURATION = {
    "i2c": { 
        "1": { "sda": 21, "scl": 22, "freq": 100000 } 
    }, 
    "uart": { 
        # El baudrate aquí es el que usará el MCU para comunicarse con el módulo LoRa.
        # Debe coincidir con la configuración UART del módulo LoRa.
        "1": { "tx": 17, "rx": 16, "baudrate": 9600 } 
    },
    "devices":{
        "rtc":          { "driver": "DS3231", "bus_type": "i2c", "bus_id": "1", "address": 0x68 },
        "lcd_main":     { "driver": "LCD_I2C", "bus_type": "i2c", "bus_id": "1", "address": 0x27, "rows": 2, "cols": 16 },
        "pressure_adc": { "driver": "ADC_Pin", "pin": 34, "attenuation": "ATTN_11DB"}
        # Ejemplo si quisieras gestionar los pines M0/M1/AUX a través del HardwareManager:
        # "lora_m0":      { "driver": "GPIO_Pin", "pin": 25, "mode": "OUT", "initial_value": 0 },
        # "lora_m1":      { "driver": "GPIO_Pin", "pin": 26, "mode": "OUT", "initial_value": 0 },
        # "lora_aux":     { "driver": "GPIO_Pin", "pin": 27, "mode": "IN" },
    }
}
STORAGE_PATH = 'data/storage.json'
DEFAULT_LOG_LEVEL = "DEBUG"

print("[env.py] Configuration variables defined.")
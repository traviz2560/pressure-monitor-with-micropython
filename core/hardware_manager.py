import asyncio
import sys 
from machine import Pin, ADC 
from lib.urtc import DS3231
from lib.machine_i2c_lcd import I2cLcd 

from .constants import DeviceState # Removed unused HW_RES_ACTION constants

DRIVER_CLASS_MAP = {
    "DS3231": DS3231,
    "LCD_I2C": I2cLcd,
    "GPIO_Pin": Pin, 
    "ADC_Pin": ADC,   
}

class HardwareManager:
    def __init__(self, logger, hw_primitives: dict, hw_config: dict, os_instance):
        self.log = logger 
        self.os = os_instance 
        self.hw_primitives = hw_primitives
        self.device_config_all = hw_config.get('devices', {}) 
        
        self.drivers = {} 
        self.bus_locks = {} 
        self._delegated_resources = {}

    def _get_bus_lock(self, resource_name: str) -> asyncio.Lock | None:
        if not resource_name: return None 
        lock_key = f"{resource_name}_bus_lock" 
        if lock_key not in self.bus_locks:
            self.log.debug(f"Creating bus lock for '{resource_name}' as '{lock_key}'")
            self.bus_locks[lock_key] = asyncio.Lock()
        return self.bus_locks[lock_key]
    
    async def initialize_all_drivers(self):
        self.log.info("Starting asynchronous driver initialization...")
        init_tasks = []
        for name, config in self.device_config_all.items():
            self.drivers[name] = {
                'instance': None, 'lock_key': None, 
                'state': DeviceState.UNINITIALIZED, 'config': config.copy()
            }
            init_tasks.append(self._initialize_single_driver(name))
        
        results = await asyncio.gather(*init_tasks, return_exceptions=True)
        
        for i, name_key in enumerate(self.device_config_all.keys()): # Ensure correct mapping if dict order changes
            if isinstance(results[i], Exception):
                self.log.error(f"Exception during initialization of driver '{name_key}': {results[i]}")
                if self.drivers[name_key]['state'] != DeviceState.FAILED:
                     self.drivers[name_key]['state'] = DeviceState.FAILED
        self.log.info("Asynchronous driver initialization process completed.")

    def log_driver_states(self):
        self.log.info("Current driver states:")
        if not self.drivers: self.log.info("  No drivers configured."); return
        for name, data in self.drivers.items():
            self.log.info(f"  - {name}: {data.get('state', 'UNKNOWN_STATE')}")

    async def _initialize_single_driver(self, name: str):
        driver_entry = self.drivers[name]
        if driver_entry['state'] != DeviceState.UNINITIALIZED:
            self.log.warn(f"Driver '{name}' not UNINITIALIZED ({driver_entry['state']}). Skipping.")
            return

        driver_entry['state'] = DeviceState.INITIALIZING
        config = driver_entry['config']
        driver_name_from_config = config.get("driver")
        driver_class = DRIVER_CLASS_MAP.get(driver_name_from_config)
        self.log.info(f"Initializing driver '{name}' (Type: {driver_name_from_config})...")

        if not driver_class:
            self.log.error(f"Unknown driver type '{driver_name_from_config}' for '{name}'.")
            driver_entry['state'] = DeviceState.FAILED; return

        instance = None; bus_resource_key = None
        try:
            if driver_name_from_config in ["DS3231", "LCD_I2C"]:
                bus_type = config.get("bus_type"); bus_id_str = str(config.get("bus_id", "1"))
                if bus_type != "i2c": raise ValueError(f"'{name}' expects 'i2c', got '{bus_type}'.")
                bus_resource_key = f"i2c_{bus_id_str}"
                bus_obj = self.hw_primitives.get(bus_resource_key)
                if not bus_obj: raise ValueError(f"I2C primitive '{bus_resource_key}' for '{name}' not found.")
                address = config.get("address"); 
                if address is None: raise ValueError(f"Missing 'address' for I2C dev '{name}'.")

                i2c_lock = self._get_bus_lock(bus_resource_key)
                async with i2c_lock: # type: ignore
                    self.log.debug(f"I2C lock acquired for initializing '{name}'.")
                    if driver_name_from_config == "DS3231":
                        instance = driver_class(bus_obj, address)
                        _ = instance.datetime() # Life-check
                        self.log.debug(f"DS3231 '{name}' life-check OK.")
                    elif driver_name_from_config == "LCD_I2C":
                        instance = driver_class(bus_obj, address, config['rows'], config['cols'])
                        instance.clear() # Life-check (clear implicitly tests basic communication)
                        self.log.debug(f"LCD_I2C '{name}' instance created & cleared.")
                self.log.debug(f"I2C lock released for '{name}'.")

            elif driver_name_from_config == "GPIO_Pin": # Synchronous init
                pin_num = config["pin"]; pin_key = f"gpio_{pin_num}"
                pin_obj = self.hw_primitives.get(pin_key) or Pin(pin_num)
                self.hw_primitives[pin_key] = pin_obj # Ensure it's stored
                mode_str=config.get('mode','IN').upper(); pull_str=config.get('pull'); init_val=config.get('initial_value')
                pin_mode = Pin.IN if mode_str == "IN" else Pin.OUT
                pull_val = getattr(Pin, pull_str.upper(), None) if pull_str else None
                pin_obj.init(mode=pin_mode, value=init_val if pin_mode == Pin.OUT and init_val is not None else None, pull=pull_val)
                instance = pin_obj
                self.log.debug(f"GPIO_Pin '{name}' (Pin {pin_num}) configured.")

            elif driver_name_from_config == "ADC_Pin": # Synchronous init
                pin_num = config["pin"]; adc_key = f"adc_{pin_num}"
                adc_obj = self.hw_primitives.get(adc_key) or ADC(Pin(pin_num))
                self.hw_primitives[adc_key] = adc_obj
                atten_str=config.get('attenuation','ATTN_11DB'); atten_val=getattr(ADC,atten_str.upper(),None)
                if atten_val: adc_obj.atten(atten_val)
                _ = adc_obj.read_u16() # Life-check
                instance = adc_obj
                self.log.debug(f"ADC_Pin '{name}' (Pin {pin_num}) configured.")
            
            else: raise ValueError(f"Driver '{driver_name_from_config}' has no specific init logic.")

            driver_entry.update({'instance': instance, 'lock_key': bus_resource_key, 'state': DeviceState.READY})
            self.log.info(f"Driver '{name}' (Type: {driver_name_from_config}) initialized successfully. State: READY.")
        except Exception as e:
            self.log.error(f"FAILED to initialize driver '{name}': {e}")
            sys.print_exception(e)
            driver_entry['state'] = DeviceState.FAILED

    async def execute_action(self, device_name: str, method_name: str, args: tuple, kwargs: dict, requester_service: str = None) -> dict:
        response = {'request_ok': False, 'value': None} 
        driver_entry = self.drivers.get(device_name)

        if not driver_entry:
            response['error'] = f"Device '{device_name}' not configured."
            self.log.error(response['error']); return response
        if driver_entry['state'] != DeviceState.READY:
            response['error'] = f"Device '{device_name}' not READY. State: {driver_entry['state']}."
            self.log.warn(response['error'] + f" (Req: {method_name} by {requester_service})"); return response
        instance = driver_entry['instance']
        if instance is None:
            response['error'] = f"No instance for READY device '{device_name}'."; self.log.error(response['error'])
            driver_entry['state'] = DeviceState.FAILED; return response

        bus_resource_key = driver_entry.get('lock_key')
        if bus_resource_key and requester_service: # Delegation Check
            owner = self._delegated_resources.get(bus_resource_key)
            if owner and owner != requester_service:
                response['error'] = f"Resource '{bus_resource_key}' for '{device_name}' delegated to '{owner}'. Denied for '{requester_service}'."
                self.log.warn(response['error']); return response
        
        asyncio_bus_lock = self._get_bus_lock(bus_resource_key)
        try: method_to_call = getattr(instance, method_name)
        except AttributeError:
            response['error'] = f"Method '{method_name}' not found on '{device_name}' ({type(instance).__name__})."
            self.log.error(response['error']); return response
        
        # self.log.debug(f"HWMAN Call: {device_name}.{method_name}, Lock: {bool(asyncio_bus_lock)}")
        try:
            if asyncio_bus_lock:
                async with asyncio_bus_lock:
                    # Ensure args are passed correctly; method_to_call expects them unpacked.
                    # The 'args' tuple itself should contain the individual arguments.
                    # e.g., for move_to(col,row), args should be (col,row)
                    # for putstr(text), args should be (text,)
                    # for datetime(dt_tuple), args should be (dt_tuple,)
                    result = method_to_call(*args, **kwargs) if (args or kwargs) else method_to_call()
            else: 
                result = method_to_call(*args, **kwargs) if (args or kwargs) else method_to_call()
            
            response['value'] = result; response['request_ok'] = True
            # self.log.debug(f"Call to {device_name}.{method_name} OK. Result type: {type(result)}")
        except TypeError as te: # Usually indicates wrong number/type of arguments to method_to_call
            response['error'] = f"TypeError calling {device_name}.{method_name} with args={args}, kwargs={kwargs}: {te}"
            self.log.error(response['error']); sys.print_exception(te) #! Print stack for TypeError
        except Exception as e:
            response['error'] = f"Exception during {device_name}.{method_name}: {type(e).__name__}: {e}"
            self.log.error(response['error']); sys.print_exception(e)
        
        # self.log.debug(f"execute_action for {device_name}.{method_name} response: OK={response.get('request_ok')}, Val={str(response.get('value'))[:30]}")
        return response
    
    async def handle_delegation_request(self, action: str, resource_key: str, requester_service: str) -> dict:
        response = {'request_ok': False, 'error': 'Not fully implemented for delegation'}
        self.log.warn("handle_delegation_request called but not fully implemented for this refactor pass.")
        return response

    def get_drivers_status(self) -> dict:
        return {name: data.get('state', DeviceState.UNINITIALIZED) for name, data in self.drivers.items()}

    async def cleanup_all_drivers(self):
        self.log.info("Cleaning up all managed drivers...")
        # ... (implementation from previous response seems okay)
        # Ensure execute_action is called correctly for cleanup methods
        cleanup_actions_taken = 0
        for name, driver_info in self.drivers.items():
            if not driver_info.get('instance'): continue
            instance = driver_info['instance']; driver_type_name = type(instance).__name__ 
            cleanup_method_name = None; cleanup_args = ()
            
            if driver_type_name == "Pin" and hasattr(instance, 'mode') and instance.mode() == Pin.OUT:
                cleanup_method_name, cleanup_args = 'value', (0,)
            elif driver_type_name == "DS3231" and hasattr(instance, 'no_interrupt'):
                cleanup_method_name = 'no_interrupt'
            elif driver_type_name == "I2cLcd":
                await self.execute_action(name, 'clear', (), {}, requester_service='os_shutdown')
                await self.execute_action(name, 'backlight_off', (), {}, requester_service='os_shutdown')
                cleanup_actions_taken +=2; continue 
            if cleanup_method_name:
                try:
                    await self.execute_action(name, cleanup_method_name, cleanup_args, {}, requester_service='os_shutdown')
                    cleanup_actions_taken += 1
                except Exception as e: self.log.error(f"Error during cleanup for '{name}.{cleanup_method_name}': {e}")
        self.log.info(f"Driver cleanup finished. Actions: {cleanup_actions_taken}")
import sys
import time
import asyncio
import ujson
from machine import I2C, UART, Pin, ADC, RTC as MachineRTC, wake_reason 

from .message import Message
from .service import Service
from .hardware_manager import HardwareManager
from .constants import (
    DeviceState, OS_MSG_TYPE_HW_ACTION, OS_MSG_TYPE_HW_ACTION_RESPONSE,
    OS_MSG_TYPE_HW_RESOURCE_LOCK_REQUEST, OS_MSG_TYPE_HW_RESOURCE_LOCK_RESPONSE,
    OS_MSG_TYPE_OS_COMMAND, OS_MSG_TYPE_SERVICE_COMMAND, OS_MSG_TYPE_STATUS_REPORT,
    OS_MSG_TYPE_BROADCAST, OS_MSG_TYPE_STORAGE_UPDATE, #! Added STORAGE_UPDATE
    OS_CMD_CREATE_SERVICE, OS_CMD_STOP_SERVICE, OS_CMD_PAUSE_SERVICE, OS_CMD_RESUME_SERVICE,
    OS_CMD_SHUTDOWN, OS_CMD_SAVE_STORAGE, OS_CMD_GET_STATUS, OS_CMD_REINIT_HW_MANAGER,
    SVC_CMD_SHOW_BOOT_STATUS #! Added for specific service command
)

from lib.queue import QueueFull 
import lib.urtc as urtc_module 

from utils import get_logger
from env import HARDWARE_CONFIGURATION, SERVICE_REGISTRY, STORAGE_REGISTRY

class MicroOS:
    def __init__(self, storage_path='/data/storage.json'):
        self.storage_path = storage_path
        self.storage = {} 
        self._storage_dirty = False 
        self.hw_config_full = HARDWARE_CONFIGURATION
        self.svc_reg = SERVICE_REGISTRY 
        self.loop = asyncio.get_event_loop()
        self.loop.set_exception_handler(self._handle_exception)
        self.services = {} 
        self._shutdown_event = asyncio.Event()
        self._is_running = False 
        self.hardware_primitives = {}
        self.hardware_manager = None 
        self.system_rtc = MachineRTC() 
        self.urtc_lib = urtc_module 
        self.wake_reason_code = wake_reason()
        self.log = get_logger("MicroOS")
        self._load_storage()

    @property
    def is_running(self): return self._is_running and not self._shutdown_event.is_set()

    def _load_storage(self):
        try:
            with open(self.storage_path, 'r') as f: loaded_data = ujson.load(f)
            self.storage = STORAGE_REGISTRY.copy(); self.storage.update(loaded_data) 
            self.log.info(f"Storage loaded from {self.storage_path}")
        except Exception as e:
            self.log.warn(f"Load storage FAIL from {self.storage_path}: {e}. Using default.")
            self.storage = STORAGE_REGISTRY.copy()
        self._storage_dirty = False 

    def _save_storage(self):
        if not self._storage_dirty: self.log.debug("Storage clean, skip save."); return
        try:
            # Ensure directory exists (MicroPython specific)
            # This part is a bit hacky for general paths, adjust if needed
            try:
                import os; parts = self.storage_path.split('/')
                if len(parts) > 1:
                    path_only = "/".join(parts[:-1])
                    # Attempt to stat, if it fails or not a dir, try to create
                    is_dir = False
                    try: is_dir = (os.stat(path_only)[0] & 0x4000) != 0 # S_IFDIR
                    except OSError: pass # Path doesn't exist
                    if not is_dir: os.mkdir(path_only)
            except Exception: pass

            with open(self.storage_path, 'w') as f: ujson.dump(self.storage, f)
            self.log.info(f"Storage saved to {self.storage_path}")
            self._storage_dirty = False 
        except Exception as e:
            self.log.error(f"Save storage FAIL to {self.storage_path}: {e}"); sys.print_exception(e)

    def mark_storage_dirty(self, changed_keys: list = None): #! Added changed_keys
        if not self._storage_dirty: self.log.debug("Storage marked dirty.")
        self._storage_dirty = True
        # Broadcast that storage has been updated, with keys that changed
        self.send_message('os', OS_MSG_TYPE_BROADCAST, OS_MSG_TYPE_STORAGE_UPDATE, {'changed_keys': changed_keys or []})


    def is_storage_dirty(self): return self._storage_dirty

    def _init_hardware_primitives(self):
        self.log.info("Initializing hardware primitives...");
        # ... (I2C, UART, GPIO/ADC placeholders - code from previous response unchanged, assumed OK)
        i2c_configs = self.hw_config_full.get('i2c', {})
        for bus_id_str, conf in i2c_configs.items():
            try:
                bus_id=int(bus_id_str); i2c_key=f"i2c_{bus_id}"; scl_p,sda_p=Pin(conf['scl']),Pin(conf['sda'])
                freq=conf.get('freq',100000)
                self.hardware_primitives[i2c_key]=I2C(bus_id,scl=scl_p,sda=sda_p,freq=freq)
                self.log.info(f"Primitive I2C({bus_id}) SCL={conf['scl']},SDA={conf['sda']},F={freq}Hz OK.")
            except Exception as e: self.log.error(f"I2C primitive {bus_id_str} init FAIL: {e}"); sys.print_exception(e)
        uart_configs = self.hw_config_full.get('uart', {})
        for bus_id_str, conf in uart_configs.items():
            try:
                bus_id=int(bus_id_str); uart_key=f"uart_{bus_id}"
                self.hardware_primitives[uart_key]=UART(bus_id,baudrate=conf['baudrate'],tx=conf['tx'],rx=conf['rx'])
                self.log.info(f"Primitive UART({bus_id}) TX={conf['tx']},RX={conf['rx']} OK.")
            except Exception as e: self.log.error(f"UART primitive {bus_id_str} init FAIL: {e}"); sys.print_exception(e)
        device_configs = self.hw_config_full.get('devices', {})
        for name, conf in device_configs.items():
            driver_name=conf.get("driver"); pin_num=conf.get("pin")
            if pin_num is None: continue
            try:
                if driver_name=="GPIO_Pin": pin_key=f"gpio_{pin_num}"
                elif driver_name=="ADC_Pin": pin_key=f"adc_{pin_num}"
                else: continue
                if pin_key not in self.hardware_primitives:
                    if "gpio" in pin_key: self.hardware_primitives[pin_key]=Pin(pin_num)
                    elif "adc" in pin_key: self.hardware_primitives[pin_key]=ADC(Pin(pin_num))
                    self.log.debug(f"Primitive {driver_name}({pin_num}) placeholder for '{name}'.")
            except Exception as e: self.log.error(f"Primitive Pin/ADC init FAIL for '{name}' on pin {pin_num}: {e}")
        self.log.info("Hardware primitive initialization complete.")


    async def _initialize_hardware_drivers(self):
        self.log.info("Initializing hardware drivers via HardwareManager...")
        if not self.hardware_manager:
             self.hardware_manager = HardwareManager( logger=get_logger("HWManager"),
                hw_primitives=self.hardware_primitives, hw_config=self.hw_config_full, os_instance=self )
        await self.hardware_manager.initialize_all_drivers()
        self.log.info("HardwareManager driver initialization completed.")
        self.hardware_manager.log_driver_states()

    async def create_service(self, name: str, cls, config_override: dict = None):
        if name in self.services: self.log.error(f"Svc '{name}' already exists."); return None
        self.log.info(f"Creating svc '{name}' ({cls.__name__})...")
        reg_entry=self.svc_reg.get(name,{}); base_cfg=reg_entry.get('config',{})
        final_cfg=base_cfg.copy(); 
        if config_override: final_cfg.update(config_override)
        svc = None
        try:
            svc = cls(name, self, config=final_cfg); await svc.start() 
            if not svc.is_running: self.log.error(f"Svc '{name}' failed: not running after start."); return None
            self.services[name] = svc; self.log.info(f"Svc '{name}' created & started."); return svc
        except Exception as e:
            self.log.error(f"EXCEPTION create/start svc '{name}': {e}"); sys.print_exception(e)
            if svc and name in self.services and self.services[name]==svc: del self.services[name]
            elif svc and name not in self.services: self.log.debug(f"Svc '{name}' instance created but failed before OS list add.")
            return None

    async def stop_service(self, name: str, params: dict = None):
        svc=self.services.get(name); reason=params.get('reason','OS req') if params else 'OS req'
        if not isinstance(svc,Service): self.log.warn(f"Svc '{name}' not found for stop (R: {reason})."); return False
        self.log.info(f"Stopping svc '{name}' (R: {reason})...")
        try:
            await svc.stop(); 
            if name in self.services: del self.services[name]
            self.log.info(f"Svc '{name}' stopped & removed from OS list.")
            return True
        except Exception as e:
            self.log.error(f"EXCEPTION stopping svc '{name}': {e}"); sys.print_exception(e)
            if name in self.services: del self.services[name]; self.log.warn(f"Svc '{name}' removed despite error during stop.")
            return False
        
    def send_message(self, sender: str, recipient: str, msg_type: str, payload: dict = None):
        msg=Message(sender,recipient,msg_type,payload if payload is not None else {})
        if recipient==OS_MSG_TYPE_BROADCAST: self._broadcast(msg); return
        if recipient=='os': asyncio.create_task(self.handle_os_message(msg)); return
        
        target_svc = self.services.get(recipient)
        if isinstance(target_svc, Service):
            try: target_svc.inbox.put_nowait(msg)
            except QueueFull: self.log.warn(f"Inbox full for '{recipient}'. Msg from '{sender}' (type:{msg_type}) dropped.")
            except Exception as e: self.log.error(f"Err queueing msg for '{recipient}': {e}")
        else: self.log.warn(f"Unknown recipient '{recipient}'. Msg from '{sender}' (type:{msg_type}) dropped.")

    def _broadcast(self, msg: Message):
        # self.log.debug(f"Broadcasting from '{msg.sender}': type={msg.type}, p_keys={list(msg.payload.keys())}")
        for name,svc_instance in self.services.items():
            if name!=msg.sender and isinstance(svc_instance,Service) and svc_instance.is_running:
                broadcast_msg=Message(msg.sender,name,msg.type,msg.payload.copy() if msg.payload else {})
                try: svc_instance.inbox.put_nowait(broadcast_msg)
                except QueueFull: self.log.warn(f"Inbox full for '{name}' during broadcast from '{msg.sender}'. Type '{msg.type}' dropped.")
                except Exception as e: self.log.error(f"Failed to queue broadcast msg for '{name}': {e}")

    async def handle_os_message(self, msg: Message):
        # self.log.debug(f"OS Handling msg from '{msg.sender}': type={msg.type}, p_keys={list(msg.payload.keys())}")
        if not self.hardware_manager and msg.type in [OS_MSG_TYPE_HW_ACTION, OS_MSG_TYPE_HW_RESOURCE_LOCK_REQUEST]:
            # ... (error response if HWM not ready - code from previous response unchanged)
            self.log.error("HWManager not ready for HW message."); return 
        if msg.type == OS_MSG_TYPE_HW_ACTION:
            req_id=msg.payload.get('request_id'); reply_to=msg.payload.get('reply_to')
            if req_id is None or reply_to is None: self.log.error(f"HW_ACTION from {msg.sender} missing req_id/reply_to."); return
            hw_resp = await self.hardware_manager.execute_action( # type: ignore
                msg.payload.get('device'), msg.payload.get('method'),
                tuple(msg.payload.get('args',[])), msg.payload.get('kwargs',{}), msg.sender )
            hw_resp['request_id']=req_id; self.send_message('os',reply_to,OS_MSG_TYPE_HW_ACTION_RESPONSE,hw_resp)
        elif msg.type == OS_MSG_TYPE_OS_COMMAND: await self._handle_os_level_command(msg)
        elif msg.type == OS_MSG_TYPE_SERVICE_COMMAND:
            target_name=msg.payload.get('target_service')
            if target_name and target_name in self.services:
                self.send_message(msg.sender,target_name,msg.type,msg.payload)
            else: self.log.warn(f"Svc cmd for unknown target '{target_name}' from {msg.sender}.")
        # elif msg.type == OS_MSG_TYPE_STORAGE_UPDATE and msg.sender == 'os': self._broadcast(msg) # Already done by mark_storage_dirty
        else: self.log.warn(f"OS received unhandled msg type: '{msg.type}' from {msg.sender}")

    async def _handle_os_level_command(self, msg: Message):
        action=msg.payload.get('action'); target_name=msg.payload.get('name'); params=msg.payload.get('params',{})
        if action==OS_CMD_CREATE_SERVICE and target_name:
            # ... (create service logic - code from previous response unchanged)
            svc_info=self.svc_reg.get(target_name)
            if svc_info:
                svc_cls=svc_info.get('class')
                if svc_cls and isinstance(svc_cls,type): asyncio.create_task(self.create_service(target_name,svc_cls,params))
                else: self.log.error(f"Cmd '{OS_CMD_CREATE_SERVICE}': Invalid class for '{target_name}'.")
            else: self.log.error(f"Cmd '{OS_CMD_CREATE_SERVICE}': Svc '{target_name}' not in registry.")
        elif action==OS_CMD_STOP_SERVICE and target_name: asyncio.create_task(self.stop_service(target_name,params))
        elif action==OS_CMD_PAUSE_SERVICE and target_name:
            svc=self.services.get(target_name); 
            if svc: await svc.pause()
            else: self.log.warn(f"Cmd '{OS_CMD_PAUSE_SERVICE}': Svc '{target_name}' not found.")
        elif action==OS_CMD_RESUME_SERVICE and target_name:
            svc=self.services.get(target_name);
            if svc: await svc.resume()
            else: self.log.warn(f"Cmd '{OS_CMD_RESUME_SERVICE}': Svc '{target_name}' not found.")
        elif action==OS_CMD_SHUTDOWN: await self.shutdown()
        elif action==OS_CMD_SAVE_STORAGE: self._save_storage()
        elif action==OS_CMD_GET_STATUS:
            # ... (get status logic - code from previous response unchanged)
            status_payload={'services':{},'hardware_devices':{},'storage_dirty':self.is_storage_dirty()}
            for sn,si in self.services.items(): status_payload['services'][sn]={'r':si.is_running,'p':si.is_paused,'c':si.is_critical}
            if self.hardware_manager:status_payload['hardware_devices']=self.hardware_manager.get_drivers_status()
            self.send_message('os',msg.sender,OS_MSG_TYPE_STATUS_REPORT,status_payload)
        elif action==OS_CMD_REINIT_HW_MANAGER:
            self.log.warn("Re-initializing HW Manager and drivers...");
            if self.hardware_manager: await self.hardware_manager.cleanup_all_drivers()
            await self._initialize_hardware_drivers(); self.log.warn("HW re-init complete.")
        else: self.log.warn(f"Unknown OS cmd action: '{action}' from {msg.sender}")

    async def run(self):
        self.log.info(f"--- MicroOS Starting --- (Wake: {self.wake_reason_code})")
        self._is_running=True; self._init_hardware_primitives(); await self._initialize_hardware_drivers()
        
        svc_items=[{'n':n,'c':d.get('class'),'as':d.get('autostart',True),'cr':d.get('config',{}).get('is_critical',False),'so':d.get('start_order',100)} for n,d in self.svc_reg.items()]
        svc_items.sort(key=lambda x:(x['so'],not x['cr']))
        
        self.log.info("Starting services..."); failed_crit_svcs=[]
        for item in svc_items:
            n,cls,autostart,is_crit=item['n'],item['c'],item['as'],item['cr']
            if not autostart: self.log.info(f"Svc '{n}' autostart=False, skip."); continue
            if not cls or not isinstance(cls,type) or not issubclass(cls,Service): self.log.error(f"Invalid class for '{n}', skip."); continue
            self.log.info(f"Attempting start: '{n}' (Order:{item['so']}, Crit:{is_crit})...")
            svc_inst = await self.create_service(n,cls)
            if not svc_inst:
                self.log.error(f"Svc '{n}' FAILED to start.")
                if is_crit: self.log.critical(f"CRITICAL Svc '{n}' FAILED."); failed_crit_svcs.append(n)
        
        if failed_crit_svcs:
            self.log.critical(f"Crit svcs failed: {failed_crit_svcs}. System HALT."); self.storage['system_status']=f"CRIT_HALT:{','.join(failed_crit_svcs)}"
            self._save_storage(); await self.shutdown(graceful=False); return
        
        self.storage.setdefault('system_status','RUN_OK'); self.mark_storage_dirty()
        
        display_cfg=self.svc_reg.get('display',{}).get('config',{}); boot_layout=display_cfg.get('boot_status_layout')
        display_svc_name='display'
        if boot_layout and display_svc_name in self.services:
            self.log.info(f"Requesting boot status layout '{boot_layout}' on display.")
            self.send_message('os', display_svc_name, OS_MSG_TYPE_SERVICE_COMMAND, {
                'target_service':display_svc_name, 'action':SVC_CMD_SHOW_BOOT_STATUS, 'layout_name':boot_layout,
                'services_status': {name:svc.is_running for name,svc in self.services.items()} })
        else: self.log.info("No boot status layout or display service not found.")
        
        self.log.info("--- MicroOS Main Event Loop Entered ---")
        await self._shutdown_event.wait()
        self.log.info("--- MicroOS Shutdown Sequence (Post _shutdown_event) ---")
    
    async def shutdown(self, graceful=True):
        if not self._is_running or self._shutdown_event.is_set(): self.log.info("Shutdown already in progress/OS not running."); return
        self.log.info(f"OS Shutdown requested (Graceful:{graceful}). Setting event."); self._shutdown_event.set()
        if graceful:
            self.log.info("Stopping services gracefully..."); stop_tasks=[self.stop_service(n) for n in list(self.services.keys())]
            if stop_tasks: await asyncio.gather(*stop_tasks,return_exceptions=True)
            self.log.info("All services stopped.")
        else:
            self.log.warn("Forced (non-graceful) shutdown of services.")
            for svc in list(self.services.values()): # Iterate copy
                if svc._main_task and not svc._main_task.done(): svc._main_task.cancel()
                if svc._message_processor_task and not svc._message_processor_task.done(): svc._message_processor_task.cancel()
            self.services.clear()
        self.log.info("Performing final storage save..."); self._save_storage()
        if self.hardware_manager: self.log.info("Cleaning up HW drivers..."); await self.hardware_manager.cleanup_all_drivers() # type: ignore
        self._is_running=False; self.log.info("--- MicroOS Shutdown Complete ---")
    
    def _handle_exception(self, loop, context):
        msg=context.get('message','No msg'); exc=context.get('exception',None); fut=context.get('future',None)
        self.log.critical(f"!!! Unhandled Asyncio Task Exception: {msg} !!!")
        if exc: self.log.critical(f"    Exc Type: {type(exc).__name__}, Val: {exc}"); sys.print_exception(exc)
        if fut: self.log.critical(f"    Task: {fut}")
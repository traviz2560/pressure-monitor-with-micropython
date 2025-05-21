import asyncio
import sys
import time
from .message import Message
from .constants import (
    OS_MSG_TYPE_HW_ACTION_RESPONSE, OS_MSG_TYPE_HW_RESOURCE_LOCK_RESPONSE,
    OS_MSG_TYPE_SERVICE_COMMAND, OS_MSG_TYPE_HW_ACTION, OS_MSG_TYPE_LOG,
    SVC_CMD_PAUSE, SVC_CMD_RESUME, SVC_CMD_STOP, SVC_CMD_GET_INFO,
    OS_MSG_TYPE_BROADCAST, OS_MSG_TYPE_STORAGE_UPDATE
)

from lib.queue import Queue 
from utils import get_logger

class Service:
    def __init__(self, name: str, os_instance, config: dict):
        self.name=name; self.os=os_instance; self.config=config; self.log=get_logger(f"SVC:{self.name}",config.get('log_level'))
        self.inbox=Queue(maxsize=config.get('inbox_size',20)); self._main_task=None; self._message_processor_task=None
        self._running_event=asyncio.Event();self._stop_requested_event=asyncio.Event();self._paused_event=asyncio.Event();self._paused_event.set()
        self.is_critical=config.get('is_critical',False); self._pending_hw_requests={}

    @property
    def is_running(self): return self._running_event.is_set() and not self._stop_requested_event.is_set()
    @property
    def is_paused(self): return not self._paused_event.is_set()

    async def start(self):
        if self.is_running: self.log.warn("Svc already running."); return
        if self._stop_requested_event.is_set(): self._stop_requested_event.clear()
        self.log.info(f"SVC:{self.name} Starting...");
        try:
            await self.setup() 
            self._main_task = asyncio.create_task(self.run())
            self.log.debug(f"SVC:{self.name} _main_task:{self._main_task}")
            self._message_processor_task = asyncio.create_task(self._message_processor())
            self.log.debug(f"SVC:{self.name} _msg_proc_task:{self._message_processor_task}")
            self._running_event.set() 
            self.log.info(f"SVC:{self.name} Started & tasks created.")
        except Exception as e:
            self.log.error(f"Setup/task creation fail for SVC:{self.name}: {e}")
            self._running_event.clear() 
            if self._main_task and not self._main_task.done(): self._main_task.cancel()
            if self._message_processor_task and not self._message_processor_task.done(): self._message_processor_task.cancel()
            raise 

    async def stop(self): 
        # ... (stop logic from previous response - minor logging changes for brevity, assumed OK)
        if self._stop_requested_event.is_set() or not self._running_event.is_set():
            log_method = self.log.debug if self._stop_requested_event.is_set() else self.log.info
            log_method(f"SVC:{self.name} Stop: already stopping or not running.")
            return
        self.log.info(f"SVC:{self.name} Stopping..."); self._stop_requested_event.set(); self._running_event.clear(); self._paused_event.set()
        tasks_to_cancel = []
        if self._main_task and not self._main_task.done(): tasks_to_cancel.append(self._main_task)
        if self._message_processor_task and not self._message_processor_task.done(): tasks_to_cancel.append(self._message_processor_task)
        if tasks_to_cancel:
            await asyncio.gather(*[task.cancel() for task in tasks_to_cancel], return_exceptions=True) # Simpler cancel
            self.log.debug(f"SVC:{self.name} {len(tasks_to_cancel)} tasks cancellation processed.")
        self._main_task=None; self._message_processor_task=None
        try: await self.cleanup()
        except Exception as e: self.log.error(f"SVC:{self.name} Cleanup fail: {e}");sys.print_exception(e)
        for req_id,data in list(self._pending_hw_requests.items()): 
            data['response']={'request_id':req_id,'request_ok':False,'error':'service_stopped'}; data['event'].set()
        self._pending_hw_requests.clear(); 
        self.log.info(f"SVC:{self.name} Stopped.")


    async def pause(self): 
        if self.is_critical and not self.config.get('allow_pause_if_critical',False): self.log.info("Crit svc pause denied."); return
        if self._paused_event.is_set(): self.log.info(f"SVC:{self.name} Pausing run loop..."); self._paused_event.clear()
    
    async def resume(self): 
        if not self._paused_event.is_set(): self.log.info(f"SVC:{self.name} Resuming run loop..."); self._paused_event.set()
    
    async def _message_processor(self): 
        self.log.debug(f"SVC:{self.name} Message processor task started.") #! Changed to debug
        while not self._stop_requested_event.is_set():
            try: 
                msg=await self.inbox.get()
                if msg: 
                    # self.log.debug(f"SVC:{self.name} Processing msg: {msg}")
                    await self.on_message(msg)
                    self.inbox.task_done()
            except asyncio.CancelledError: self.log.info(f"SVC:{self.name} Message processor cancelled."); break
            except Exception as e: 
                msg_str = str(msg)[:50] if 'msg' in locals() and msg else "N/A"
                self.log.error(f"SVC:{self.name} MsgPrc err on msg '{msg_str}': {e}"); sys.print_exception(e)
        self.log.debug(f"SVC:{self.name} Message processor task finished.") #! Changed to debug
    
    async def setup(self): self.log.debug(f"SVC:{self.name} Base Svc setup.")
    
    async def run(self): 
        self.log.debug(f"SVC:{self.name} Base Svc run loop. Waiting for pause event then sleeping.")
        try:
            while not self._stop_requested_event.is_set(): 
                await self._paused_event.wait()
                await asyncio.sleep(3600) 
        except asyncio.CancelledError: self.log.info(f"SVC:{self.name} Base Svc run cancelled.")
        except Exception as e: self.log.error(f"SVC:{self.name} Run loop error: {e}"); sys.print_exception(e)
        finally: self.log.debug(f"SVC:{self.name} Base Svc run finished.")
    
    async def on_message(self, msg: Message):
        # self.log.debug(f"SVC:{self.name} on_message received: {msg}")
        if msg.sender=='os' and (msg.type==OS_MSG_TYPE_HW_ACTION_RESPONSE or msg.type==OS_MSG_TYPE_HW_RESOURCE_LOCK_RESPONSE):
            req_id = msg.payload.get('request_id')
            # self.log.debug(f"SVC:{self.name} received HW resp for req_id {req_id}. OK: {msg.payload.get('request_ok')}")
            pending_req = self._pending_hw_requests.get(req_id)
            if pending_req:
                # self.log.debug(f"SVC:{self.name} found pending req for {req_id}. Setting event.")
                pending_req['response'] = msg.payload; pending_req['event'].set()
            else: self.log.warn(f"SVC:{self.name} HW resp for unknown/timed-out req_id: {req_id}.")
            return 
        
        if msg.type == OS_MSG_TYPE_SERVICE_COMMAND: 
            # Check if this message is for this service instance, if 'target_service' is present
            if msg.payload.get('target_service') and msg.payload.get('target_service') != self.name:
                # This can happen if OS broadcasts a service command or sends to wrong service.
                # self.log.debug(f"SVC:{self.name} Received SERVICE_COMMAND not targeted for self. Target: {msg.payload.get('target_service')}")
                return
            await self.handle_service_command(msg.payload)
            return
        
        # Handle broadcast messages like storage_update
        if msg.recipient == OS_MSG_TYPE_BROADCAST and msg.type == OS_MSG_TYPE_STORAGE_UPDATE:
            # self.log.debug(f"SVC:{self.name} received storage_update broadcast: {msg.payload.get('changed_keys')}")
            # Child services can override this to react to specific key changes
            pass # Base service might not do anything with it.
        
    async def cleanup(self): self.log.debug(f"SVC:{self.name} Base Svc cleanup.")
    
    def send_message(self, recipient: str, msg_type: str, payload: dict = None): 
        if not self.is_running and msg_type!=OS_MSG_TYPE_LOG: self.log.warn(f"SVC:{self.name} Send msg while not running (to={recipient},type={msg_type}).")
        self.os.send_message(self.name,recipient,msg_type,payload or {})
    
    async def handle_service_command(self, payload: dict): 
        action=payload.get('action')
        # self.log.debug(f"SVC:{self.name} handling service_command: {action}")
        if action==SVC_CMD_STOP: await self.stop()
        elif action==SVC_CMD_PAUSE: await self.pause()
        elif action==SVC_CMD_RESUME: await self.resume()
        elif action==SVC_CMD_GET_INFO: 
            reply_to=payload.get('reply_to_service',payload.get('original_sender')) # original_sender might be 'os'
            if reply_to: self.send_message(reply_to,'service_info_response',{'s_name':self.name,'info':{'r':self.is_running, 'p': self.is_paused}})
            else: self.log.warn(f"SVC:{self.name} GET_INFO cmd missing reply_to target.")
        # Else: child services will handle their specific commands if they override this or on_message

    async def _request_hardware(self, device_name: str, method_name: str, timeout_s: float = 2.0, args: tuple = (), kwargs: dict = None) -> dict: #! Return type always dict
        req_id=time.ticks_us(); payload={'request_id':req_id,'reply_to':self.name,'device':device_name,'method':method_name,'args':list(args),'kwargs':kwargs or {}}
        resp_event=asyncio.Event(); self._pending_hw_requests[req_id]={'event':resp_event,'response':None}
        
        self.os.send_message(self.name,'os',OS_MSG_TYPE_HW_ACTION,payload)
        # self.log.debug(f"SVC:{self.name} sent HW req {req_id} ({device_name}.{method_name}). Waiting...")
        
        try:
            await asyncio.wait_for_ms(resp_event.wait(),int(timeout_s*1000))
            # self.log.debug(f"SVC:{self.name} event received for req {req_id}.") 
            resp_data = self._pending_hw_requests[req_id].get('response')
            # Ensure a dictionary is always returned, even if something went wrong with response storage
            return resp_data if resp_data else {'request_id':req_id,'request_ok':False,'error':'internal_svc_no_resp_data'}
        except asyncio.TimeoutError:
            self.log.warn(f"SVC:{self.name} TIMEOUT waiting for req {req_id} ({device_name}.{method_name}).") 
            return {'request_id':req_id,'request_ok':False,'error':'timeout_in_service_wait_event'}
        except Exception as e: 
            self.log.error(f"SVC:{self.name} Wait HW resp error {req_id} ({device_name}.{method_name}): {e}");sys.print_exception(e)
            return {'request_id':req_id,'request_ok':False,'error':f'exc_wait_svc: {e}'}
        finally:
            if req_id in self._pending_hw_requests: del self._pending_hw_requests[req_id]
import sys
import asyncio
import time 
from core import Service, Message
from core.constants import (
    OS_MSG_TYPE_SERVICE_COMMAND, OS_CMD_STOP_SERVICE, 
    OS_MSG_TYPE_OS_COMMAND, 
    SVC_CMD_SET_LAYOUT, SVC_CMD_SET_BACKLIGHT, SVC_CMD_SHOW_TEMP_MSG, SVC_CMD_SHOW_BOOT_STATUS, 
    OS_MSG_TYPE_STORAGE_UPDATE 
)

def _format_time_manual_for_display(fmt_str, time_tuple):
    s = fmt_str
    s = s.replace("%H", "{:02d}".format(time_tuple[3]))
    s = s.replace("%M", "{:02d}".format(time_tuple[4]))
    s = s.replace("%S", "{:02d}".format(time_tuple[5])) 
    s = s.replace("%d", "{:02d}".format(time_tuple[2]))
    s = s.replace("%m", "{:02d}".format(time_tuple[1]))
    s = s.replace("%y", "{:02d}".format(time_tuple[0] % 100))
    s = s.replace("%Y", "{}".format(time_tuple[0]))
    return s

class StatusDisplayService(Service):

    @staticmethod 
    def _pad_str(text: str, width: int, align: str = 'left', fill_char: str = ' ') -> str:
        try:
            s_text = str(text); len_text = len(s_text)
        except: return fill_char * width 
        if len_text >= width: return s_text[:width]
        padding_needed = width - len_text
        if align == 'right': return (fill_char * padding_needed) + s_text
        elif align == 'center':
            lp = padding_needed // 2; rp = padding_needed - lp
            return (fill_char * lp) + s_text + (fill_char * rp)
        else: return s_text + (fill_char * padding_needed)

    def __init__(self, name: str, os_instance, config: dict):
        super().__init__(name, os_instance, config)
        self.lcd_device_key = config.get('device_key', 'lcd_main')
        self.refresh_interval_s = float(config.get('refresh_interval_s', 1.0)) 
        self.default_layout = config.get('default_layout', 'main_status')
        self.boot_status_layout_name = config.get('boot_status_layout', "boot_status_overview")
        self.boot_status_duration_s = float(config.get('boot_status_duration_s', 7.0))
        self.time_format = config.get('display_time_format', "%H:%M") 
        self.date_format = config.get('display_date_format', "%d/%m/%y") 
        self.alternate_interval_s = float(config.get('alternate_interval_s', 5.0)) 

        self.lcd_cols = int(config.get('cols', 16)); self.lcd_rows = int(config.get('rows', 2))   
        self.display_buffer = [self._pad_str("", self.lcd_cols) for _ in range(self.lcd_rows)] 
        self.previous_display_buffer = [""] * self.lcd_rows; self.current_layout = "" 
        self.backlight_state = True; self._dirty = True 
        self._initial_lcd_check_done_in_run = False; self._showing_boot_status = False
        self.current_temp_str = "---.-"; self.current_pressure_str = "----" 
        self.current_time_str = "--:--"; self.current_date_str = "--/--/--"
        self.system_status_str = "---"; self.last_alternation_ticks = 0
        self.log.info(f"Initialized LCD '{self.lcd_device_key}' ({self.lcd_rows}x{self.lcd_cols}). Refresh: {self.refresh_interval_s}s")
    
    def _update_local_cache(self): 
        self.current_temp_str = f"{self.os.storage.get('current_temperature', -99.9): >5.1f}".replace("-99.9", "---.-")
        self.system_status_str = self.os.storage.get('system_status', "INI").upper()[:3]
        pressure_val = self.os.storage.get('current_pressure_psi')
        self.current_pressure_str = f"{int(pressure_val): >4}" if pressure_val is not None else "----"
        try:
            now_tuple = time.localtime(time.time())
            self.current_time_str = _format_time_manual_for_display(self.time_format, now_tuple)
            self.current_date_str = _format_time_manual_for_display(self.date_format, now_tuple)
        except Exception as e:
            self.log.error(f"Error formatting time: {e}"); self.current_time_str="ER:ER"; self.current_date_str="ER/ER/ER"

    async def setup(self): 
        await super().setup()
        self.log.info(f"DisplaySvc setup: LCD '{self.lcd_device_key}'. Config OK.")
        if self.os.hardware_manager and self.lcd_device_key not in self.os.hardware_manager.drivers: # type: ignore
            raise RuntimeError(f"LCD '{self.lcd_device_key}' not in HWM for DisplaySvc.")
        self.log.info("DisplaySvc config validated. Initial LCD check in run().")

    async def _perform_initial_lcd_check(self) -> bool:
        self.log.info(f"Performing initial LCD check for '{self.lcd_device_key}'...")
        if not await self._lcd_command('clear', timeout_s=2.5): 
            self.log.error(f"LCD '{self.lcd_device_key}' FAILED initial 'clear'.")
            return False
        self.previous_display_buffer = [""] * self.lcd_rows 
        await self._set_backlight(self.backlight_state) 
        await self.set_layout(self.default_layout, clear_display=False) 
        self.last_alternation_ticks = time.ticks_ms() 
        self.log.info(f"Initial LCD check successful. Default layout '{self.default_layout}' set.")
        return True

    async def run(self):
        if not self._initial_lcd_check_done_in_run:
            check_ok = await self._perform_initial_lcd_check(); self._initial_lcd_check_done_in_run = True
            if not check_ok:
                if self.is_critical:
                    self.log.critical(f"CRIT DisplaySvc FAILED initial LCD check. Requesting stop.")
                    self.send_message('os',OS_MSG_TYPE_OS_COMMAND,{'action':OS_CMD_STOP_SERVICE,'name':self.name,'params':{'reason':'crit_lcd_fail'}})
                    return 
                else: self.log.warn(f"Initial LCD check FAILED. Display will not function.")
        try:
            while self.is_running: 
                await self._paused_event.wait() 
                now_ticks = time.ticks_ms()
                if time.ticks_diff(now_ticks, self.last_alternation_ticks) >= (self.alternate_interval_s * 1000):
                    current_item = self.os.storage.get("display_alternating_item", "temp")
                    next_item = "pressure" if current_item == "temp" else "temp"
                    self.os.storage["display_alternating_item"] = next_item
                    self.os.mark_storage_dirty(["display_alternating_item"]) 
                    self.last_alternation_ticks = now_ticks; self._dirty = True 
                if not self._showing_boot_status: self._dirty = True 
                if self._dirty and not self._showing_boot_status:
                    self._update_local_cache(); self._update_display_buffer_content(); await self._redraw_lcd() 
                
                slept_s=0.0; chunk=0.2 
                while slept_s < self.refresh_interval_s and self.is_running and self._paused_event.is_set():
                    if self._dirty and not self._showing_boot_status: break 
                    await asyncio.sleep(chunk); slept_s += chunk
                if not self.is_running: break
        except asyncio.CancelledError: self.log.info("DisplaySvc run loop cancelled.")
        except Exception as e: self.log.error(f"DisplaySvc run loop error: {e}"); sys.print_exception(e)
        finally:
            self.log.debug("DisplaySvc run loop finished.")
            if not self.is_running: 
                self.log.info("Final LCD cleanup (clear & backlight off).")
                await self._lcd_command('clear',timeout_s=1.0); await self._set_backlight(False) 
    
    async def on_message(self, msg: Message):
        await super().on_message(msg) 
        if msg.type == 'temperature_update' or msg.type == 'pressure_update' or \
           (msg.type == OS_MSG_TYPE_STORAGE_UPDATE and \
            any(k in msg.payload.get('changed_keys',[]) for k in ['system_status','current_temperature', 'current_pressure_psi'])):
            self._dirty = True 
        elif msg.type == OS_MSG_TYPE_SERVICE_COMMAND: 
             payload = msg.payload; action = payload.get('action')
             if payload.get('target_service') and payload.get('target_service') != self.name: return
             if action == SVC_CMD_SET_LAYOUT: 
                  new_layout = payload.get('layout_name')
                  if new_layout: await self.set_layout(new_layout, clear_display=True) 
             elif action == SVC_CMD_SET_BACKLIGHT: await self._set_backlight(payload.get('state', True))
             elif action == SVC_CMD_SHOW_TEMP_MSG: 
                  asyncio.create_task(self._display_message_temporary_task(
                      payload.get('line1',''), payload.get('line2',''), payload.get('duration_ms',2000)))
             elif action == SVC_CMD_SHOW_BOOT_STATUS: 
                  asyncio.create_task(self._display_boot_status_task(
                      payload.get('layout_name',self.boot_status_layout_name), payload.get('services_status',{})))
             else: await super().handle_service_command(payload) 
    
    async def set_layout(self, layout_name: str, clear_display: bool = False) -> bool:
        if layout_name != self.current_layout or clear_display: 
            self.log.info(f"Setting display layout to: '{layout_name}' (Clear: {clear_display})")
            if clear_display:
                await self._lcd_command('clear', timeout_s=1.5) 
                self.previous_display_buffer = [""] * self.lcd_rows 
            self.current_layout = layout_name
            self._fill_buffer_from_layout_template() 
            self._update_local_cache(); self._update_display_buffer_content()    
            self._dirty = True; return True 
        return False

    def _fill_buffer_from_layout_template(self): 
        self.display_buffer = [self._pad_str("", self.lcd_cols) for _ in range(self.lcd_rows)]
        if self.current_layout == "main_status": pass 
        elif self.current_layout == self.boot_status_layout_name: pass
        elif self.current_layout == "settings_menu": 
            if self.lcd_rows > 0: self.display_buffer[0] = self._pad_str("> Option 1", self.lcd_cols)
            if self.lcd_rows > 1: self.display_buffer[1] = self._pad_str("  Option 2", self.lcd_cols)

    def _update_display_buffer_content(self): 
        if self.current_layout == "main_status":
            if self.lcd_rows > 0:
                date_time_str = f"{self.current_date_str} {self.current_time_str}"
                self.display_buffer[0] = self._pad_str(date_time_str, self.lcd_cols, align='left')
            if self.lcd_rows > 1:
                item_to_show = self.os.storage.get("display_alternating_item", "temp")
                line1_content = ""
                status_str = self.system_status_str 
                if item_to_show == "temp":
                    line1_content = f"T:{self.current_temp_str}C {status_str}"
                elif item_to_show == "pressure":
                    line1_content = f"P:{self.current_pressure_str}psi {status_str}"
                else: line1_content = f"{item_to_show[:5].upper()}: ??? {status_str}"
                self.display_buffer[1] = self._pad_str(line1_content, self.lcd_cols)
        elif self.current_layout == self.boot_status_layout_name: pass
    
    async def _redraw_lcd(self): 
        something_written = False
        for r_idx, new_line in enumerate(self.display_buffer):
            if r_idx >= self.lcd_rows: break 
            padded_line = self._pad_str(new_line, self.lcd_cols) 
            if padded_line != self.previous_display_buffer[r_idx]:
                if await self._lcd_command('move_to', 0, r_idx, timeout_s=0.6): 
                    if await self._lcd_command('putstr', padded_line, timeout_s=1.0):
                        self.previous_display_buffer[r_idx] = padded_line
                        something_written = True
                    else: self.log.warn(f"putstr FAIL on row {r_idx}.")
                else: self.log.warn(f"move_to(0,{r_idx}) FAIL.")
        
        if something_written:
            # No necesitas 'self.log_level_int'. self.log.debug() ya lo maneja.
            self.log.debug(f"LCD L0: '{self.previous_display_buffer[0]}'") 
            if self.lcd_rows > 1: self.log.debug(f"LCD L1: '{self.previous_display_buffer[1]}'")
        
        self._dirty = False 
    
    async def _set_backlight(self, state: bool) -> bool:
        if state != self.backlight_state:
            self.log.info(f"Setting backlight {'ON' if state else 'OFF'}")
            m = 'backlight_on' if state else 'backlight_off'
            if await self._lcd_command(m, timeout_s=0.6): self.backlight_state = state; return True
            else: self.log.error(f"Set backlight to {state} FAIL."); return False 
        return False 

    async def _display_message_temporary_task(self, line1: str, line2: str = "", duration_ms: int = 2000): 
        self.log.debug(f"Temp msg: '{line1}','{line2}' for {duration_ms}ms")
        original_layout=self.current_layout;
        paused_here=False
        if self._paused_event.is_set(): await self.pause(); paused_here=True
        temp_b = [self._pad_str("", self.lcd_cols) for _ in range(self.lcd_rows)]
        if self.lcd_rows>0: temp_b[0]=self._pad_str(line1, self.lcd_cols)
        if self.lcd_rows>1 and line2: temp_b[1]=self._pad_str(line2, self.lcd_cols)
        original_previous_buffer = list(self.previous_display_buffer) 
        self.previous_display_buffer=[""]*self.lcd_rows; self.display_buffer=temp_b; await self._redraw_lcd()
        await asyncio.sleep_ms(duration_ms)
        self.previous_display_buffer = original_previous_buffer  
        self.current_layout=original_layout 
        self.log.debug(f"Temp msg END: Restoring layout to '{self.current_layout}'.")
        self._update_local_cache(); self._update_display_buffer_content(); self._dirty = True 
        if paused_here: await self.resume()

    async def _display_boot_status_task(self, layout_name: str, services_status: dict): 
        self.log.info(f"Displaying boot status. Layout:'{layout_name}', Duration:{self.boot_status_duration_s}s")
        self._showing_boot_status = True; original_layout=self.current_layout 
        paused_here=False
        if self._paused_event.is_set(): await self.pause(); paused_here=True
        await self._lcd_command('clear',timeout_s=1.5); self.previous_display_buffer=[""]*self.lcd_rows
        self.current_layout=layout_name; service_names=list(services_status.keys())
        start_ticks=time.ticks_ms(); duration_ms=int(self.boot_status_duration_s*1000)
        current_page=0; display_lines_available=max(0,self.lcd_rows-1)
        while time.ticks_diff(time.ticks_ms(),start_ticks) < duration_ms:
            self.display_buffer=[self._pad_str("",self.lcd_cols) for _ in range(self.lcd_rows)]
            self.display_buffer[0]=self._pad_str("Service Status:",self.lcd_cols)
            if self.lcd_rows>1 and display_lines_available>0:
                start_svc_idx=current_page*display_lines_available
                for i in range(display_lines_available):
                    svc_idx=start_svc_idx+i; line_idx_in_buffer=i+1
                    if line_idx_in_buffer>=self.lcd_rows: break
                    if svc_idx<len(service_names):
                        svc_n=service_names[svc_idx]; stat="OK" if services_status.get(svc_n,False) else "NG"
                        max_name_len=self.lcd_cols-(len(stat)+2);
                        if max_name_len<1: max_name_len=1
                        disp_n=svc_n if len(svc_n)<=max_name_len else svc_n[:max_name_len-1]+"~"
                        self.display_buffer[line_idx_in_buffer]=self._pad_str(f"{disp_n}:{stat}",self.lcd_cols)
            self.previous_display_buffer=[""]*self.lcd_rows; await self._redraw_lcd()
            await asyncio.sleep_ms(2000)
            if display_lines_available>0:
                num_pages=(len(service_names)+display_lines_available-1)//display_lines_available
                if num_pages > 0: current_page=(current_page+1)%num_pages # Ensure num_pages > 0 before modulo
            if not self.is_running or time.ticks_diff(time.ticks_ms(),start_ticks)>=duration_ms: break
        self.log.info("Boot status task: ENDING."); self._showing_boot_status=False
        await self._lcd_command('clear',timeout_s=1.5); self.previous_display_buffer=[""]*self.lcd_rows
        layout_to_restore=original_layout if original_layout else self.default_layout
        self.current_layout=layout_to_restore
        self.log.info(f"Boot status END: Restored layout to '{self.current_layout}'.")
        self._update_local_cache(); self._update_display_buffer_content(); self._dirty=True
        if paused_here: self.log.info("Boot status END: Resuming main run loop."); await self.resume()
        else: self.log.info("Boot status END: Main run loop not paused by this task.")
    
    async def _lcd_command(self, method_name: str, *args, timeout_s: float = 1.0) -> bool: 
        response = await self._request_hardware(
            device_name=self.lcd_device_key, method_name=method_name,
            timeout_s=timeout_s, args=args,
        )
        if response and response.get('request_ok'): return True
        return False
    
    async def cleanup(self):
        await super().cleanup()
        self.log.info("Cleaning up display (final state)...")
        try: await self._lcd_command('clear',timeout_s=1.0); await self._set_backlight(False)
        except Exception as e: self.log.error(f"Error display final cleanup: {e}")
        self.log.info("Display final cleanup complete.")
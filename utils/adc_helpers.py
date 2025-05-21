def _bisect_left(a, x, lo=0, hi=None):
    """Búsqueda binaria para encontrar posición de inserción."""
    if hi is None:
        hi = len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo

class RunningMedianFilter:
    """
    Filtro de mediana móvil eficiente para MicroPython.
    Ventana de tamaño fijo.
    """
    def __init__(self, size: int):
        if not isinstance(size, int) or size <= 0:
            raise ValueError("Filter size must be a positive integer")
        self.size = size
        self.buffer = [0.0] * self.size # Circular buffer for raw values
        self.window = [] # Sorted window of values
        self.count = 0   # Number of values added so far (up to size)
        self.index = 0   # Current index in the circular buffer

    def add(self, value: float):
        """Añade un nuevo valor al filtro."""
        try:
            value = float(value)
        except ValueError:
            # Handle non-float inputs if necessary, or let it raise
            raise ValueError("Filter can only accept float values")

        if self.count < self.size:
            # Phase 1: Filling the buffer and window
            self.buffer[self.index] = value
            # Insert into sorted window
            pos = _bisect_left(self.window, value)
            self.window.insert(pos, value)
            self.count += 1
        else:
            # Phase 2: Buffer is full, replace oldest value
            old_val = self.buffer[self.index]
            self.buffer[self.index] = value
            
            # Remove old_val from sorted window
            # This is the potentially slow part (O(N) for pop in list)
            # For small N (e.g., < 20-30), it's usually acceptable in MicroPython
            try:
                self.window.pop(_bisect_left(self.window, old_val))
            except IndexError: 
                # Should not happen if old_val was in window.
                # Could occur if float precision issues prevent exact match.
                # Fallback: rebuild window (costly, but rare)
                # print(f"WARN: Old value {old_val} not found precisely in filter window. Rebuilding.")
                # self.window = sorted(self.buffer[:self.count]) 
                pass # Or log an error if this happens frequently

            # Insert new_val into sorted window
            pos = _bisect_left(self.window, value)
            self.window.insert(pos, value)
        
        self.index = (self.index + 1) % self.size

    def get_median(self) -> float | None:
        """Obtiene la mediana actual."""
        if not self.window: # Or if self.count == 0
            return None
            
        # Ensure window is sorted (should be if add() is correct)
        # self.window.sort() # Could add this for safety but impacts performance
            
        mid_idx = len(self.window) // 2
        if len(self.window) % 2 == 1: # Odd number of elements
            return self.window[mid_idx]
        else: # Even number of elements
            if mid_idx > 0 and mid_idx <= len(self.window): # Ensure valid indices
                 return (self.window[mid_idx - 1] + self.window[mid_idx]) / 2.0
            elif self.window: # If only one element (shouldn't happen with even check)
                 return self.window[0]
            return None # Should not be reached if window has elements

    def clear(self):
        self.buffer = [0.0] * self.size
        self.window = []
        self.count = 0
        self.index = 0

# --- Funciones de Linealización del ADC ---
# El valor 'x' de entrada aquí se espera que sea el valor normalizado del ADC (0.0 a 1.0)
# raw_adc_reading / adc_max_value (e.g., 4095 for 12-bit)

def custom_adc_to_voltage(x: float) -> float:
    """
    Convierte una lectura de ADC normalizada (0.0-1.0) a un valor linealizado 
    (por ejemplo, voltaje) usando splines por tramos.
    Asegúrate de que los rangos (ej. x < 0.1565) sean exhaustivos y no se solapen.
    """
    if not isinstance(x, (float, int)): return x # Return as is if not a number

    # Ajusta estos rangos y ecuaciones según tu calibración específica.
    # Es crucial que los puntos de quiebre (0.1565, 0.2562, etc.) sean correctos.
    #if x < 0.0586:
    #    return 0
    if x < 0.1565:
        return -0.5115 * (x - 0.0586)**3 + 0.0000 * (x - 0.0586)**2 + 1.0323 * (x - 0.0586) + 0.1002
    elif x < 0.2562: # Usar elif para asegurar exclusividad
        return 0.4063 * (x - 0.1565)**3 + -0.1503 * (x - 0.1565)**2 + 1.0176 * (x - 0.1565) + 0.2008
    elif x < 0.3553:
        return 0.6062 * (x - 0.2562)**3 + -0.0288 * (x - 0.2562)**2 + 0.9997 * (x - 0.2562) + 0.3011
    elif x < 0.4598:
        return -0.8909 * (x - 0.3553)**3 + 0.1515 * (x - 0.3553)**2 + 1.0119 * (x - 0.3553) + 0.4006
    elif x < 0.5524:
        return 2.1905 * (x - 0.4598)**3 + -0.1279 * (x - 0.4598)**2 + 1.0144 * (x - 0.4598) + 0.5070
    elif x < 0.6476:
        return -5.3610 * (x - 0.5524)**3 + 0.4803 * (x - 0.5524)**2 + 1.0470 * (x - 0.5524) + 0.6015
    elif x < 0.7617:
        return 0.2146 * (x - 0.6476)**3 + -1.0514 * (x - 0.6476)**2 + 0.9926 * (x - 0.6476) + 0.7009
    elif x <= 0.9128: # Considera qué hacer si x > 0.9128
        return 2.1566 * (x - 0.7617)**3 + -0.9780 * (x - 0.7617)**2 + 0.7612 * (x - 0.7617) + 0.8008   
    
    # Fallback si x está fuera de los rangos definidos (o para valores > 0.9128)
    # Podrías extrapolar o devolver un valor de error/indicador.
    # Por ahora, si es mayor que el último rango, podrías aplicar la última ecuación o una lineal simple.
    # O simplemente devolver el valor normalizado si no encaja, indicando que está fuera del rango de calibración.
    # print(f"ADC value {x} out of calibrated range for custom_adc_to_voltage")
    return x # Devolver x si no está en ningún rango específico (o manejar como error)

def simple_adc_passthrough(x: float) -> float:
    """Una función de linealización placeholder que no hace nada."""
    return x

# Mapa de funciones de linealización disponibles
LINEARIZATION_FUNCTIONS = {
    "custom_adc_to_voltage": custom_adc_to_voltage,
    "passthrough": simple_adc_passthrough,
    # Añade más funciones de linealización aquí si las necesitas para otros sensores
}
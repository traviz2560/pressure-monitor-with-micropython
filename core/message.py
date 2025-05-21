import time

class Message:
    _id_counter = 0 #! Class variable for unique message IDs (optional, for debugging)

    def __init__(self, sender: str, recipient: str, msg_type: str, payload: dict = None):
        self.sender = sender
        self.recipient = recipient
        self.type = msg_type
        self.payload = payload if payload is not None else {}
        self.timestamp = time.ticks_ms()
        
        # Message._id_counter += 1 #! Optional:
        # self.id = Message._id_counter #! Optional:

    def __str__(self):
        # Limit payload string length for concise logging
        payload_str = str(self.payload)
        if len(payload_str) > 70: #! Reduced length
            payload_str = payload_str[:67] + '...'
        # msg_id_str = f" (ID:{self.id})" if hasattr(self, 'id') else "" #! Optional
        return f"Msg(from={self.sender}, to={self.recipient}, type={self.type}, p_keys={list(self.payload.keys())})"
        # return f"Msg{msg_id_str}(from={self.sender}, to={self.recipient}, type={self.type}, payload={payload_str})"
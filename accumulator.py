import threading

class TranscriptAccumulator:
    """Accumulates multi-clause streaming transcriptions from Antigravity language server.
    
    The server finalizes utterances clause-by-clause with isFinal=True.
    This class correctly preserves all finalized segments and merges them with
    any trailing partial utterance to produce the complete, whole output.
    """
    def __init__(self):
        self.finalized_segments = []
        self.current_partial = ""
        self.lock = threading.Lock()

    def reset(self):
        with self.lock:
            self.finalized_segments.clear()
            self.current_partial = ""

    def process_message(self, msg: dict):
        if not isinstance(msg, dict):
            return
        if "transcription" not in msg:
            return

        t_data = msg["transcription"]
        raw_text = t_data.get("text", "")
        if not raw_text:
            return

        text = raw_text.strip()
        is_final = t_data.get("isFinal", False)

        with self.lock:
            if is_final:
                if text:
                    self.finalized_segments.append(text)
                self.current_partial = ""
            else:
                self.current_partial = text

    def finalize_current(self):
        """Moves any pending partial transcription into finalized_segments so subsequent
        stream segments (e.g. across speech pauses) cannot overwrite it.
        """
        with self.lock:
            if self.current_partial:
                # Prevent duplicate appending if the segment was already finalized
                if not self.finalized_segments or self.finalized_segments[-1] != self.current_partial:
                    self.finalized_segments.append(self.current_partial)
                self.current_partial = ""

    def get_full_text(self) -> str:
        with self.lock:
            all_parts = list(self.finalized_segments)
            if self.current_partial:
                all_parts.append(self.current_partial)

            # Join parts cleanly with spaces
            full = " ".join(part for part in all_parts if part).strip()
            return full

import hashlib
import logging
import queue
import threading
import time

import httpx

RATE_LIMIT_SECONDS = 60


class TelegramHandler(logging.Handler):
    def __init__(
        self,
        bot_token: str,
        chat_id: int,
        level=logging.ERROR,
        queue_size: int = 200,
        dedup_window_sec: int = 300,
        rate_limit_per_min: int = 30,
    ) -> None:
        super().__init__(level)
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.msg_queue: queue.Queue[str] = queue.Queue(maxsize=queue_size)
        self._stop = False
        self._dedup = {}
        self._rate = []
        self.dedup_window = dedup_window_sec
        self.rate_limit = rate_limit_per_min
        thread = threading.Thread(target=self._worker, daemon=True)
        thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
            text_hash = hashlib.md5(text.encode()).hexdigest()
            now = time.time()
            ts = self._dedup.get(text_hash, 0)
            if now - ts < self.dedup_window:
                return
            try:
                self.msg_queue.put_nowait(text)
                self._dedup[text_hash] = now
            except queue.Full:
                pass
        except Exception:
            self.handleError(record)

    def _worker(self):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        with httpx.Client(timeout=5.0) as client:
            while not self._stop:
                try:
                    text = self.msg_queue.get()
                    now = time.time()
                    self._rate = [t for t in self._rate if now - t < RATE_LIMIT_SECONDS]
                    if len(self._rate) >= self.rate_limit:
                        time.sleep(1.0)
                        self.msg_queue.put(text)
                        continue
                    payload = {
                        "chat_id": self.chat_id,
                        "text": text,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    }
                    client.post(url, json=payload)
                    self._rate.append(now)
                except Exception:
                    time.sleep(1.0)

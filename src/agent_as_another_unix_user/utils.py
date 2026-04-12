import sys
from click import style
from threading import Thread
from time import sleep
from contextlib import contextmanager


class Spinner:
    def __init__(self, message: str) -> None:
        self.message = message
        self._started = False
        self._frames = "|/-\\"
        self._index = 0
        self._width = len(self.message) + 1

    def tick(self) -> None:
        frame = self._frames[self._index % len(self._frames)]
        self._index += 1
        if not self._started:
            self._started = True
        sys.stderr.write(f"\r{self.message} {style(frame, fg='green')}")
        sys.stderr.flush()

    def stop(self) -> None:
        if not self._started:
            return
        sys.stderr.write("\r" + (" " * self._width) + "\r")
        sys.stderr.flush()

    @contextmanager
    @classmethod
    def in_thread(cls, message: str):
        spinner = Spinner(message)
        needed = True

        def _refresh():
            nonlocal needed
            while needed:
                sleep(0.1)
                spinner.tick()
            spinner.stop()

        thread = Thread(target=_refresh)
        thread.start()
        try:
            yield
        finally:
            needed = False
            thread.join()

#!/usr/bin/env python3

import sys
import argparse
import socket
import serial
import select
import io
import os
import time
import datetime
import pathlib
import re
from functools import partial

# Local dependency to py_linenoise.
# If this import fails, it might be because the submodule has not been cloned.
# It can be fixed with:
# git submodule init --update
from py_linenoise import linenoise

APP_DATA_DIR = os.path.join(pathlib.Path.home(), ".jterm")

EXIT_RESULT_OK = 0
EXIT_RESULT_ARGUMENT_ERROR = 1
EXIT_RESULT_INIT_CONNECT_TIMEOUT = 2
EXIT_RESULT_LATER_CONNECT_TIMEOUT = 3
EXIT_RESULT_INTERRUPTED = 4


class LineBuf:
    def __init__(self):
        self.buf = io.BytesIO()
        self.eol = b"\n"

    def write(self, data):
        self.buf.write(data)

    def has_line(self):
        return self.buf.getvalue().find(self.eol) >= 0

    def readline(self):
        data = self.buf.getvalue()
        pos = data.find(self.eol)
        if pos == -1:
            return None
        line = data[0:pos]
        self.buf = io.BytesIO(data[pos + len(self.eol) :])
        self.buf.seek(0, os.SEEK_END)
        return line


class Interface:
    def read(self, size):
        raise NotImplementedError()

    def write(self, data):
        raise NotImplementedError()

    def fileno(self):
        raise NotImplementedError()

    def try_open(self, timeout):
        first_try = True
        start = time.monotonic()
        while True:
            try:
                self.open()
                print(f"Connected to '{self}'")
                return True
            except OSError as e:
                if first_try:
                    print(f"Failed to open '{self}': {e}.")
                    retry_time = f"for {timeout} s" if timeout else "forever"
                    print(f"Will retry continuously {retry_time}. Stop with ctrl+c.")
                    first_try = False
                if timeout and time.monotonic() >= start + timeout:
                    print(f"Giving up opening '{self}': {e}")
                    return False
            time.sleep(0.1)

    def open(self):
        raise NotImplementedError()

    def close(self):
        raise NotImplementedError()


class SerialInterface(Interface):
    def __init__(self, port, baudrate):
        self._dev = None
        self._port = port
        self._baudrate = baudrate
        self._timeout = 0

    def read(self, size):
        try:
            return self._dev.read(size)
        except serial.SerialException:
            return b""

    def write(self, data):
        self._dev.write(data)

    def fileno(self):
        return self._dev.fileno()

    def open(self):
        self._dev = serial.Serial(
            port=self._port, baudrate=self._baudrate, timeout=self._timeout
        )

    def close(self):
        if self._dev:
            self._dev.close()
            self._dev = None

    def __str__(self):
        return f"{self._port}"


class SocketInterface(Interface):
    def __init__(self, host, port, timeout):
        self._dev = None
        self._host = host
        self._port = port
        self._timeout = timeout

    def read(self, size):
        if self._dev is None:
            raise ValueError("Not open")
        try:
            return self._dev.recv(size)
        except BlockingIOError:
            return b""
        except socket.timeout:
            return b""

    def write(self, data):
        if self._dev is None:
            raise ValueError("Not open")
        self._dev.send(data)

    def fileno(self):
        return self._dev.fileno() if self._dev else None

    def open(self):
        self._dev = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._dev.settimeout(self._timeout)
        self._dev.connect((self._host, self._port))

    def close(self):
        if self._dev is None:
            return
        self._dev.close()
        self._dev = None

    def __str__(self):
        return f"{self._host}:{self._port}"


def completion(ln, s):
    """return a list of line completions"""
    if not s:
        return None
    history = ln.history_list()
    matches = [h for h in reversed(history) if h.startswith(s)]
    return matches


def hints(ln, s):
    """return the hints for this command"""
    if not s:
        return None
    history = ln.history_list()
    for h in reversed(history):
        if h.startswith(s):
            return (h[len(s) :], 35, False)
    return None


def eol_option_as_bytestring(eol_option):
    eol_alternatives = {
        "lf": "\n",
        "crlf": "\r\n",
        "cr": "\r",
    }
    return eol_alternatives[eol_option]


def process_cmd(cmdline, interface, log_file, args):
    if log_file is not None:
        log_file.write(cmdline + "\n")
    if args.delay_between_bytes > 0:
        for b in cmdline.encode():
            interface.write(bytes([b]))
            time.sleep(args.delay_between_bytes)
    else:
        interface.write(cmdline.encode())
    time.sleep(args.delay_before_eol)
    eol = eol_option_as_bytestring(args.eol)
    for b in eol.encode():
        interface.write(bytes([b]))
        time.sleep(args.delay_between_bytes)
    time.sleep(args.delay_after_eol)


def remove_ansi_escape_codes(s):
    # From:
    # https://stackoverflow.com/questions/14693701/how-can-i-remove-the-ansi-escape-sequences-from-a-string-in-python
    # 7-bit C1 ANSI sequences
    ansi_escape = re.compile(
        r"""
        \x1B  # ESC
        (?:   # 7-bit C1 Fe (except CSI)
            [@-Z\\-_]
        |     # or [ for CSI, followed by a control sequence
            \[
            [0-?]*  # Parameter bytes
            [ -/]*  # Intermediate bytes
            [@-~]   # Final byte
        )
    """,
        re.VERBOSE,
    )
    return ansi_escape.sub("", s)


def replace_non_printable(s, accept=""):
    s_out = []
    for c in s:
        if c in accept:
            s_out.append(c)
        elif c == "\\":
            s_out.append("\\\\")
        elif c.isprintable():
            s_out.append(c)
        else:
            s_out.append(f"\\x{ord(c):02x}")
    return "".join(s_out)


def print_line(line, log_file):
    time = datetime.datetime.now().isoformat(timespec="milliseconds")
    s = line.decode(errors="replace")
    s = s.replace("\r", "")
    ESC = "\x1b"
    s_print = replace_non_printable(s, ESC)
    if ESC in s:
        s_print = s_print + ESC + "[0m"
    print(f"{time} {s_print}")
    if log_file is not None:
        s_log = replace_non_printable(remove_ansi_escape_codes(s))
        log_file.write(f"{time} {s_log}\n")


def process_input(ln, line_state, interface, log_file, prompt, args):
    res = ln.edit_feed(line_state)
    if res == linenoise.EditResult.MORE:
        pass
    elif res == linenoise.EditResult.EOF_OR_ERROR:
        raise EOFError()
    elif res == linenoise.EditResult.ESCAPE:
        ln.edit_stop(line_state)
        line_state = ln.edit_start(prompt)
    elif res == linenoise.EditResult.ENTER:
        ln.edit_stop(line_state)
        cmdline = str(line_state)
        ln.history_add(cmdline)
        process_cmd(cmdline, interface, log_file, args)
        line_state = ln.edit_start(prompt)
    else:
        raise ValueError(res)
    return line_state


def process_interface(line_state, line_buf, interface, log_file):
    data = interface.read(1024)
    if not data:
        return False
    line_buf.write(data)
    if line_buf.has_line():
        line_state.hide()
        while (line := line_buf.readline()) is not None:
            print_line(line, log_file)
        line_state.show()
    return True


class JtermLineNoise(linenoise.linenoise):
    def history_next(self, ls):
        """Show next history item"""
        base = str(ls)[0 : ls.pos]
        i = ls.history_idx - 1
        while i > 0:
            if self.history_get(i).startswith(base):
                break
            i -= 1
        else:
            # No matching entry found in history.
            return
        if ls.history_idx == 0:
            # update the current history entry with the line buffer
            self.history_set(ls.history_idx, str(ls))
        ls.history_idx = i
        ls.edit_set(self.history_get(ls.history_idx), ls.pos)

    def history_prev(self, ls):
        """Show previous history item"""
        base = str(ls)[0 : ls.pos]
        i = ls.history_idx + 1
        while i < len(self.history):
            if self.history_get(i).startswith(base):
                break
            i += 1
        else:
            # No matching entry found in history.
            return
        # update the current history entry with the line buffer
        if ls.history_idx == 0:
            # update the current history entry with the line buffer
            self.history_set(ls.history_idx, str(ls))
        ls.history_idx = i
        ls.edit_set(self.history_get(ls.history_idx), ls.pos)


def interactive(interface, log_file, args):
    ln = JtermLineNoise()
    line_buf = LineBuf()

    # Set the completion callback. This will be called
    # every time the user uses the <tab> key.
    ln.set_completion_callback(partial(completion, ln))
    ln.set_hints_callback(partial(hints, ln))

    # Load history from file.
    ln.history_set_maxlen(args.history_max)
    ln.history_load(args.history)
    print(f"History file: '{args.history}', length: {len(ln.history_list())}")

    exit_code = EXIT_RESULT_OK

    prompt = "> "
    line_state = ln.edit_start(prompt)
    try:
        while True:
            fds = (line_state.ifd, interface.fileno())
            (rd, _, _) = select.select(fds, (), ())
            if line_state.ifd in rd:
                # Data is available on stdin (or EOF).
                line_state = process_input(
                    ln, line_state, interface, log_file, prompt, args
                )
            if interface.fileno() in rd:
                # Data is available (or EOF) at our interface.
                got_data = process_interface(line_state, line_buf, interface, log_file)
                if not got_data:
                    line_state.hide()
                    print(f"Interface '{interface}' closed. Will retry to open it.")
                    interface.close()
                    if not interface.try_open(args.later_connect_timeout):
                        print(
                            "Timeout before successful connect. Increase '--later_connect_timeout' or set it to '0' to retry forever."
                        )
                        exit_code = EXIT_RESULT_LATER_CONNECT_TIMEOUT
                        break
                    line_state.show()
    except (EOFError, KeyboardInterrupt):
        ln.edit_stop(line_state)
        print("EOF or interrupted. Exiting.")
    except:
        # Unexpected error. Please report it.
        ln.edit_stop(line_state)
        print("Unexpected error:", sys.exc_info()[0])
        raise
    print(f"Log available in '{args.log}'")
    os.makedirs(os.path.dirname(args.history), exist_ok=True)
    ln.history_save(args.history)
    return exit_code


def split_host_and_port(host_colon_port):
    host, port = host_colon_port.split(":")
    return host if host else "localhost", int(port)


def parse_args():
    parser = argparse.ArgumentParser(
        description="jterm",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--serial",
        metavar="DEVICE",
        help="The serial device to connect to, for example '/dev/ttyUSB0'",
    )
    parser.add_argument(
        "--socket",
        metavar="HOST:PORT",
        help="Host and port to connect to, separated by ':'",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=115200,
        help="Baudrate of the serial device",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1,
        help="Timeout (s) when connecting through a socket interface",
    )
    parser.add_argument(
        "--delay_between_bytes",
        metavar="DELAY",
        type=float,
        default=0,
        help="Delay (s) between each byte sent to an interface",
    )
    parser.add_argument(
        "--delay_before_eol",
        metavar="DELAY",
        type=float,
        default=0,
        help="Delay (s) after a command has been written and before the end of line sequence is to be written to the interface",
    )
    parser.add_argument(
        "--delay_after_eol",
        metavar="DELAY",
        type=float,
        default=0.01,
        help="Delay (s) after end of line sequence",
    )
    default_log_file_name = (
        datetime.datetime.now().isoformat(sep="_", timespec="seconds").replace(":", "")
        + ".log"
    )
    parser.add_argument(
        "--log",
        help="Log file to append to",
        default=os.path.join(APP_DATA_DIR, "logs/" + default_log_file_name),
    )
    parser.add_argument(
        "--history",
        help="File to load and save command history to",
        default=os.path.join(APP_DATA_DIR, "history.txt"),
    )
    parser.add_argument(
        "--history_max",
        help="Maximum number of commands to keep in history",
        type=int,
        default=500,
    )
    parser.add_argument(
        "--eol",
        default="crlf",
        choices=("crlf", "cr", "lf"),
        help="End of line sequence for lines written to interface",
    )
    parser.add_argument(
        "--first_connect_timeout",
        metavar="TIMEOUT",
        type=float,
        default=0,
        help="Time to wait (s) before giving up first connect attempt (0 to retry forever)",
    )
    parser.add_argument(
        "--later_connect_timeout",
        metavar="TIMEOUT",
        type=float,
        default=0,
        help="Time to wait (s) before giving up later connect attempts (0 to retry forever)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.socket and args.serial:
        print(
            "Please specify exactly one interface: '--socket' or '--serial', not both."
        )
        sys.exit(EXIT_RESULT_ARGUMENT_ERROR)
    if args.socket:
        try:
            host, port = split_host_and_port(args.socket)
        except ValueError:
            print(
                (
                    "Please specify target to connect to as '<host>:<port>', "
                    "or just ':<port>' for localhost."
                )
            )
            sys.exit(EXIT_RESULT_ARGUMENT_ERROR)
        interface = SocketInterface(host, port, args.timeout)
    elif args.serial:
        interface = SerialInterface(args.serial, args.baudrate)
    else:
        print(
            "Please specify an interface to connect through: '--socket' or '--serial'."
        )
        print("Example:")
        prog = sys.argv[0]
        print(f"  {prog} --socket localhost:1234")
        print(f"  {prog} --serial /dev/ttyUSB0")
        sys.exit(EXIT_RESULT_ARGUMENT_ERROR)

    try:
        if not interface.try_open(args.first_connect_timeout):
            print(
                "Timeout before first connect. Increase '--first_connect_timeout' or set it to '0' to retry forever."
            )
            sys.exit(EXIT_RESULT_INIT_CONNECT_TIMEOUT)
    except KeyboardInterrupt:
        print("\nCtrl+c detected. Exiting.")
        sys.exit(EXIT_RESULT_INTERRUPTED)

    log_file = None
    if args.log:
        print(f"Log: '{args.log}'")
        os.makedirs(os.path.dirname(args.log), exist_ok=True)
        log_file = open(args.log, "a")
    res = interactive(interface, log_file, args)
    sys.exit(res)


if __name__ == "__main__":
    main()

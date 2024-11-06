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
import xmodem

from py_linenoise import linenoise

APP_DATA_DIR = os.path.join(pathlib.Path.home(), ".jterm")
HISTORY_FILE = os.path.join(APP_DATA_DIR, "history.txt")


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

    def set_timeout(self, timeout_s):
        raise NotImplementedError()

    def open(self):
        raise NotImplementedError()

    def try_open(self, first_try=True):
        while True:
            try:
                self.open()
                break
            except serial.SerialException as e:
                if first_try:
                    print(
                        f"Failed to open {self}: {e}. Will retry until stopped with ctrl+c."
                    )
                    first_try = False
                time.sleep(0.1)


class SerialInterface(Interface):
    def __init__(self, port, baudrate):
        self._port = port
        self._baudrate = baudrate

    def read(self, size):
        return self.dev.read(size)

    def write(self, data):
        self.dev.write(data)

    def fileno(self):
        return self.dev.fileno()

    def open(self):
        self.dev = serial.Serial(port=self._port, baudrate=self._baudrate, timeout=0)

    def close(self):
        self.dev.close()
        self.dev = None

    def set_timeout(self, timeout_s):
        self.dev.timeout = timeout_s

    def __str__(self):
        return f"{self._port}"


class SocketInterface(Interface):
    def __init__(self, host, port):
        self._host = host
        self._port = port

    def read(self, size):
        try:
            return self.dev.recv(size)
        except BlockingIOError:
            return b""
        except socket.timeout:
            return b""

    def write(self, data):
        self.dev.send(data)

    def fileno(self):
        return self.dev.fileno()

    def open(self):
        self.dev = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.dev.connect((self._host, self._port))
        self.dev.settimeout(0)

    def set_timeout(self, timeout_s):
        self.dev.settimeout(timeout_s)

    def __str__(self):
        return f"{self._host}:{self._port}"


def completion(s):
    """return a list of line completions"""
    if len(s) >= 1 and s[0] == "h":
        return ("hello", "hello there")
    return None


def hints(s):
    """return the hints for this command"""
    if s == "hello":
        # string, color, bold
        return (" World", 35, False)
    return None


def process_cmd(cmdline, interface, log_file, args):
    eol = "\n"
    if args.eol == "cr":
        eol = b"\r"
    elif args.eol == "crlf":
        eol = b"\r\n"
    elif args.eol == "lf":
        pass
    else:
        raise ValueError(f"Bad argument for --eof '{args.eof}'")

    if log_file is not None:
        log_file.write(cmdline + "\n")

    if args.delay_between_byte_ms > 0:
        for c in cmdline.encode:
            interface.write(c)
            time.sleep(args.delay_between_byte_ms / 1000)
    else:
        interface.write(cmdline.encode())
    time.sleep(args.delay_before_eol_ms / 1000)
    interface.write(eol)


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
    elif res == linenoise.EditResult.HOTKEY:
        ln.edit_stop(line_state)
        cmd = str(line_state)
        print("Hotkey detected, command line: '%s'" % cmd)
        interface.write(linenoise._KEY_CTRL_L.encode())
        line_state = ln.edit_start(prompt, cmd)
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


def send_file(interface, filename):
    def getc(size, timeout=1):
        interface.set_timeout(timeout)
        return interface.read(size)

    def putc(data, _timeout=0):
        interface.write(data)

    with open(filename, "rb") as f:
        print(f"Sending file '{filename}' to '{interface} using XMODEM protocol")
        modem = xmodem.XMODEM(getc, putc)
        modem.send(f, retry=16, timeout=10, quiet=False, callback=None)


def interactive(interface, log_file, args):
    ln = linenoise.linenoise()
    line_buf = LineBuf()

    # Set the completion callback. This will be called
    # every time the user uses the <tab> key.
    ln.set_completion_callback(completion)
    ln.set_hints_callback(hints)

    # Load history from file. The history file is a plain text file
    # where entries are separated by newlines.
    ln.history_load(HISTORY_FILE)

    ln.set_hotkey(linenoise._KEY_CTRL_L)

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
                # Data is available at our interface (or EOF).
                got_data = process_interface(line_state, line_buf, interface, log_file)
                if not got_data:
                    print(
                        f"Interface {interface} closed. Will retry to open it. Press ctrl+c to stop."
                    )
                    interface.close()
                    interface.try_open()

    except EOFError:
        ln.edit_stop(line_state)
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        ln.history_save(HISTORY_FILE)
    except:
        ln.edit_stop(line_state)
        raise


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
        help="The serial device to connect to",
    )
    parser.add_argument(
        "--socket",
        metavar="HOST:PORT",
        help="Host and port to connect to, separated by `:`",
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=115200,
        help="Baudrate of the serial device",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=1,
        help="Timeout in seconds",
    )
    parser.add_argument(
        "--delay_before_eol_ms",
        type=int,
        default=100,
        help="Delay between command and end of line sequence (ms)",
    )
    parser.add_argument(
        "--delay_between_byte_ms",
        type=int,
        default=0,
        help="Delay between each byte sent (ms)",
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
        "--sendfile",
        help="Send a file by using the XMODEM protocol",
    )
    parser.add_argument(
        "--eol",
        default="crlf",
        help="End of line character(s), options: lf (default), crlf, cr",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if args.socket and args.serial:
        print("Please specify either --socket or --serial, not both.")
        sys.exit(1)
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
            sys.exit(1)
        interface = SocketInterface(host, port)
    elif args.serial:
        interface = SerialInterface(args.serial, args.baudrate)
    else:
        print("Please specify either --socket or --serial.")
        sys.exit(1)

    if args.eol != "crlf" and args.eol != "cr" and args.eol != "lf":
        print("Please choose --eol to be 'crlf', 'cr' or 'lf'")
        sys.exit(1)

    interface.try_open(True)

    if args.sendfile:
        send_file(interface, args.sendfile)
        return

    log_file = None
    if args.log:
        print(f"Appending to log file: '{args.log}'")
        os.makedirs(os.path.dirname(args.log), exist_ok=True)
        log_file = open(args.log, "a")
    interactive(interface, log_file, args)


if __name__ == "__main__":
    main()

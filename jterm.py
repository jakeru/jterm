#!/usr/bin/env python3

import sys
import argparse
import socket
import serial
import select
import io
import os
import datetime

from py_linenoise import linenoise

HISTORY_FILE = os.path.join(os.environ.get("HOME", "/home"), ".jterm", "history.txt")


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


class SerialInterface(Interface):
    def __init__(self, port, baudrate):
        self._port = port
        self._baudrate = baudrate
        self.open()

    def read(self, size):
        return self.dev.read(size)

    def write(self, data):
        self.dev.write(data)

    def fileno(self):
        return self.dev.fileno()

    def open(self):
        self.dev = serial.Serial(port=self._port, baudrate=self._baudrate, timeout=0)


class SocketInterface(Interface):
    def __init__(self, host, port):
        self.dev = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.dev.connect((host, port))
        self.dev.settimeout(0)

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


def process_cmd(cmdline, interface):
    interface.write(cmdline.encode() + b"\r\n")


def print_line(line):
    time = datetime.datetime.now().isoformat(timespec="milliseconds")
    s = line.decode(errors="replace")
    # Remove one trailing '\r' if it is present.
    if s and s[-1] == "\r":
        s = s[:-1]
    print(f"{time} {s}")


def process_input(ln, line_state, interface, prompt):
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
        process_cmd(cmdline, interface)
        line_state = ln.edit_start(prompt)
    elif res == linenoise.EditResult.HOTKEY:
        ln.edit_stop(line_state)
        cmd = str(line_state)
        print("Hotkey detected, command line: '%s'" % cmd)
        line_state = ln.edit_start(prompt, cmd)
    else:
        raise ValueError(res)
    return line_state


def process_interface(line_state, line_buf, interface):
    while data := interface.read(1024):
        line_buf.write(data)
    if line_buf.has_line():
        line_state.hide()
        while (line := line_buf.readline()) is not None:
            print_line(line)
        line_state.show()


def interactive(interface):
    ln = linenoise.linenoise()
    line_buf = LineBuf()

    # Set the completion callback. This will be called
    # every time the user uses the <tab> key.
    ln.set_completion_callback(completion)
    ln.set_hints_callback(hints)

    # Load history from file. The history file is a plain text file
    # where entries are separated by newlines.
    ln.history_load(HISTORY_FILE)

    prompt = "> "
    line_state = ln.edit_start(prompt)
    try:
        while True:
            fds = (line_state.ifd, interface.fileno())
            (rd, _, _) = select.select(fds, (), ())
            if line_state.ifd in rd:
                # Data is available on stdin (or EOF).
                line_state = process_input(ln, line_state, interface, prompt)
            if interface.fileno() in rd:
                # Data is available at our interface (or EOF).
                process_interface(line_state, line_buf, interface)
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
    interactive(interface)


if __name__ == "__main__":
    main()

# jterm - Serial terminal written in Python

This tool is useful when communicating with devices over a serial port or a TCP
socket.

By using (a slightly modified version of)
[py_linenoise](https://github.com/deadsy/py_linenoise) this tool allows the user
to type commands and receive data at the same time.

It has been used by me (Jakob Ruhe) for quite some time but in April 2025
published version 1.0.0 of this tool.

## Features

- If an interface cannot be opened, this tool will continuously retry to open it
  up until a configurable amount of maximum time (or forever, which is the default).
- User may type commands while receiving data at the same time.
- Basic command line editing support (backspace, arrow keys).
- Command history is kept between sessions.
- Type a few letters and press Ctrl+p (or up arrow) to search in history.
- Data sent and received are always logged with date and time in a new log file
  for each session.
- Support for slow devices by introducing a configurable delay between each byte
  sent.
- ANSI Colors are kept on stdout but not included in the log file.

## Future improvements

- ✓ DONE: Profile support to separate history files and logs.
  Added in June 2025.
- ✓ DONE: Navigation using ctrl+arrow to jump between words.
  Added in June 2025.
- ✓ DONE: Multiline prompt.
  Added in June 2025.
- Configuration files.
- Wait for prompt before sending next command.
- Word completion.

## Installation

Clone this repository and include the submodule (by specifying `--recursive`).
The destination path (`~/jterm` in this example) can be whatever you prefer.

Like this:

``` sh
git clone https://github.com/jakeru/jterm.git --recursive ~/jterm
```

If you get an error that says that module `pylinenoise` cannot be found, it is
very likely that the repository was not cloned with submodules included.

In order to fetch the submodules, go to the destination path and execute the
following:

``` sh
git update --init
```

## Install other dependencies

This application requires [pyserial](https://pypi.org/project/pyserial/).

Pyserial is a common package and you may therefore consider to install it system
wide.

Otherwise you can create a Python Virtual Environment for this application and
install the package in it.

In the following sections, both methods are explained. Choose one of them.

### Install `pyserial` system wide

In Ubuntu, this is the preferred way to install `pyserial` system wide:

``` sh
apt install python3-serial
```

Use your favorite search engine to find out about how to install the package
system wide on other platforms.

### Install `pyserial` using a Python virtual environment

``` sh
cd ~/jterm
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

## Run

If the dependencies are installed system wide you may run the application like
this:

``` sh
~/jterm/jterm.py
```

If you have created a Python Virtual Environment for it, you can either first
activate it, or run `jterm` like this:

``` sh
~/jterm/env/bin/python3 ~/jterm/jterm.py
```

## Usage

To connect to a serial port with a specific baudrate (default is 115200 bps):

``` sh
jterm --serial /dev/ttyACM0 --baudrate 9600
```

To connect to a TCP socket:

``` sh
jterm socket :1234
jterm socket example.com:1234
```

Logs are saved into `~/.jterm/logs` using the current date and time as filename.
The parameter `--log` can be specified to choose a different filename for the
log. For more options, supply the `--help` argument when launching `jterm`.

# RUDP

Reliable UDP file transfer with selective-repeat acknowledgements, CRC32
validation, and optional SACK blocks.

## Run

Create a virtual environment and install the pinned test dependency:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
```

Start the receiver, then send a file:

```bash
python apps/recv_file.py 9000 received.bin
python apps/send_file.py 127.0.0.1 9000 input.bin
```

Run the tests with:

```bash
python -m pytest
```
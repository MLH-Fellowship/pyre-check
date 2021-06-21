# Overview
This is a trivially vulnerable Flask application, meant as a playground for
testing Pysa

# Setup
1. Setup a virtual environment (if you haven't already):
```bash
python -m venv ../.env
source ../.env/bin/activate
```

2. Install pyre-check and other dependencies
```bash
pip install -r requirements.txt
```

4. Create the .pyre_configuration file:
```bash
pyre init
```

5. Run the analysis:
```bash
pyre analyze --no-verify
````

Instead of steps 2-4, you may run `. setup.sh` which does the same but generates a slighlly
different `.pyre_configuration`.
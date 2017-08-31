import os
import sys

# Workaround to https://github.com/pallets/flask/issues/1246
# Convenient for testing directly out of the source directory
# (can also be worked around with a virtualenv and pip install -e)
os.environ['PYTHONPATH'] = os.getcwd()

from sage_patchbot.server.serve import main

main(sys.argv)

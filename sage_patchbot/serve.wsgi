import sys
from os.path import join, abspath, dirname
sys.path.insert(0, abspath(join('..', dirname(__file__))))
from sage_patchbot.server import app as application

import codecs
import os
import re

from setuptools import setup

here = os.path.abspath(os.path.dirname(__file__))

def read(*parts):
    return codecs.open(os.path.join(here, *parts), 'r').read()

def find_version(*file_paths):
    version_file = read(*file_paths)
    version_match = re.search(r"^__version__ = ['\"]([^'\"]*)['\"]",
                              version_file, re.M)
    if version_match:
        return version_match.group(1)
    raise RuntimeError("Unable to find version string.")

setup(name='sage-patchbot',
      version=find_version('sage_patchbot', 'version.py'),
      description='bot for automatic test of sagemath trac tickets',
      url='https://github.com/robertwb/sage-patchbot',
      author='Robert Bradshaw',
      license='GPL',
      entry_points={
          'console_scripts': ['patchbot=sage_patchbot.patchbot:main']},
      packages=['sage_patchbot'],
      package_data={'sage_patchbot': ['static/*.css',
                                      'images/*.png','images/*.svg',
                                      'templates/*.html',
                                      'templates/*.svg',
                                      'templates/*.txt',
                                      'sage_patchbot/serve.wsgi']},
      zip_safe=False)
